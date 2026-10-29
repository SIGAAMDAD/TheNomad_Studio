"""R9 production command dispatcher: P2 owns durable outbox rows."""
from .dispatcher import OwnerCommandOutboxHandler
__all__ = ['OwnerCommandOutboxHandler']
