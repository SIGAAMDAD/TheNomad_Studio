using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Nomad.Studio.ToolHost.Contracts;

namespace Nomad.Studio.ToolHost;

public static class Protocol
{
    public const string Version = "1.0.0";
    public const int MaxFrameBytes = 65536;
    private static readonly UTF8Encoding Utf8 = new(false, true);
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNameCaseInsensitive = false };
    private static readonly Dictionary<string, Dictionary<string, JsonValueKind>> Fields = new(StringComparer.Ordinal)
    {
        ["handshake"] = new() { ["kind"] = JsonValueKind.String, ["protocol_version"] = JsonValueKind.String,
            ["host_nonce"] = JsonValueKind.String, ["supported_isolation"] = JsonValueKind.Array },
        ["run"] = new() { ["kind"] = JsonValueKind.String, ["protocol_version"] = JsonValueKind.String,
            ["operation_id"] = JsonValueKind.String, ["request_id"] = JsonValueKind.String,
            ["project_id"] = JsonValueKind.String, ["task_id"] = JsonValueKind.String,
            ["session_id"] = JsonValueKind.String, ["lease_generation"] = JsonValueKind.Number,
            ["source_revision_id"] = JsonValueKind.String, ["canon_snapshot_id"] = JsonValueKind.String,
            ["package_digest"] = JsonValueKind.String, ["entrypoint_id"] = JsonValueKind.String,
            ["grant_digest"] = JsonValueKind.String, ["workspace_id"] = JsonValueKind.String,
            ["args"] = JsonValueKind.Object, ["deadline_ms"] = JsonValueKind.Number,
            ["input_artifact_digests"] = JsonValueKind.Array },
        ["observe"] = new() { ["kind"] = JsonValueKind.String, ["protocol_version"] = JsonValueKind.String,
            ["operation_id"] = JsonValueKind.String, ["project_id"] = JsonValueKind.String },
        ["cancel"] = new() { ["kind"] = JsonValueKind.String, ["protocol_version"] = JsonValueKind.String,
            ["operation_id"] = JsonValueKind.String, ["project_id"] = JsonValueKind.String,
            ["lease_generation"] = JsonValueKind.Number, ["request_id"] = JsonValueKind.String }
    };

    public static async Task<string?> ReadLineAsync(Stream stream, int maxBytes, CancellationToken cancellationToken)
    {
        using var storage = new MemoryStream();
        byte[] one = new byte[1];
        while (true)
        {
            int count = await stream.ReadAsync(one.AsMemory(), cancellationToken);
            if (count == 0)
            {
                if (storage.Length == 0) return null;
                throw new InvalidDataException("Truncated NDJSON frame");
            }
            if (one[0] == (byte)'\n') break;
            if (storage.Length == maxBytes) throw new InvalidDataException("NDJSON frame too long");
            storage.WriteByte(one[0]);
        }
        byte[] bytes = storage.ToArray();
        if (bytes.Length > 0 && bytes[^1] == (byte)'\r') Array.Resize(ref bytes, bytes.Length - 1);
        if (bytes.Length == 0) throw new InvalidDataException("Empty NDJSON frame");
        try { return Utf8.GetString(bytes); }
        catch (DecoderFallbackException e) { throw new InvalidDataException("Invalid frame UTF-8", e); }
    }

    public static object Parse(string line)
    {
        if (Utf8.GetByteCount(line) > MaxFrameBytes) throw new InvalidDataException("Frame too large");
        using var document = JsonDocument.Parse(line, new JsonDocumentOptions
        { AllowTrailingCommas = false, CommentHandling = JsonCommentHandling.Disallow, MaxDepth = 24 });
        JsonElement root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("kind", out JsonElement kindProp)
            || kindProp.ValueKind != JsonValueKind.String) throw new InvalidDataException("Missing frame kind");
        string kind = kindProp.GetString()!;
        if (!Fields.TryGetValue(kind, out var required)) throw new InvalidDataException("Unrecognized inbound frame kind");
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (JsonProperty field in root.EnumerateObject())
        {
            if (!seen.Add(field.Name) || !required.TryGetValue(field.Name, out var type))
                throw new InvalidDataException("Unknown/duplicate property: " + field.Name);
            if (field.Value.ValueKind != type) throw new InvalidDataException("Bad property type: " + field.Name);
            if (type == JsonValueKind.String && field.Value.GetString()!.Length == 0)
                throw new InvalidDataException("Empty string property: " + field.Name);
        }
        if (seen.Count != required.Count) throw new InvalidDataException("Missing required frame properties");
        if (root.GetProperty("protocol_version").GetString() != Version)
            throw new InvalidDataException("Incompatible protocol version");
        foreach (string name in kind switch
            {
                "run" => new[] { "lease_generation", "deadline_ms" },
                "cancel" => new[] { "lease_generation" },
                _ => Array.Empty<string>()
            })
        {
            if (!root.GetProperty(name).TryGetInt32(out int i) || i < 1)
                throw new InvalidDataException("Invalid positive integer: " + name);
        }
        if (kind == "handshake") ValidateStringArray(root.GetProperty("supported_isolation"), false);
        if (kind == "run") ValidateStringArray(root.GetProperty("input_artifact_digests"), true);
        Type type = kind switch
        {
            "handshake" => typeof(Handshake), "run" => typeof(ToolRun),
            "observe" => typeof(ToolObserve), "cancel" => typeof(ToolCancel),
            _ => throw new InvalidDataException("Unsupported input")
        };
        return JsonSerializer.Deserialize(line, type, JsonOptions)
            ?? throw new InvalidDataException("Unable to deserialize NDJSON frame");
    }

    private static void ValidateStringArray(JsonElement array, bool nonemptyItems)
    {
        foreach (var item in array.EnumerateArray())
            if (item.ValueKind != JsonValueKind.String || (nonemptyItems && item.GetString()!.Length == 0))
                throw new InvalidDataException("Invalid array element");
    }

    public static string Serialize(object value) => JsonSerializer.Serialize(value, value.GetType(), new JsonSerializerOptions
    {
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull
    });

    public static string Sha256(string text) => Convert.ToHexString(SHA256.HashData(Utf8.GetBytes(text))).ToLowerInvariant();

    public static ToolReceipt Receipt(string operationId, string projectId, int generation, string hostId,
        string state, string? reason = null, int? exitCode = null, string? start = null, string? end = null,
        string[]? logs = null, string[]? artifacts = null) =>
        new("receipt", Version, operationId, projectId, state, generation, hostId, exitCode, start, end,
            logs ?? Array.Empty<string>(), artifacts ?? Array.Empty<string>(), reason);
}
