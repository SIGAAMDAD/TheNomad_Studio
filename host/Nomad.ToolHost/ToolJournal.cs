using System.Text.Json;
using Nomad.Studio.ToolHost.Contracts;

namespace Nomad.Studio.ToolHost;

public sealed record JournalEntry(string RequestHash, ToolReceipt Receipt, int? Pid, string? ProcessStartUtc);

public sealed class ToolJournal
{
    private readonly string root;
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNameCaseInsensitive = false };
    public ToolJournal(string absoluteRoot)
    {
        root = Path.GetFullPath(absoluteRoot);
        if (!Path.IsPathFullyQualified(absoluteRoot)) throw new InvalidOperationException("Journal path must be absolute");
        Directory.CreateDirectory(root);
        Config.SecureDirectory(root);
    }

    public string PathFor(string projectId, string operationId) =>
        Path.Combine(root, Protocol.Sha256(projectId + "\n" + operationId) + ".json");

    public string LogPath(string projectId, string operationId, string stream) =>
        Path.Combine(root, Protocol.Sha256(projectId + "\n" + operationId) + "." + stream + ".log");

    public JournalEntry? Read(string projectId, string operationId)
    {
        string path = PathFor(projectId, operationId);
        if (!File.Exists(path)) return null;
        var data = JsonSerializer.Deserialize<JournalEntry>(File.ReadAllBytes(path), JsonOptions)
            ?? throw new InvalidDataException("Invalid journal record");
        if (data.Receipt.ProjectId != projectId || data.Receipt.OperationId != operationId)
            throw new InvalidDataException("Journal operation mismatch");
        return data;
    }

    public void Persist(JournalEntry entry)
    {
        string target = PathFor(entry.Receipt.ProjectId, entry.Receipt.OperationId);
        string temp = target + "." + Guid.NewGuid().ToString("N") + ".tmp";
        byte[] content = JsonSerializer.SerializeToUtf8Bytes(entry, JsonOptions);
        try
        {
            using (var file = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                file.Write(content);
                file.Flush(flushToDisk: true);
            }
            File.Move(temp, target, overwrite: true);
        }
        finally { if (File.Exists(temp)) File.Delete(temp); }
    }

    // After a process crash, no committed terminal receipt is reconstructible from a PID.
    // Mark it unknown, preserving the prior run identity and time for reconciliation.
    public void ReconcileInterrupted()
    {
        foreach (string path in Directory.EnumerateFiles(root, "*.json"))
        {
            JournalEntry data = JsonSerializer.Deserialize<JournalEntry>(File.ReadAllBytes(path), JsonOptions)
                ?? throw new InvalidDataException("Corrupt operation journal: " + path);
            if (data.Receipt.State is "running" or "queued")
            {
                var uncertain = data.Receipt with
                {
                    State = "unknown", EndedAt = DateTimeOffset.UtcNow.ToString("O"),
                    Reason = "Interrupted host; actual child outcome requires trusted external reconciliation"
                };
                Persist(data with { Receipt = uncertain });
            }
        }
    }
}
