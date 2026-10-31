"""Nomad Studio WG-G project-scoped provider and artifact implementations.

Boundary: A/J authenticate and authorize; C persists effect intents/cursors; I parses
relay lifecycle; H executes installed tools; G reads back only actual external bytes.
"""
from .common import IntegrationError, RemoteUnknown, ProviderFailure, ProjectBinding, canonical, sha256
from .artifacts import Artifact, ArtifactStore
from .github import GitHub, GitHubChannel, RemoteFile, ScanPage
from .drive import Drive
from .ci import GitHubActions, BuildTarget, BuildObservation
from .effects import EffectIntent, EffectObservation, EffectReconciler
from .relay import validate_relay, validate_receipt
from .toolchain import PackageVerifier
from .connectors import ConnectorService, ConnectorAttachment
from .source import GitHubSource, SourceSnapshot
from .retry import RetryPolicy
from .api_ports import GOperatorPorts, PreparedBuild
