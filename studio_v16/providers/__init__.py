"""WG-G: isolated, configured providers; no worker-held credentials."""
from .git_provider import IsolatedGitProvider, ProviderBlocked
from .drive_provider import DriveRevisionProvider
