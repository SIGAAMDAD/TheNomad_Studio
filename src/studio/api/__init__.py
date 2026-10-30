"""WG-J operator API: frozen OpenAPI contract router and trusted HTTP boundary."""
from .contracts import FrozenContracts
from .errors import ApiError
from .events import EventPageService
from .http import StudioWSGI
from .identity import ConfiguredOpaqueVerifier, PrincipalProvisioner, VerifiedPrincipal
from .service import DomainRegistry, StudioOperatorService
from .server import create_http_server

__all__ = (
    'ApiError', 'create_http_server', 'ConfiguredOpaqueVerifier', 'DomainRegistry', 'EventPageService',
    'FrozenContracts', 'PrincipalProvisioner', 'StudioOperatorService', 'StudioWSGI', 'VerifiedPrincipal',
)
