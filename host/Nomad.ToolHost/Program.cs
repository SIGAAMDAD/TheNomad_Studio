using System.Security.Cryptography;
using System.Text;
using Nomad.Studio.ToolHost.Contracts;

namespace Nomad.Studio.ToolHost;

public static class Program
{
    // Local broker launches this process with PRIVATE stdin/stdout, verified executable
    // identity and a SHA-pinned registry. No sockets, HTTP server, GitHub relay ingestion,
    // LLM API client or model-agent runtime is hosted here.
    public static async Task<int> Main(string[] args)
    {
        if (args.Length != 6 || args[0] != "--registry" || args[2] != "--registry-sha256" || args[4] != "--journal")
        {
            Console.Error.WriteLine("usage: Nomad.ToolHost --registry ABS_PATH --registry-sha256 SHA256 --journal ABS_PATH");
            return 2;
        }
        Registry registry;
        ToolJournal journal;
        try
        {
            registry = Config.Load(args[1], args[3]);
            journal = new ToolJournal(args[5]);
            journal.ReconcileInterrupted();
        }
        catch (Exception e)
        {
            Console.Error.WriteLine("host preflight failed: " + e.Message);
            return 3;
        }
        string nonce = Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
        string hostId = Environment.MachineName + ":" + Environment.ProcessId;
        string[] supported = Sandbox.Available();
        var writer = new SemaphoreSlim(1, 1);
        async Task Send(object reply)
        {
            await writer.WaitAsync();
            try
            {
                string frame = Protocol.Serialize(reply);
                if (Encoding.UTF8.GetByteCount(frame) > Protocol.MaxFrameBytes)
                    throw new InvalidDataException("Oversized outgoing frame");
                await Console.Out.WriteLineAsync(frame);
                await Console.Out.FlushAsync();
            }
            finally { writer.Release(); }
        }
        var host = new HostRuntime(registry, journal, hostId, Send);
        // The host emits a one-time challenge and refuses all tool commands until its
        // parent echoes the exact nonce and protocol. This is a pipe-liveness check;
        // actual authority is the OS-controlled private pipe and pinned registry.
        await Send(new Handshake("handshake", Protocol.Version, nonce, supported));
        try
        {
            Stream stdin = Console.OpenStandardInput();
            string? first = await Protocol.ReadLineAsync(stdin, Protocol.MaxFrameBytes, CancellationToken.None);
            if (first is null || Protocol.Parse(first) is not Handshake reply || reply.HostNonce != nonce ||
                !reply.SupportedIsolation.SequenceEqual(supported, StringComparer.Ordinal))
            {
                Console.Error.WriteLine("broker handshake rejected");
                return 4;
            }
            while (true)
            {
                string? frame = await Protocol.ReadLineAsync(stdin, Protocol.MaxFrameBytes, CancellationToken.None);
                if (frame is null) break;
                object command;
                try { command = Protocol.Parse(frame); }
                catch (Exception e) when (e is InvalidDataException or System.Text.Json.JsonException)
                {
                    await Send(Protocol.Receipt("protocol_error", "unscoped", 1, hostId, "failed", "invalid_frame: " + e.Message));
                    continue;
                }
                switch (command)
                {
                    case ToolRun run: await host.RunAsync(run, frame); break;
                    case ToolObserve observe: await host.ObserveAsync(observe); break;
                    case ToolCancel cancel: await host.CancelAsync(cancel); break;
                    default:
                        await Send(Protocol.Receipt("protocol_error", "unscoped", 1, hostId, "failed", "invalid_frame_state"));
                        break;
                }
            }
            await host.ShutdownAsync();
            return 0;
        }
        catch (Exception e) when (e is InvalidDataException or IOException or UnauthorizedAccessException or
            InvalidOperationException or System.Text.Json.JsonException)
        {
            Console.Error.WriteLine("host transport error: " + e.Message);
            await host.ShutdownAsync();
            return 5;
        }
    }
}
