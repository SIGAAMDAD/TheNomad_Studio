using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using Nomad.Studio.ToolHost;
using Nomad.Studio.ToolHost.Contracts;

static class Checks
{
    private static int completed;
    private static void Assert(bool condition, string label)
    {
        if (!condition) throw new InvalidOperationException("FAIL: " + label);
        Console.WriteLine("PASS " + label);
        completed++;
    }

    private static void Reject(Action test, string label)
    {
        bool rejected = false;
        try { test(); }
        catch (Exception e) when (e is InvalidOperationException or InvalidDataException or JsonException)
        { rejected = true; }
        Assert(rejected, label);
    }

    private static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    private static async Task<ToolReceipt> Terminal(ToolJournal journal, string project, string operation)
    {
        for (int i = 0; i < 200; i++)
        {
            ToolReceipt? latest = journal.Read(project, operation)?.Receipt;
            if (latest is not null && latest.State is "completed" or "failed" or "canceled" or "unknown")
                return latest;
            await Task.Delay(20);
        }
        throw new InvalidOperationException("Timed out awaiting terminal operation receipt");
    }

    public static async Task Main()
    {
        string temporary = Path.Combine(Path.GetTempPath(), "nomad-wg-h-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(temporary);
        string workspace = Path.Combine(temporary, "workspace");
        Directory.CreateDirectory(workspace);
        string registryPath = Path.Combine(temporary, "registry.json");
        string install = Path.Combine(temporary, "installed-package");
        Directory.CreateDirectory(install);
        File.Copy("/usr/bin/true", Path.Combine(install, "true"));
        File.WriteAllText(Path.Combine(install, "args.json"), "{\"type\":\"object\",\"additionalProperties\":false}");
        File.WriteAllText(Path.Combine(install, "output.json"), "{\"type\":\"object\"}");
        string binarySha = Hash(File.ReadAllBytes(Path.Combine(install, "true")));
        var entry = new EntryPoint("unit-test", "true", binarySha,
            Sandbox.LinuxCapability, Array.Empty<string>(), Array.Empty<ArgumentRule>(),
            new Dictionary<string, string>(), Array.Empty<string>(), 100000, 64 * 1024 * 1024, 16, 3000);
        byte[] packageManifestBytes = JsonSerializer.SerializeToUtf8Bytes(new
        {
            manifest_version = "1", package_id = "tests.tool", package_version = "1.0.0", kind = "tool",
            capabilities = Array.Empty<string>(), dependencies = Array.Empty<string>(),
            entrypoints = new[] { new
            {
                id = "unit-test", executable_digest = binarySha,
                args_schema_digest = Hash(File.ReadAllBytes(Path.Combine(install, "args.json"))),
                output_schema_digest = Hash(File.ReadAllBytes(Path.Combine(install, "output.json"))),
                required_isolation = Sandbox.LinuxCapability,
                executable_path = "true", args_schema_path = "args.json", output_schema_path = "output.json"
            } },
            resources = Array.Empty<object>()
        });
        File.WriteAllBytes(Path.Combine(install, "package.json"), packageManifestBytes);
        string digest = Hash(packageManifestBytes);
        const string grant = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        var auth = new AuthorizedOperation("op-a", "req-a", "project-a", "task-a", "worker-a", "shift-a", 2,
            "revision-a", "canon-a", digest, "unit-test", grant, "work-a", Array.Empty<string>());
        var registry = new Registry(1,
            new[] { new Package(digest, install, "package.json", new[] { entry }) },
            new[] { new Grant(grant, "project-a", digest, new[] { "work-a" }, new[] { "unit-test" }, 3000) },
            new[] { new Workspace("work-a", "project-a", workspace) },
            new[] { auth });
        File.WriteAllBytes(registryPath, JsonSerializer.SerializeToUtf8Bytes(registry));
        string pinnedHash = Hash(File.ReadAllBytes(registryPath));
        var loaded = Config.Load(registryPath, pinnedHash);
        Assert(loaded.Packages.Length == 1, "registry pinned SHA-256 loads");
        Reject(() => Config.Load(registryPath, new string('f', 64)), "wrong registry SHA denied");
        File.AppendAllText(registryPath, " ");
        Reject(() => Config.Load(registryPath, pinnedHash), "modified registry bytes denied");
        File.WriteAllBytes(registryPath, JsonSerializer.SerializeToUtf8Bytes(registry));

        using JsonDocument args = JsonDocument.Parse("{}");
        var run = new ToolRun("run", Protocol.Version, auth.OperationId, auth.RequestId, auth.ProjectId,
            auth.TaskId, auth.WorkerId, auth.ShiftId, auth.LeaseGeneration, auth.SourceRevisionId, auth.CanonSnapshotId,
            auth.PackageDigest, auth.EntrypointId, auth.GrantDigest, auth.WorkspaceId, args.RootElement.Clone(),
            2000, auth.InputArtifactDigests);
        string frame = Protocol.Serialize(run);
        Assert(Protocol.Parse(frame) is ToolRun, "frozen run frame parses");
        Reject(() => Protocol.Parse(frame.Replace("\"kind\":", "\"unexpected\":true,\"kind\":")), "unknown frame key denied");
        Reject(() => Protocol.Parse(frame.Replace("\"kind\":", "\"kind\":\"run\",\"kind\":")), "duplicate frame key denied");
        Reject(() => Protocol.Parse(frame.Replace("\"1.0.0\"", "\"2.0.0\"")), "incompatible protocol rejected");
        Assert(Config.Authorize(loaded, run).Entrypoint.Id == "unit-test", "pinned installed manifest and grant authorized");
        if (!Sandbox.Available().Contains(Sandbox.LinuxCapability, StringComparer.Ordinal))
            Reject(() => Sandbox.Create(Config.Authorize(loaded, run), run.DeadlineMs),
                "unavailable sandbox fails closed");
        File.AppendAllText(Path.Combine(install, "args.json"), " ");
        Reject(() => Config.Authorize(loaded, run), "tampered installed schema bytes denied");
        File.WriteAllText(Path.Combine(install, "args.json"), "{\"type\":\"object\",\"additionalProperties\":false}");
        Reject(() => Config.Authorize(loaded, run with { ProjectId = "other" }), "cross-project request denied");
        Reject(() => Config.Authorize(loaded, run with { LeaseGeneration = 1 }), "stale task lease denied");
        Reject(() => Config.Authorize(loaded, run with { WorkerId = "another" }), "wrong persistent worker denied");
        Reject(() => Config.Authorize(loaded, run with { ShiftId = "another" }), "wrong executing shift denied");
        Reject(() => Config.Authorize(loaded, run with { SourceRevisionId = "other" }), "unpinned source revision denied");
        Reject(() => Config.Authorize(loaded, run with { CanonSnapshotId = "other" }), "unpinned Canon denied");
        Reject(() => Config.Authorize(loaded, run with { GrantDigest = digest }), "wrong grant denied");
        Reject(() => Config.Authorize(loaded, run with { DeadlineMs = 4000 }), "oversized deadline denied");
        using JsonDocument malicious = JsonDocument.Parse("{\"command\":\"rm -rf /\"}");
        Reject(() => Config.Authorize(loaded, run with { Args = malicious.RootElement.Clone() }), "untyped shell argument rejected");
        string linked = Path.Combine(workspace, "link");
        File.CreateSymbolicLink(linked, "/usr/bin/true");
        Reject(() => Config.ResolveExecutable(workspace, "link"), "symlink escape blocked");
        Reject(() => Config.ResolveExecutable("/usr/bin", "../../etc/passwd"), "package path escape blocked");

        var journal = new ToolJournal(Path.Combine(temporary, "journal"));
        var seen = new List<ToolReceipt>();
        var gate = new object();
        Task Send(ToolReceipt receipt) { lock (gate) seen.Add(receipt); return Task.CompletedTask; }
        // Isolated process-lifecycle test only: a synthetic ProcessStartInfo is injected
        // into this UNIT harness. It is NOT evidence that a real sandbox executes.
        var runtime = new HostRuntime(loaded, journal, "test-host", Send, (_, _) => new ProcessStartInfo("/usr/bin/true")
        {
            UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true,
            WorkingDirectory = workspace
        });
        await runtime.RunAsync(run, frame);
        ToolReceipt finished = await Terminal(journal, "project-a", "op-a");
        Assert(finished.State == "completed" && finished.ExitCode == 0, "unit-injected process finished with receipt: " + finished.State + " / " + finished.Reason);
        Assert(finished.LogDigests.Length == 2, "stdout and stderr sha256 receipts recorded");
        int count = seen.Count;
        await runtime.RunAsync(run, frame);
        Assert(seen.Count == count + 1, "identical request returns journal replay");
        await runtime.RunAsync(run with { DeadlineMs = 1999 }, Protocol.Serialize(run with { DeadlineMs = 1999 }));
        Assert(seen[^1].Reason?.StartsWith("idempotency_conflict") == true, "same operation changed body rejected");
        await runtime.CancelAsync(new ToolCancel("cancel", Protocol.Version, "op-a", "project-a", 1, "cancel-a"));
        Assert(seen[^1].Reason == "stale_lease_generation", "stale cancel cannot affect operation");
        await runtime.ObserveAsync(new ToolObserve("observe", Protocol.Version, "op-a", "other-project"));
        Assert(seen[^1].Reason == "operation_not_found", "observe is project-scoped");

        var interrupted = Protocol.Receipt("op-interrupted", "project-a", 2, "old-host", "running");
        journal.Persist(new JournalEntry("oldhash", interrupted, 999999, DateTimeOffset.UtcNow.ToString("O")));
        journal.ReconcileInterrupted();
        Assert(journal.Read("project-a", "op-interrupted")!.Receipt.State == "unknown",
            "restart marks interrupted run unknown, never completed");
        await runtime.ShutdownAsync();
        Console.WriteLine($"PASS completed {completed} isolated checks; OS isolation, .NET clean build, and integration remain separate gates");
        Directory.Delete(temporary, recursive: true);
    }
}
