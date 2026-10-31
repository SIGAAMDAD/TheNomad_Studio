"""Typed local errors; HTTP translation is owned by WG-J."""


class StorageError(Exception):
    code = "internal"


class Forbidden(StorageError):
    code = "forbidden"


class Invalid(StorageError):
    code = "invalid"


class Conflict(StorageError):
    code = "conflict"


class Missing(StorageError):
    code = "missing"


class UnknownEffect(StorageError):
    code = "unknown_effect"


class Unavailable(StorageError):
    code = "capability_unavailable"
