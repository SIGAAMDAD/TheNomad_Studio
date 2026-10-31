using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Nomad.Studio.ToolHost;
using Nomad.Studio.ToolHost.Contracts;

internal static class AdditionalHostCoverage
{
    private static void Check(bool value, string label)
    {
        if (!value) throw new InvalidOperationException("FAIL " + label);
        Console.WriteLine("PASS " + label);
    }

    private static void Reject(Action action, string label)
    {
        bool failed = false;
        try { action(); }
        catch (Exception e) when (e is InvalidOperationException or InvalidDataException or
                                  JsonException or ArgumentException or FileNotFoundException)
        { failed = true; }
        Check(failed, label);
    }

    private static string Hash(byte[] body) => Convert.ToHexString(SHA256.HashData(body)).ToLowerInvariant();

    private static async Task RejectRead(byte[] data, int max, string label)
    {
        using var stream = new MemoryStream(data);
        bool rejected = false;
        try { await Protocol.ReadLineAsync(stream, max, CancellationToken.None); }
        catch (InvalidDataException) { rejected = true; }
        Check(rejected, label);
    }

    private static async Task<int> RunChild(string registryPath, string digest, string journalPath,
                                            bool correctChallenge, bool sendInvalidFrame)
    {
        var exe = typeof(Nomad.Studio.ToolHost.Program).Assembly.Location;
        var start = new ProcessStartInfo("dotnet")
        {
            UseShellExecute = false, RedirectStandardInput = true,
            RedirectStandardOutput = true, RedirectStandardError = true,
            CreateNoWindow = true,
        };
        foreach (var part in new[] { exe, "--registry", registryPath,
                                    "--registry-sha256", digest, "--journal", journalPath })
            start.ArgumentList.Add(part);
        using Process child = Process.Start(start) ?? throw new InvalidOperationException("Unable to start host protocol subprocess");
        try
        {
            string? frame = await child.StandardOutput.ReadLineAsync();
            Check(frame is not null, "host process emitted initial challenge");
            var challenge = Protocol.Parse(frame!) as Handshake;
            Check(challenge is not null && challenge.HostNonce.Length >= 32, "host challenge structure verified");
            var response = challenge! with { HostNonce = correctChallenge ? challenge.HostNonce : "wrong-nonce" };
            await child.StandardInput.WriteLineAsync(Protocol.Serialize(response));
            if (correctChallenge && sendInvalidFrame)
            {
                await child.StandardInput.WriteLineAsync("{this-is-not-valid-json}");
                await child.StandardInput.WriteLineAsync(Protocol.Serialize(new ToolObserve("observe",Protocol.Version,"unknown-operation","project-a")));
                await child.StandardInput.WriteLineAsync(Protocol.Serialize(new ToolCancel("cancel",Protocol.Version,"unknown-operation","project-a",2,"unknown-cancel")));
            }
            child.StandardInput.Close();
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(15));
            await child.WaitForExitAsync(timeout.Token);
            return child.ExitCode;
        }
        catch
        {
            if (!child.HasExited) child.Kill(entireProcessTree: true);
            throw;
        }
    }

    private static async Task VerifyRuntimeAuthorizationBoundaries(Registry registry, ToolRun source,
                                                                  string home)
    {
        string path = Path.Combine(home, "host-runtime-auth-boundaries");
        Directory.CreateDirectory(path);
        var journal = new ToolJournal(path);
        var observed = new List<ToolReceipt>();
        Task Collect(ToolReceipt receipt) { observed.Add(receipt); return Task.CompletedTask; }
        var runtime = new HostRuntime(registry,journal,"authorization-test",Collect,
            (_,_) => throw new InvalidOperationException("unverified request started a process"));
        await runtime.ObserveAsync(new ToolObserve("observe",Protocol.Version,
                                                  source.OperationId,source.ProjectId));
        Check(observed[^1].State == "failed" && observed[^1].Reason == "operation_not_found",
              "authorized operation without journal receipt is not fabricated as active");
        await runtime.CancelAsync(new ToolCancel("cancel",Protocol.Version,
                                                 source.OperationId,source.ProjectId,
                                                 source.LeaseGeneration,"cancel-before-run"));
        Check(observed[^1].State == "failed" && observed[^1].Reason == "operation_not_found",
              "valid lease cancellation before dispatch reports no running operation");
        var invalid = source with { SourceRevisionId="unapproved-source-revision" };
        await runtime.RunAsync(invalid,Protocol.Serialize(invalid));
        Check(observed[^1].State == "failed" &&
              observed[^1].Reason?.StartsWith("unauthorized:") == true,
              "runtime refuses unapproved source revision before subprocess start");
        await runtime.ShutdownAsync();
    }

    private static async Task VerifyRuntimeFailurePaths(Registry registry, ToolRun baseRun,
                                                        string temporary)
    {
        int sequence = 0;
        async Task<(ToolReceipt result,List<ToolReceipt> emissions)> Execute(
            string scenario, string executable, string[] arguments,
            int deadline = 2000, long maxOutput = 65536, string[]? outputFiles = null,
            bool cancelDuringRun = false)
        {
            string id = "coverage-" + (++sequence).ToString() + "-" + scenario;
            var operation = registry.Operations[0] with { OperationId=id, RequestId="req-"+id };
            var tool = baseRun with { OperationId=id, RequestId=operation.RequestId,
                                      DeadlineMs=deadline };
            var entry = registry.Packages[0].Entrypoints[0] with
                { MaxOutputBytes=maxOutput, OutputFiles=outputFiles ?? Array.Empty<string>() };
            var altered = registry with
            {
                Operations = new[] { operation },
                Packages = new[] { registry.Packages[0] with { Entrypoints = new[] { entry } } }
            };
            string home = Path.Combine(temporary,id);
            Directory.CreateDirectory(home);
            var journal = new ToolJournal(home);
            var emissions = new List<ToolReceipt>();
            var gate = new object();
            Task Emit(ToolReceipt receipt) { lock (gate) emissions.Add(receipt); return Task.CompletedTask; }
            var runtime = new HostRuntime(altered, journal, "coverage-host", Emit, (_, _) =>
            {
                var psi = new ProcessStartInfo(executable)
                {
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    WorkingDirectory = temporary
                };
                foreach (var argument in arguments) psi.ArgumentList.Add(argument);
                return psi;
            });
            await runtime.RunAsync(tool,Protocol.Serialize(tool));
            if (cancelDuringRun)
            {
                for (int i = 0; i < 100; i++)
                {
                    if (journal.Read(tool.ProjectId,tool.OperationId)?.Receipt.State == "running") break;
                    await Task.Delay(20);
                }
                await runtime.CancelAsync(new ToolCancel("cancel",Protocol.Version,id,
                                                        tool.ProjectId,tool.LeaseGeneration,"cancel-"+id));
            }
            ToolReceipt? terminal = null;
            for (int i = 0; i < 250; i++)
            {
                terminal = journal.Read(tool.ProjectId,tool.OperationId)?.Receipt;
                if (terminal is not null && terminal.State is "completed" or "failed" or "canceled" or "unknown")
                    break;
                await Task.Delay(20);
            }
            await runtime.ShutdownAsync();
            var final = journal.Read(tool.ProjectId,tool.OperationId)?.Receipt
                ?? throw new InvalidOperationException("Missing journal result for "+scenario);
            Check(final.State is "completed" or "failed" or "canceled" or "unknown",
                  "tool execution produced a terminal state for "+scenario);
            await runtime.ObserveAsync(new ToolObserve("observe",Protocol.Version,id,tool.ProjectId));
            Check(emissions.Count > 0 && emissions[^1].State == final.State,
                  "journal observation returns exact terminal state for "+scenario);
            return (final,emissions);
        }

        var failure = await Execute("nonzero","/usr/bin/false",Array.Empty<string>());
        Check(failure.result.State == "failed" && failure.result.ExitCode != 0,
              "nonzero child exit persists failed terminal receipt");

        var output = await Execute("output-limit","/usr/bin/sh",
                                   new[] { "-c", "printf '0123456789012345678901234567890123456789'" },
                                   maxOutput:8);
        Check(output.result.State == "failed" && output.result.Reason is not null,
              "bounded stdout stops oversized tool output");

        var missing = await Execute("missing-output","/usr/bin/true",Array.Empty<string>(),
                                    outputFiles:new[]{"required-artifact-does-not-exist"});
        Check(missing.result.State == "failed" && missing.result.Reason?.StartsWith("execution_failure") == true,
              "missing required output artifact invalidates successful process exit");

        var timeout = await Execute("deadline","/usr/bin/sleep",new[]{"3"},deadline:90);
        Check(timeout.result.State == "canceled" || timeout.result.State == "failed",
              "expired operation deadline terminates child without fabricated completion");

        var canceled = await Execute("cancellation","/usr/bin/sleep",new[]{"3"},
                                     cancelDuringRun:true);
        Check(canceled.result.State == "canceled" &&
              canceled.emissions.Any(x=>x.Reason == "cancel_requested"),
              "authorized in-flight cancellation affects only current lease");

        var clean = await Execute("success-output","/usr/bin/sh",
                                  new[]{"-c","printf stdout; printf stderr >&2"});
        Check(clean.result.State == "completed" && clean.result.LogDigests.Length == 2,
              "stdout and stderr records are flushed and content-hashed");
    }

    public static async Task RunAsync(Registry registry, ToolRun run, string registryPath, string install, string workspace)
    {
        Check(!Config.IsHash("bad") && Config.IsHash(new string('b',64)), "manifest SHA helper enforces byte identity");
        Reject(() => Config.SecureDirectory("relative/path"), "unrooted workspace denied");
        Reject(() => Config.SecureDirectory(Path.Combine(workspace,"missing")), "absent workspace denied");
        Reject(() => Config.ResolveExistingFile(install,"../escape"), "source path traversal denied");
        Reject(() => Config.ResolveExistingFile(install,"missing"), "absent package resource denied");
        Reject(() => Config.ResolveExistingFile(install,install), "absolute source path denied");
        Reject(() => Config.ResolveExecutable(install,""), "blank executable denied");
        Reject(() => Config.ResolveExecutable(install,"../escape"), "executable traversal denied");
        Check(Config.HashFile(Path.Combine(install,"true")).Length == 32,"real executable SHA-256 stream");

        // Keep the installed registry authoritative: deliberately corrupted test
        // variants must be refused and then exact original bytes restored.
        byte[] original = File.ReadAllBytes(registryPath);
        try
        {
            void VerifyReject(Registry altered, string label)
            {
                byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(altered);
                File.WriteAllBytes(registryPath,bytes);
                Reject(() => Config.Load(registryPath,Hash(bytes)),label);
            }
            VerifyReject(registry with { Version = 2 },"registry version mismatch denied");
            VerifyReject(registry with { Operations = registry.Operations.Concat(registry.Operations).ToArray() },
                         "duplicate operation IDs denied");
            VerifyReject(registry with { Workspaces = registry.Workspaces.Concat(registry.Workspaces).ToArray() },
                         "duplicate project workspace denied");
            VerifyReject(registry with { Packages = registry.Packages.Concat(registry.Packages).ToArray() },
                         "duplicate installed package digest denied");
            VerifyReject(registry with { Grants = registry.Grants.Concat(registry.Grants).ToArray() },
                         "duplicate project grant denied");
        }
        finally { File.WriteAllBytes(registryPath,original); }

        var resolved = Config.Authorize(registry,run);

        // Exercise manifest argument parsing against the installed, SHA-pinned
        // package while varying only the trusted registry's argument contract.
        var choice = new ArgumentRule("mode", "enum", new[] { "safe", "fast" });
        var declared = registry.Packages[0].Entrypoints[0] with { Arguments = new[] { choice } };
        var extended = registry with
        {
            Packages = new[] { registry.Packages[0] with { Entrypoints = new[] { declared } } }
        };
        using var validArgs = JsonDocument.Parse("{\"mode\":\"safe\"}");
        var supplied = run with { Args = validArgs.RootElement.Clone() };
        var approved = Config.Authorize(extended, supplied);
        Check(approved.Args.SequenceEqual(new[] { "safe" }, StringComparer.Ordinal),
              "declared enum argument survives pinned authorization");
        using var forbiddenArgs = JsonDocument.Parse("{\"mode\":\"denied\"}");
        Reject(() => Config.Authorize(extended, supplied with { Args = forbiddenArgs.RootElement.Clone() }),
               "unapproved enum value denied");
        using var missingArgs = JsonDocument.Parse("{}");
        Reject(() => Config.Authorize(extended, supplied with { Args = missingArgs.RootElement.Clone() }),
               "missing declared argument denied");
        using var wrongTypeArgs = JsonDocument.Parse("{\"mode\":123}");
        Reject(() => Config.Authorize(extended, supplied with { Args = wrongTypeArgs.RootElement.Clone() }),
               "nonstring enum argument denied");
        using var duplicateArgs = JsonDocument.Parse("{\"mode\":\"safe\",\"mode\":\"safe\"}");
        Reject(() => Config.Authorize(extended, supplied with { Args = duplicateArgs.RootElement.Clone() }),
               "duplicate argument keys denied");
        using var enormousArgs = JsonDocument.Parse(JsonSerializer.Serialize(
            new Dictionary<string, string> { ["mode"] = new string('a', 4097) }));
        Reject(() => Config.Authorize(extended, supplied with { Args = enormousArgs.RootElement.Clone() }),
               "oversized argument value denied");
        var untrustedRule = declared with { Arguments = new[] { new ArgumentRule("mode", "shell", new[] { "safe" }) } };
        var untrusted = extended with
        {
            Packages = new[] { registry.Packages[0] with { Entrypoints = new[] { untrustedRule } } }
        };
        Reject(() => Config.Authorize(untrusted, supplied), "non-enum argument rule denied");
        var badEnvironment = declared with
        {
            Environment = new Dictionary<string, string> { ["BAD=KEY"] = "value" }
        };
        var unsafeEnvironment = extended with
        {
            Packages = new[] { registry.Packages[0] with { Entrypoints = new[] { badEnvironment } } }
        };
        Reject(() => Config.Authorize(unsafeEnvironment, supplied),
               "invalid environment variable name denied");
        var invalidFixedArgs = declared with { FixedArgs = new[] { "nul\0argument" } };
        var unsafeFixed = extended with
        {
            Packages = new[] { registry.Packages[0] with { Entrypoints = new[] { invalidFixedArgs } } }
        };
        Reject(() => Config.Authorize(unsafeFixed, supplied),
               "embedded NUL in pinned argument denied");
        // Only construction is unit-tested here. Production Sandbox.Create still
        // MUST execute its real OS isolation probe before using this command.
        var command = Sandbox.BuildVerifiedCommand(resolved,run.DeadlineMs);
        Check(command.FileName == "/usr/bin/prlimit", "sandbox command enforces kernel rlimits");
        Check(command.ArgumentList.Contains("--unshare-all"), "sandbox command isolates namespaces");
        Check(command.ArgumentList.Contains("--die-with-parent"),"child process cannot outlive verified sandbox parent");
        Check(command.ArgumentList.Contains("--clearenv"),"sandbox clears inherited environment");
        Check(command.ArgumentList.Contains("--new-session"),"sandbox starts isolated process session");
        Check(command.ArgumentList.Contains("--tmpfs"),"sandbox mounts temporary writable root");
        Check(command.ArgumentList.Contains(resolved.Workspace),"sandbox pins exact task workspace");
        Check(command.ArgumentList.Contains(resolved.Executable),"sandbox pins installed executable path");
        Check(command.Environment.Count == 1 && command.Environment["PATH"] == "/usr/bin:/bin",
              "sandbox does not inherit hostile process environment");
        var configured = resolved with { Entrypoint = resolved.Entrypoint with
            { Environment = new Dictionary<string,string> { ["UNIT_TEST"]="bounded" } } };
        var populated = Sandbox.BuildVerifiedCommand(configured,run.DeadlineMs);
        Check(populated.ArgumentList.Contains("UNIT_TEST"),"only manifest-declared environment included");
        Reject(() => Sandbox.Create(resolved with { Entrypoint = resolved.Entrypoint with { Isolation = "untrusted" } },1000),
               "no sandbox downgrade to unverified backend");

        // Test the actual pipe protocol, including frame-length and UTF-8
        // failures, instead of only calling Protocol.Parse on valid objects.
        using (var valid = new MemoryStream(Encoding.UTF8.GetBytes("valid\r\n")))
            Check(await Protocol.ReadLineAsync(valid,128,CancellationToken.None)=="valid","CRLF frame correctly decoded");
        using (var ended = new MemoryStream())
            Check(await Protocol.ReadLineAsync(ended,128,CancellationToken.None) is null,"graceful EOF is not an error frame");
        await RejectRead(Encoding.UTF8.GetBytes("unterminated"),128,"truncated frame rejected");
        await RejectRead(Encoding.UTF8.GetBytes("\n"),128,"empty line rejected");
        await RejectRead(Encoding.UTF8.GetBytes("too-long\n"),3,"oversized frame rejected");
        await RejectRead(new byte[]{0xff,0xfe,0x0a},128,"invalid UTF-8 rejected");
        Reject(()=>Protocol.Parse(new string('a',Protocol.MaxFrameBytes+1)),"giant JSON frame rejected");
        Reject(()=>Protocol.Parse("[]"),"nonobject frame rejected");
        Reject(()=>Protocol.Parse("{\"kind\":\"untrusted\"}"),"unknown protocol discriminator rejected");
        Reject(()=>Protocol.Parse("{\"kind\":\"handshake\"}"),"missing handshake fields rejected");
        Reject(()=>Protocol.Parse("{\"kind\":\"handshake\",\"protocol_version\":\"1.0.0\",\"host_nonce\":\"x\",\"supported_isolation\":[1]}"),
               "invalid handshake isolation member rejected");
        var handshake = new Handshake("handshake",Protocol.Version,"challenge",new[]{Sandbox.LinuxCapability});
        Check(Protocol.Parse(Protocol.Serialize(handshake)) is Handshake,"valid protocol handshake decoded");
        Check(Protocol.Receipt("operation","project",1,"test-host","completed").State=="completed","canonical terminal receipt constructed");
        Check(Protocol.Sha256("hello")==Hash(Encoding.UTF8.GetBytes("hello")),"stable protocol SHA-256");

        Check(await Nomad.Studio.ToolHost.Program.Main(Array.Empty<string>())==2,"operator CLI rejects unverified argument count");
        Check(await Nomad.Studio.ToolHost.Program.Main(new[]{"--registry", "invalid", "--registry-sha256", "invalid", "--journal", "invalid"})==3,
              "operator CLI rejects preflight registry before opening pipes");
        string actualHash=Hash(File.ReadAllBytes(registryPath));
        string journal1=Path.Combine(Path.GetDirectoryName(registryPath)!,"host-pipe-correct");
        string journal2=Path.Combine(Path.GetDirectoryName(registryPath)!,"host-pipe-rejected");
        Check(await RunChild(registryPath,actualHash,journal1,true,true)==0,
              "real broker handshake and malformed frame stream recover correctly");
        Check(await RunChild(registryPath,actualHash,journal2,false,false)==4,
              "real broker challenge mismatch fails closed");
        await VerifyRuntimeFailurePaths(registry,run,Path.GetDirectoryName(registryPath)!);
        await VerifyRuntimeAuthorizationBoundaries(registry,run,Path.GetDirectoryName(registryPath)!);
    }
}