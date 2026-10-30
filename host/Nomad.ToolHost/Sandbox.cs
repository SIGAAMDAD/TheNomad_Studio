using System.Diagnostics;
using System.Globalization;

namespace Nomad.Studio.ToolHost;

// Actual platform enforcement is checked before advertising a capability or running
// an operation. No fallback from sandboxed to ordinary Process.Start is permitted.
public static class Sandbox
{
    private const string Bubblewrap = "/usr/bin/bwrap";
    private const string Prlimit = "/usr/bin/prlimit";
    public const string LinuxCapability = "linux-bwrap-userns-rlimits-no-network";

    public static string[] Available()
    {
        return LinuxPreflight(out _) ? new[] { LinuxCapability } : Array.Empty<string>();
    }

    public static bool LinuxPreflight(out string reason)
    {
        if (!OperatingSystem.IsLinux())
        {
            reason = "No verified sandbox backend on this operating system";
            return false;
        }
        if (!File.Exists(Bubblewrap) || !File.Exists(Prlimit) || !File.Exists("/usr/bin/true"))
        {
            reason = "bubblewrap, prlimit or /usr/bin/true is unavailable";
            return false;
        }
        try
        {
            using var process = new Process { StartInfo = new ProcessStartInfo(Bubblewrap)
                { UseShellExecute = false, RedirectStandardError = true, RedirectStandardOutput = true } };
            foreach (string arg in new[] { "--unshare-all", "--die-with-parent", "--ro-bind", "/usr", "/usr",
                "--proc", "/proc", "--dev", "/dev", "--", "/usr/bin/true" })
                process.StartInfo.ArgumentList.Add(arg);
            if (!process.Start()) { reason = "Isolation probe failed to start"; return false; }
            if (!process.WaitForExit(3000))
            {
                process.Kill(entireProcessTree: true);
                reason = "Isolation probe timed out";
                return false;
            }
            if (process.ExitCode != 0)
            {
                reason = "Namespace isolation probe failed (exit " + process.ExitCode + ")";
                return false;
            }
            reason = "verified namespace/network isolation probe";
            return true;
        }
        catch (Exception e) when (e is System.ComponentModel.Win32Exception or IOException or InvalidOperationException)
        {
            reason = "Sandbox preflight error: " + e.GetType().Name;
            return false;
        }
    }

    public static ProcessStartInfo Create(ResolvedRun resolved, int deadlineMs)
    {
        if (resolved.Entrypoint.Isolation != LinuxCapability || !LinuxPreflight(out string reason))
            throw new InvalidOperationException("capability_unavailable: " +
                (resolved.Entrypoint.Isolation == LinuxCapability ? reason : "unrecognized/unverified isolation requirement"));

        EntryPoint entry = resolved.Entrypoint;
        // RLIMITs are inherited by the sandbox and its descendants. CPU, address space,
        // file size and per-UID process count are kernel enforced; deadline is separately
        // enforced by a host watchdog and process-tree cancellation.
        var psi = new ProcessStartInfo(Prlimit)
        {
            UseShellExecute = false, RedirectStandardError = true, RedirectStandardOutput = true,
            RedirectStandardInput = false, CreateNoWindow = true, WorkingDirectory = resolved.Workspace
        };
        psi.Environment.Clear();
        psi.Environment["PATH"] = "/usr/bin:/bin";
        string[] limits =
        {
            "--cpu=" + Math.Max(1, (int)Math.Ceiling(deadlineMs / 1000.0)).ToString(CultureInfo.InvariantCulture),
            "--as=" + entry.MaxMemoryBytes.ToString(CultureInfo.InvariantCulture),
            "--fsize=" + entry.MaxOutputBytes.ToString(CultureInfo.InvariantCulture),
            "--nproc=" + entry.MaxProcesses.ToString(CultureInfo.InvariantCulture), "--", Bubblewrap,
            "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
            "--ro-bind", "/usr", "/usr"
        };
        foreach (var arg in limits) psi.ArgumentList.Add(arg);
        // System runtime directories are read-only; only the installed package and the
        // designated project workspace are accessible through explicit mounts.
        AddSystemMount(psi, "/bin", "usr/bin");
        AddSystemMount(psi, "/lib", "usr/lib");
        AddSystemMount(psi, "/lib64", "usr/lib64");
        if (File.Exists("/etc/ld.so.cache"))
        {
            psi.ArgumentList.Add("--ro-bind"); psi.ArgumentList.Add("/etc/ld.so.cache"); psi.ArgumentList.Add("/etc/ld.so.cache");
        }
        string packageRoot = resolved.InstallRoot;
        psi.ArgumentList.Add("--ro-bind"); psi.ArgumentList.Add(packageRoot); psi.ArgumentList.Add(packageRoot);
        psi.ArgumentList.Add("--bind"); psi.ArgumentList.Add(resolved.Workspace); psi.ArgumentList.Add(resolved.Workspace);
        foreach (string arg in new[] { "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
            "--chdir", resolved.Workspace, "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp" })
            psi.ArgumentList.Add(arg);
        foreach (var pair in entry.Environment.OrderBy(x => x.Key, StringComparer.Ordinal))
        {
            psi.ArgumentList.Add("--setenv"); psi.ArgumentList.Add(pair.Key); psi.ArgumentList.Add(pair.Value);
        }
        psi.ArgumentList.Add("--"); psi.ArgumentList.Add(resolved.Executable);
        foreach (string arg in resolved.Args) psi.ArgumentList.Add(arg);
        return psi;
    }

    private static void AddSystemMount(ProcessStartInfo psi, string location, string defaultTarget)
    {
        if (Directory.Exists(location))
        {
            if ((File.GetAttributes(location) & FileAttributes.ReparsePoint) != 0)
            {
                psi.ArgumentList.Add("--symlink"); psi.ArgumentList.Add(defaultTarget); psi.ArgumentList.Add(location);
            }
            else
            {
                psi.ArgumentList.Add("--ro-bind"); psi.ArgumentList.Add(location); psi.ArgumentList.Add(location);
            }
        }
    }
}
