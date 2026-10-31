using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Nomad.Studio.ToolHost;

// Registry is prepared by the authenticated local broker, from package/grant/workspace
// information owned by the other Studio services. GitHub relay text is never a registry.
// A trusted broker passes the expected SHA-256 of these exact registry bytes at launch.
public sealed record Registry(
    [property: JsonPropertyName("version")] int Version,
    [property: JsonPropertyName("packages")] Package[] Packages,
    [property: JsonPropertyName("grants")] Grant[] Grants,
    [property: JsonPropertyName("workspaces")] Workspace[] Workspaces,
    [property: JsonPropertyName("operations")] AuthorizedOperation[] Operations);

public sealed record Package(
    [property: JsonPropertyName("digest")] string Digest,
    [property: JsonPropertyName("install_root")] string InstallRoot,
    [property: JsonPropertyName("manifest_file")] string ManifestFile,
    [property: JsonPropertyName("entrypoints")] EntryPoint[] Entrypoints);

public sealed record EntryPoint(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("relative_executable")] string RelativeExecutable,
    [property: JsonPropertyName("sha256")] string Sha256,
    [property: JsonPropertyName("isolation")] string Isolation,
    [property: JsonPropertyName("fixed_args")] string[] FixedArgs,
    [property: JsonPropertyName("arguments")] ArgumentRule[] Arguments,
    [property: JsonPropertyName("environment")] Dictionary<string, string> Environment,
    [property: JsonPropertyName("output_files")] string[] OutputFiles,
    [property: JsonPropertyName("max_output_bytes")] long MaxOutputBytes,
    [property: JsonPropertyName("max_memory_bytes")] long MaxMemoryBytes,
    [property: JsonPropertyName("max_processes")] int MaxProcesses,
    [property: JsonPropertyName("max_deadline_ms")] int MaxDeadlineMs);

public sealed record ArgumentRule(
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("allowed_values")] string[] AllowedValues);

public sealed record Grant(
    [property: JsonPropertyName("digest")] string Digest,
    [property: JsonPropertyName("project_id")] string ProjectId,
    [property: JsonPropertyName("package_digest")] string PackageDigest,
    [property: JsonPropertyName("workspace_ids")] string[] WorkspaceIds,
    [property: JsonPropertyName("entrypoint_ids")] string[] EntrypointIds,
    [property: JsonPropertyName("max_deadline_ms")] int MaxDeadlineMs);

public sealed record Workspace(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("project_id")] string ProjectId,
    [property: JsonPropertyName("root")] string Root);

public sealed record AuthorizedOperation(
    [property: JsonPropertyName("operation_id")] string OperationId,
    [property: JsonPropertyName("request_id")] string RequestId,
    [property: JsonPropertyName("project_id")] string ProjectId,
    [property: JsonPropertyName("task_id")] string TaskId,
    [property: JsonPropertyName("worker_id")] string WorkerId,
    [property: JsonPropertyName("shift_id")] string ShiftId,
    [property: JsonPropertyName("lease_generation")] int LeaseGeneration,
    [property: JsonPropertyName("source_revision_id")] string SourceRevisionId,
    [property: JsonPropertyName("canon_snapshot_id")] string CanonSnapshotId,
    [property: JsonPropertyName("package_digest")] string PackageDigest,
    [property: JsonPropertyName("entrypoint_id")] string EntrypointId,
    [property: JsonPropertyName("grant_digest")] string GrantDigest,
    [property: JsonPropertyName("workspace_id")] string WorkspaceId,
    [property: JsonPropertyName("input_artifact_digests")] string[] InputArtifactDigests);

public sealed record ResolvedRun(string Executable, string InstallRoot, string Workspace, EntryPoint Entrypoint, string[] Args);

public static class Config
{
    private static readonly JsonSerializerOptions StrictJson = new()
    {
        PropertyNameCaseInsensitive = false,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow
    };

    public static Registry Load(string path, string expectedSha)
    {
        if (!Path.IsPathFullyQualified(path) || !IsHash(expectedSha))
            throw new InvalidOperationException("Registry requires absolute path and broker-pinned SHA-256");
        byte[] bytes = File.ReadAllBytes(path);
        if (!EqualHash(SHA256.HashData(bytes), expectedSha))
            throw new InvalidOperationException("Registry digest mismatch");
        Registry data = JsonSerializer.Deserialize<Registry>(bytes, StrictJson)
            ?? throw new InvalidOperationException("Registry is empty");
        if (data.Version != 1 || data.Packages is null || data.Grants is null || data.Workspaces is null || data.Operations is null)
            throw new InvalidOperationException("Unsupported or incomplete registry");
        if (data.Operations.GroupBy(x => x.OperationId, StringComparer.Ordinal).Any(x => x.Count() != 1))
            throw new InvalidOperationException("Duplicate operation IDs");
        if (data.Workspaces.GroupBy(x => (x.ProjectId, x.Id)).Any(x => x.Count() != 1))
            throw new InvalidOperationException("Duplicate workspace IDs");
        if (data.Packages.GroupBy(x => x.Digest, StringComparer.Ordinal).Any(x => x.Count() != 1))
            throw new InvalidOperationException("Duplicate package digest");
        if (data.Grants.GroupBy(x => (x.ProjectId, x.Digest)).Any(x => x.Count() != 1))
            throw new InvalidOperationException("Duplicate project grant");
        return data;
    }

    public static ResolvedRun Authorize(Registry registry, Contracts.ToolRun run)
    {
        AuthorizedOperation auth = ExactlyOne(registry.Operations.Where(x => x.OperationId == run.OperationId), "operation");
        if (auth.RequestId != run.RequestId || auth.ProjectId != run.ProjectId || auth.TaskId != run.TaskId ||
            auth.WorkerId != run.WorkerId || auth.ShiftId != run.ShiftId || auth.LeaseGeneration != run.LeaseGeneration ||
            auth.SourceRevisionId != run.SourceRevisionId || auth.CanonSnapshotId != run.CanonSnapshotId ||
            auth.PackageDigest != run.PackageDigest || auth.EntrypointId != run.EntrypointId ||
            auth.GrantDigest != run.GrantDigest || auth.WorkspaceId != run.WorkspaceId ||
            !auth.InputArtifactDigests.SequenceEqual(run.InputArtifactDigests, StringComparer.Ordinal))
            throw new InvalidOperationException("Unapproved operation context, task lease, revision or inputs");
        Package package = ExactlyOne(registry.Packages.Where(x => x.Digest == run.PackageDigest), "package");
        Grant grant = ExactlyOne(registry.Grants.Where(x => x.ProjectId == run.ProjectId && x.Digest == run.GrantDigest), "grant");
        Workspace workspace = ExactlyOne(registry.Workspaces.Where(x => x.ProjectId == run.ProjectId && x.Id == run.WorkspaceId), "workspace");
        EntryPoint entry = ExactlyOne(package.Entrypoints.Where(x => x.Id == run.EntrypointId), "entrypoint");
        if (grant.PackageDigest != package.Digest ||
            !grant.WorkspaceIds.Contains(run.WorkspaceId, StringComparer.Ordinal) ||
            !grant.EntrypointIds.Contains(run.EntrypointId, StringComparer.Ordinal))
            throw new InvalidOperationException("Grant does not authorize package/workspace/entrypoint");
        if (run.DeadlineMs > grant.MaxDeadlineMs || run.DeadlineMs > entry.MaxDeadlineMs)
            throw new InvalidOperationException("Deadline exceeds installed grant limits");
        if (!IsHash(package.Digest) || !IsHash(grant.Digest) || !IsHash(entry.Sha256))
            throw new InvalidOperationException("Unpinned package, grant or executable");
        string install = SecureDirectory(package.InstallRoot);
        ValidateInstalledPackage(package, install, entry);
        string workspaceRoot = SecureDirectory(workspace.Root);
        string executable = ResolveExecutable(install, entry.RelativeExecutable);
        if (!EqualHash(HashFile(executable), entry.Sha256))
            throw new InvalidOperationException("Executable SHA-256 mismatch");
        if (entry.MaxOutputBytes < 1 || entry.MaxMemoryBytes < 1024 * 1024 || entry.MaxProcesses < 1 ||
            entry.MaxDeadlineMs < 1 || entry.FixedArgs is null || entry.Arguments is null || entry.Environment is null || entry.OutputFiles is null)
            throw new InvalidOperationException("Invalid entrypoint resource limits");
        if (entry.Environment.Any(x => x.Key.Length == 0 || x.Key.Contains('=') || x.Key.Contains('\0') || x.Value.Contains('\0')))
            throw new InvalidOperationException("Invalid environment allowlist");
        var args = new List<string>(entry.FixedArgs);
        if (entry.FixedArgs.Any(x => x is null || x.Contains('\0')))
            throw new InvalidOperationException("Invalid pinned argument");
        if (run.Args.ValueKind != JsonValueKind.Object)
            throw new InvalidOperationException("args must be an object");
        var supplied = run.Args.EnumerateObject().ToArray();
        if (supplied.Length != entry.Arguments.Length || supplied.Select(x => x.Name).Distinct(StringComparer.Ordinal).Count() != supplied.Length)
            throw new InvalidOperationException("Argument names do not match entrypoint declaration");
        foreach (ArgumentRule rule in entry.Arguments)
        {
            var item = supplied.SingleOrDefault(x => x.NameEquals(rule.Name));
            if (item.Name is null || item.Value.ValueKind != JsonValueKind.String)
                throw new InvalidOperationException("Required string argument missing: " + rule.Name);
            string value = item.Value.GetString()!;
            if (value.Contains('\0') || value.Length > 4096)
                throw new InvalidOperationException("Invalid argument value");
            if (rule.Kind != "enum" || rule.AllowedValues is null || !rule.AllowedValues.Contains(value, StringComparer.Ordinal))
                throw new InvalidOperationException("Argument value outside manifest-allowed enumeration");
            args.Add(value);
        }
        return new ResolvedRun(executable, install, workspaceRoot, entry, args.ToArray());
    }

    private static T ExactlyOne<T>(IEnumerable<T> candidates, string label)
    {
        T[] result = candidates.Take(2).ToArray();
        return result.Length == 1 ? result[0] : throw new InvalidOperationException("Missing or ambiguous " + label);
    }

    public static bool IsHash(string? hash) => hash is { Length: 64 } && hash.All(Uri.IsHexDigit);

    public static byte[] HashFile(string path)
    {
        using var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        return SHA256.HashData(file);
    }

    private static bool EqualHash(byte[] bytes, string hex)
    {
        try { return CryptographicOperations.FixedTimeEquals(bytes, Convert.FromHexString(hex)); }
        catch (FormatException) { return false; }
    }

    // Reject symlink/reparse components, including the directory's ancestors. A linked
    // workspace can otherwise escape even if the last component itself is not a link.
    public static string SecureDirectory(string path)
    {
        if (!Path.IsPathFullyQualified(path)) throw new InvalidOperationException("Directory path must be absolute");
        string full = Path.GetFullPath(path);
        if (!Directory.Exists(full)) throw new InvalidOperationException("Directory missing");
        CheckNoLinks(full);
        return full;
    }


    private static void ValidateInstalledPackage(Package package, string install, EntryPoint selected)
    {
        string manifestPath = ResolveExistingFile(install, package.ManifestFile);
        if (!EqualHash(HashFile(manifestPath), package.Digest))
            throw new InvalidOperationException("Installed package manifest digest mismatch");
        using var document = JsonDocument.Parse(File.ReadAllBytes(manifestPath));
        JsonElement root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object || root.GetProperty("manifest_version").GetString() != "1" ||
            root.GetProperty("entrypoints").ValueKind != JsonValueKind.Array ||
            root.GetProperty("resources").ValueKind != JsonValueKind.Array)
            throw new InvalidOperationException("Installed package manifest incompatible");
        var matched = root.GetProperty("entrypoints").EnumerateArray()
            .Where(x => x.GetProperty("id").GetString() == selected.Id).Take(2).ToArray();
        if (matched.Length != 1) throw new InvalidOperationException("Entrypoint not uniquely declared in installed manifest");
        JsonElement spec = matched[0];
        if (spec.GetProperty("executable_path").GetString() != selected.RelativeExecutable ||
            spec.GetProperty("executable_digest").GetString() != selected.Sha256 ||
            spec.GetProperty("required_isolation").GetString() != selected.Isolation)
            throw new InvalidOperationException("Entrypoint differs from installed package manifest");
        foreach (string prefix in new[] { "args_schema", "output_schema" })
        {
            string path = ResolveExistingFile(install, spec.GetProperty(prefix + "_path").GetString()!);
            string sha = spec.GetProperty(prefix + "_digest").GetString()!;
            if (!IsHash(sha) || !EqualHash(HashFile(path), sha))
                throw new InvalidOperationException("Installed " + prefix + " digest mismatch");
        }
        foreach (JsonElement resource in root.GetProperty("resources").EnumerateArray())
        {
            string path = ResolveExistingFile(install, resource.GetProperty("path").GetString()!);
            string sha = resource.GetProperty("sha256").GetString()!;
            long bytes = resource.GetProperty("byte_size").GetInt64();
            if (bytes < 0 || new FileInfo(path).Length != bytes || !IsHash(sha) || !EqualHash(HashFile(path), sha))
                throw new InvalidOperationException("Installed package resource mismatch");
        }
    }

    public static string ResolveExistingFile(string installRoot, string relative)
    {
        if (string.IsNullOrWhiteSpace(relative) || Path.IsPathRooted(relative))
            throw new InvalidOperationException("Package path must be relative");
        string full = Path.GetFullPath(Path.Combine(installRoot, relative));
        string diff = Path.GetRelativePath(installRoot, full);
        if (diff == "." || diff.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar).Any(x => x == ".."))
            throw new InvalidOperationException("Package resource escaped install root");
        CheckNoLinks(full);
        if (!File.Exists(full)) throw new InvalidOperationException("Package file missing");
        return full;
    }

    public static string ResolveExecutable(string installRoot, string relative)
    {
        if (string.IsNullOrWhiteSpace(relative) || Path.IsPathRooted(relative))
            throw new InvalidOperationException("Executable must be package-relative");
        string full = Path.GetFullPath(Path.Combine(installRoot, relative));
        if (!Path.GetRelativePath(installRoot, full).Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
            .All(part => part != "..") || full == installRoot)
            throw new InvalidOperationException("Executable escaped package root");
        CheckNoLinks(full);
        if (!File.Exists(full)) throw new InvalidOperationException("Executable missing");
        return full;
    }

    public static void AssertNoLinks(string full) => CheckNoLinks(full);

    private static void CheckNoLinks(string full)
    {
        string? current = Path.GetPathRoot(full);
        if (current is null) throw new InvalidOperationException("Invalid filesystem root");
        foreach (string component in full[current.Length..].Split(Path.DirectorySeparatorChar, StringSplitOptions.RemoveEmptyEntries))
        {
            current = Path.Combine(current, component);
            FileAttributes attrs = File.GetAttributes(current);
            if ((attrs & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException("Symlink/reparse point forbidden: " + current);
        }
    }
}
