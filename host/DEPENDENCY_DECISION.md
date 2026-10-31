# WG-H dependency assessment (REQ-037)

| Dependency | Choice | Ownership and version contract | Status |
|---|---|---|---|
| C# host | .NET base SDK `net10.0`; `System.Diagnostics.Process`, `System.Text.Json`, `System.Security.Cryptography` | WG-H. No external NuGet packages or mutable floating package references | Source authored; clean .NET 10 build NOT_RUN (SDK unavailable) |
| Linux isolation | OS-installed bubblewrap (`/usr/bin/bwrap`) and util-linux prlimit (`/usr/bin/prlimit`) | WG-H verifies actual namespace isolation at runtime; OS image owner must pin exact binary revisions/hashes in deployment | NOT_RUN (bubblewrap unavailable in authoring environment) |
| .NET process wrappers | No CliWrap dependency | Built-in typed `ProcessStartInfo.ArgumentList` sufficient for current host functionality | No third-party lock needed |
| Windows isolation | Restricted identity + ACL + Job Object / network deny platform implementation | WG-H; no Windows execution path is enabled until native isolation proves required controls | NOT_IMPLEMENTED |
| Installed tool/package manifests | Frozen `CONTRACTS/package.schema.json`, imported from WG-B | WG-B owns installation/readiness, WG-H independently verifies selected runtime files | Isolated source validation authored; runtime NOT_RUN |

No model-provider API SDK is present. No new global hosting service or spending is introduced. A clean-install check must verify the exact deployed .NET SDK and Linux isolation binary versions before a Tier C pilot, without claiming those checks from static file inspection.
