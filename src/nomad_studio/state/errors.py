"""Non-wire exceptions from the single-writer SQLite authority."""

class StateError(Exception):
    code = 'BLOCKED'


class ConfigurationError(StateError):
    code = 'NOT_CONFIGURED'


class OwnerLocked(StateError):
    code = 'BACKPRESSURE'


class Backpressure(StateError):
    code = 'BACKPRESSURE'


class DeadlineExceeded(StateError):
    code = 'BACKPRESSURE'


class UnknownCommit(StateError):
    code = 'PROVIDER_UNKNOWN'


class MigrationError(StateError):
    code = 'INVALID_SCHEMA'


class IdempotencyConflict(StateError):
    code = 'IDEMPOTENCY_CONFLICT'


class InvalidState(StateError):
    code = 'INVALID_SCHEMA'


class ClosedStore(StateError):
    code = 'NOT_CONFIGURED'


class StaleAssignment(StateError):
    code = 'STALE_ASSIGNMENT'
