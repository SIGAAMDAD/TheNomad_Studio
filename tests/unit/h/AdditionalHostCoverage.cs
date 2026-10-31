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
    }
}