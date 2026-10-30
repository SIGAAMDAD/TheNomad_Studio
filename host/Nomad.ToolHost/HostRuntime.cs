using System.Collections.Concurrent;
using System.Diagnostics;
using System.Security.Cryptography;
using Nomad.Studio.ToolHost.Contracts;

namespace Nomad.Studio.ToolHost;

public sealed class HostRuntime
{
    private sealed record Running(string RequestHash, ToolReceipt Receipt, CancellationTokenSource Cancel);
    private readonly Registry registry;
    private readonly ToolJournal journal;
    private readonly Func<ResolvedRun, int, ProcessStartInfo> sandboxFactory;
    private readonly ConcurrentDictionary<string, Running> active = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, object> perOperation = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, Task> runningTasks = new(StringComparer.Ordinal);
    private readonly string identity;
    private readonly Func<ToolReceipt, Task> emit;

    public HostRuntime(Registry registry, ToolJournal journal, string identity,
        Func<ToolReceipt, Task> emit, Func<ResolvedRun, int, ProcessStartInfo>? sandboxFactory = null)
    {
        this.registry = registry;
        this.journal = journal;
        this.identity = identity;
        this.emit = emit;
        this.sandboxFactory = sandboxFactory ?? Sandbox.Create;
    }

    private static string Key(string project, string operation) => project + "\n" + operation;

    public async Task RunAsync(ToolRun run, string rawFrame)
    {
        string hash = Protocol.Sha256(rawFrame);
        string key = Key(run.ProjectId, run.OperationId);
        string? reject = null;
        ResolvedRun? resolved = null;
        try { resolved = Config.Authorize(registry, run); }
        catch (Exception e)
        { reject = "unauthorized: " + e.GetType().Name + ": " + e.Message; }
        if (reject is not null)
        {
            await emit(Protocol.Receipt(run.OperationId, run.ProjectId, run.LeaseGeneration, identity, "failed", reject));
            return;
        }
        object gate = perOperation.GetOrAdd(key, _ => new object());
        ToolReceipt? replay = null;
        CancellationTokenSource? cancel = null;
        lock (gate)
        {
            JournalEntry? existing = journal.Read(run.ProjectId, run.OperationId);
            if (existing is not null)
            {
                replay = existing.RequestHash == hash ? existing.Receipt :
                    Protocol.Receipt(run.OperationId, run.ProjectId, run.LeaseGeneration, identity,
                        "failed", "idempotency_conflict: operation_id reused with changed request body");
            }
            else
            {
                cancel = new CancellationTokenSource();
                var queued = Protocol.Receipt(run.OperationId, run.ProjectId, run.LeaseGeneration, identity, "queued");
                journal.Persist(new JournalEntry(hash, queued, null, null));
                if (!active.TryAdd(key, new Running(hash, queued, cancel)))
                    throw new InvalidOperationException("Active operation key already occupied");
            }
        }
        if (replay is not null) { await emit(replay); return; }
        await emit(Protocol.Receipt(run.OperationId, run.ProjectId, run.LeaseGeneration, identity, "queued"));
        // Async continuation leaves the stdin reader free to accept observe/cancel requests.
        Task runner = Task.Run(async () => await ExecuteAsync(run, resolved!, hash, key, cancel!));
        runningTasks[key] = runner;
        _ = runner.ContinueWith(completed => runningTasks.TryRemove(key, out var removed), TaskScheduler.Default);
    }

    public async Task ShutdownAsync()
    {
        foreach (Running item in active.Values)
        {
            try { item.Cancel.Cancel(); }
            catch (ObjectDisposedException) { }
        }
        Task[] tasks = runningTasks.Values.ToArray();
        if (tasks.Length > 0) await Task.WhenAll(tasks);
    }

    public Task ObserveAsync(ToolObserve observe)
    {
        if (!IsAuthorizedOperation(observe.ProjectId, observe.OperationId))
            return emit(Protocol.Receipt(observe.OperationId, observe.ProjectId, 1, identity,
                "failed", "operation_not_found"));
        ToolReceipt result;
        JournalEntry? state = journal.Read(observe.ProjectId, observe.OperationId);
        result = state?.Receipt ?? Protocol.Receipt(observe.OperationId, observe.ProjectId, 1, identity,
            "failed", "operation_not_found");
        return emit(result);
    }

    public async Task CancelAsync(ToolCancel command)
    {
        ToolReceipt response;
        if (!IsAuthorizedOperation(command.ProjectId, command.OperationId))
        {
            await emit(Protocol.Receipt(command.OperationId, command.ProjectId, command.LeaseGeneration, identity,
                "failed", "operation_not_found"));
            return;
        }
        string key = Key(command.ProjectId, command.OperationId);
        JournalEntry? state = journal.Read(command.ProjectId, command.OperationId);
        if (state is null)
            response = Protocol.Receipt(command.OperationId, command.ProjectId, command.LeaseGeneration, identity,
                "failed", "operation_not_found");
        else if (state.Receipt.LeaseGeneration != command.LeaseGeneration)
            response = Protocol.Receipt(command.OperationId, command.ProjectId, command.LeaseGeneration, identity,
                "failed", "stale_lease_generation");
        else if (state.Receipt.State is "completed" or "failed" or "canceled" or "unknown")
            response = state.Receipt;
        else if (active.TryGetValue(key, out Running? process))
        {
            try { process.Cancel.Cancel(); }
            catch (ObjectDisposedException) { }
            response = state.Receipt with { Reason = "cancel_requested" };
        }
        else
            response = state.Receipt with { Reason = "cancel_raced_with_termination" };
        await emit(response);
    }

    private bool IsAuthorizedOperation(string project, string operation) =>
        registry.Operations.Any(x => x.ProjectId == project && x.OperationId == operation);

    private async Task ExecuteAsync(ToolRun run, ResolvedRun resolved, string hash, string key, CancellationTokenSource cancel)
    {
        var started = DateTimeOffset.UtcNow.ToString("O");
        var receipt = Protocol.Receipt(run.OperationId, run.ProjectId, run.LeaseGeneration, identity, "running", start: started);
        Process? process = null;
        bool outputExceeded = false;
        bool startedProcess = false;
        try
        {
            // Sandbox factory MUST fail closed; tests may inject a fake only for isolated
            // state-machine tests, never in production Program.Main.
            ProcessStartInfo start = sandboxFactory(resolved, run.DeadlineMs);
            cancel.CancelAfter(run.DeadlineMs);
            process = new Process { StartInfo = start, EnableRaisingEvents = true };
            {
                if (cancel.IsCancellationRequested) throw new OperationCanceledException();
                if (!process.Start()) throw new InvalidOperationException("Unable to start sandboxed tool");
                startedProcess = true;
                journal.Persist(new JournalEntry(hash, receipt, process.Id, process.StartTime.ToUniversalTime().ToString("O")));
                active.AddOrUpdate(key, new Running(hash, receipt, cancel), (_, _) => new Running(hash, receipt, cancel));
                await emit(receipt);
                using var stdoutFile = new FileStream(journal.LogPath(run.ProjectId, run.OperationId, "stdout"),
                    FileMode.CreateNew, FileAccess.Write, FileShare.Read);
                using var stderrFile = new FileStream(journal.LogPath(run.ProjectId, run.OperationId, "stderr"),
                    FileMode.CreateNew, FileAccess.Write, FileShare.Read);
                Task stdout = PumpBoundedAsync(process.StandardOutput.BaseStream, stdoutFile,
                    resolved.Entrypoint.MaxOutputBytes, cancel, () => outputExceeded = true);
                Task stderr = PumpBoundedAsync(process.StandardError.BaseStream, stderrFile,
                    resolved.Entrypoint.MaxOutputBytes, cancel, () => outputExceeded = true);
                try
                {
                    await Task.WhenAll(process.WaitForExitAsync(cancel.Token), stdout, stderr);
                }
                catch
                {
                    if (!process.HasExited) process.Kill(entireProcessTree: true);
                    await process.WaitForExitAsync();
                    throw;
                }
                stdoutFile.Flush(flushToDisk: true); stderrFile.Flush(flushToDisk: true);
                string[] logs = { HashFile(journal.LogPath(run.ProjectId, run.OperationId, "stdout")),
                    HashFile(journal.LogPath(run.ProjectId, run.OperationId, "stderr")) };
                string[] outputs = process.ExitCode == 0 ? HashOutputs(resolved) : Array.Empty<string>();
                receipt = receipt with { State = process.ExitCode == 0 ? "completed" : "failed",
                    ExitCode = process.ExitCode, EndedAt = DateTimeOffset.UtcNow.ToString("O"),
                    LogDigests = logs, OutputArtifactDigests = outputs,
                    Reason = process.ExitCode == 0 ? null : "process_exit_nonzero" };
            }
        }
        catch (OperationCanceledException)
        {
            receipt = receipt with { State = outputExceeded ? "failed" : "canceled",
                Reason = outputExceeded ? "log_output_limit_exceeded" : "canceled_or_deadline_expired",
                EndedAt = DateTimeOffset.UtcNow.ToString("O") };
        }
        catch (Exception e)
        {
            receipt = receipt with { State = "failed", Reason = "execution_failure: " + e.Message,
                EndedAt = DateTimeOffset.UtcNow.ToString("O") };
        }
        finally
        {
            // Unknown is preferable to falsely claiming termination on a kill failure.
            if (startedProcess && process is not null && !process.HasExited)
            {
                try { process.Kill(entireProcessTree: true); await process.WaitForExitAsync(); }
                catch
                {
                    receipt = receipt with { State = "unknown", Reason = "unable_to_confirm_child_termination" };
                }
            }
            process?.Dispose();
            if (receipt.LogDigests.Length == 0)
            {
                string[] existing = new[] { "stdout", "stderr" }
                    .Select(stream => journal.LogPath(run.ProjectId, run.OperationId, stream))
                    .Where(File.Exists).Select(HashFile).ToArray();
                if (existing.Length > 0) receipt = receipt with { LogDigests = existing };
            }
            journal.Persist(new JournalEntry(hash, receipt, null, null));
            active.TryRemove(key, out _);
            cancel.Dispose();
            await emit(receipt);
        }
    }

    private static async Task PumpBoundedAsync(Stream input, FileStream output, long limit,
        CancellationTokenSource cancellation, Action onOverflow)
    {
        byte[] buffer = new byte[8192];
        long bytes = 0;
        while (true)
        {
            int count = await input.ReadAsync(buffer.AsMemory(), cancellation.Token);
            if (count == 0) break;
            bytes += count;
            if (bytes > limit)
            {
                onOverflow();
                cancellation.Cancel();
                throw new InvalidOperationException("Process log exceeded configured byte cap");
            }
            await output.WriteAsync(buffer.AsMemory(0, count), cancellation.Token);
        }
    }

    private static string HashFile(string path) => Convert.ToHexString(Config.HashFile(path)).ToLowerInvariant();

    private static string[] HashOutputs(ResolvedRun resolved)
    {
        var result = new List<string>();
        foreach (string file in resolved.Entrypoint.OutputFiles)
        {
            if (string.IsNullOrWhiteSpace(file) || Path.IsPathRooted(file))
                throw new InvalidOperationException("Output artifact path must be workspace-relative");
            string full = Path.GetFullPath(Path.Combine(resolved.Workspace, file));
            string relative = Path.GetRelativePath(resolved.Workspace, full);
            if (relative == "." || relative.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar).Any(x => x == ".."))
                throw new InvalidOperationException("Output artifact escaped workspace");
            if (!File.Exists(full))
                throw new InvalidOperationException("Required output artifact absent or linked");
            Config.AssertNoLinks(full);
            result.Add(HashFile(full));
        }
        return result.ToArray();
    }
}
