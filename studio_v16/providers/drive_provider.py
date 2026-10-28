"""Revision-CAS Google Drive provider contract with external readback fencing.

The injected client must implement server-side *atomic* update_if_revision;
read-then-write alone is never treated as CAS. No tokens or worker credentials.
"""
from __future__ import annotations
import hashlib
from .git_provider import ProviderBlocked


class DriveRevisionProvider:
    def __init__(self, cas_client, *, authorized=None, enable_external_effects=False):
        self._client=cas_client
        self._authorized=authorized
        self._enabled=enable_external_effects

    def revision(self,file_id):
        value=self._client.revision(file_id)
        if not isinstance(value,str) or not value:
            raise ProviderBlocked('DOCUMENT_CONFLICT')
        return value

    def read_pinned(self, file_id, revision, exported_sha256):
        # Existing clients expose current revision readback. Historical exports
        # require a separately supplied pinned-revision client; never fall back
        # to unpinned connector content.
        pinned = getattr(self._client, 'read_revision', None)
        if not callable(pinned):
            raise ProviderBlocked('DOCUMENT_CONFLICT')
        try:
            data = pinned(file_id, revision)
        except (ConnectionError, TimeoutError, LookupError, ValueError):
            raise ProviderBlocked('DOCUMENT_CONFLICT') from None
        if (not isinstance(data, dict) or data.get('revision') != revision
                or not isinstance(data.get('content'), bytes)
                or hashlib.sha256(data['content']).hexdigest() != exported_sha256):
            raise ProviderBlocked('DOCUMENT_CONFLICT')
        return data['content']

    def readback(self,intent):
        target=intent['target']
        data=self._client.readback(target['file_id'])
        if not isinstance(data,dict) or not isinstance(data.get('content'),bytes):
            return None
        observed=hashlib.sha256(data['content']).hexdigest()
        if (observed!=target['exported_sha256'] or
                data.get('idempotency_key')!=intent['idempotency_key'] or
                not isinstance(data.get('revision'),str) or
                not data['revision'] or data['revision']==target['expected_revision']):
            return None
        return dict(file_id=target['file_id'],revision=data['revision'],
                    exported_sha256=target['exported_sha256'])

    def publish(self,intent,payload):
        if not self._enabled or self._authorized is None:
            raise ProviderBlocked('CUTOVER_NOT_APPROVED')
        target=intent['target']
        if hashlib.sha256(payload).hexdigest()!=target['exported_sha256'] or len(payload)>16*1024*1024:
            raise ProviderBlocked('SOURCE_SHA_MISMATCH')
        found=self.readback(intent)
        if found is not None:return found
        if self.revision(target['file_id'])!=target['expected_revision']:
            raise ProviderBlocked('DOCUMENT_CONFLICT')
        if self._authorized(intent) is not True:
            raise ProviderBlocked('CUTOVER_NOT_APPROVED')
        try:
            self._client.update_if_revision(target['file_id'],target['expected_revision'],payload,
                                            intent['idempotency_key'])
        except (ConnectionError,TimeoutError):
            found=self.readback(intent)
            if found is not None:return found
            raise ProviderBlocked('RECONCILE_REQUIRED')
        except ValueError as exc:
            raise ProviderBlocked('DOCUMENT_CONFLICT') from exc
        found=self.readback(intent)
        if found is None: raise ProviderBlocked('RECONCILE_REQUIRED')
        return found

    def recovery_readback(self, intent):
        """Trusted current state, including proved absence and terminal mismatch."""
        target = intent['target']
        data = self._client.readback(target['file_id'])
        if not isinstance(data, dict) or not isinstance(data.get('revision'), str) or not data['revision']:
            return {'state': 'UNKNOWN'}
        if data['revision'] == target['expected_revision']:
            return {'state': 'ABSENT', 'evidence': dict(file_id=target['file_id'], revision=data['revision'])}
        if not isinstance(data.get('content'), bytes):
            return {'state': 'UNKNOWN'}
        observed = dict(file_id=target['file_id'], revision=data['revision'],
                        exported_sha256=hashlib.sha256(data['content']).hexdigest())
        if data.get('idempotency_key') != intent['idempotency_key'] or observed['exported_sha256'] != target['exported_sha256']:
            return {'state': 'CONFLICT'}
        return {'state': 'PRESENT', 'observed': observed}
