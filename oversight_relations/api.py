"""WG-AO's candidate-only private Oversight integration port."""
from __future__ import annotations
from typing import Mapping
from .service import RelationsComponent

PORT_NAMES = ('store','case_access','evidence_authority','mediator_registry','emergency_gate','notice_authority')

def build_component(deps: Mapping[str,object]) -> RelationsComponent:
    """Build without registering hr.mediation in any Studio gateway."""
    return RelationsComponent(**{name: deps.get(name) for name in PORT_NAMES})
