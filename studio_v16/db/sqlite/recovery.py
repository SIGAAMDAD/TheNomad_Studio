"""Separate, server-authenticated readback capability; no publication admission."""
from dataclasses import dataclass
from studio_v16.db.publication import PublicationDenied
from .publication import row
from .types import canonical
from studio_v16.gateway.contracts import canonical_hash


@dataclass(frozen=True)
class TrustedRecoveryIdentity:
    # Trusted service input, constructed after independent mTLS authentication.
    # Never deserialize this object or provider receipts from a worker request.
    principal_id: str
    verified_gateway: bool = False
    provider_readback_verified: bool = False


class SQLiteRecovery:
    def reconcile_dispatched_effect(self, publication_id, idempotency_key, dispatch_id, receipt,
                                    recovery_identity: TrustedRecoveryIdentity, *, absence_evidence=None):
        identity = recovery_identity
        if (not isinstance(identity, TrustedRecoveryIdentity) or not identity.verified_gateway
                or not identity.provider_readback_verified or identity.principal_id not in self.recovery_principals):
            raise PublicationDenied('IDENTITY_UNVERIFIED')
        with self.owner.write() as (conn, now):
            principal = row(conn, 'SELECT status,kind FROM principals WHERE principal_id=?', (identity.principal_id,))
            if principal != {'status': 'ACTIVE', 'kind': 'RECOVERY_READBACK'}:
                raise PublicationDenied('IDENTITY_UNVERIFIED')
            record = self._intent(conn, publication_id, idempotency_key)
            dispatch = row(conn, '''SELECT dispatch_id,principal_id FROM effect_dispatches WHERE publication_id=?
                ORDER BY dispatch_sequence DESC LIMIT 1''', (publication_id,))
            if (record is None or dispatch is None or dispatch['dispatch_id'] != dispatch_id
                    or identity.principal_id == dispatch['principal_id'] or identity.principal_id == record['principal_id']):
                raise PublicationDenied('ACCESS_REQUIRED')
            # Deliberately independent of the revoked worker's grant/mode/lease.
            # Only the already admitted dispatch's exact terminal facts can change.
            result = self._apply_receipt(conn, record, receipt, now)
            if absence_evidence is not None:
                target = record['intent']['target']
                expected = (dict(repo_id=target['repo_id'], work_branch=target['work_branch'], head_sha=target['source_base_sha'])
                            if target['provider'] == 'GITHUB' else
                            dict(file_id=target['file_id'], revision=target['expected_revision']))
                if result != 'RECONCILE_REQUIRED' or absence_evidence != expected:
                    raise PublicationDenied('INVALID_SCOPE')
                conn.execute('''INSERT INTO effect_absence_proofs(dispatch_id,verifier_principal_id,
                    evidence_json,evidence_sha256,observed_at) VALUES(?,?,?,?,?) ON CONFLICT(dispatch_id) DO NOTHING''',
                    (dispatch_id, identity.principal_id, canonical(expected), canonical_hash(expected), now))
            return result

    def recorded_dispatch(self, publication_id, idempotency_key, recovery_identity):
        identity = recovery_identity
        if (not isinstance(identity, TrustedRecoveryIdentity) or not identity.verified_gateway
                or identity.principal_id not in self.recovery_principals):
            raise PublicationDenied('IDENTITY_UNVERIFIED')
        with self.owner.connection(readonly=True) as conn:
            principal = row(conn, 'SELECT status,kind FROM principals WHERE principal_id=?', (identity.principal_id,))
            if principal != {'status': 'ACTIVE', 'kind': 'RECOVERY_READBACK'}:
                raise PublicationDenied('IDENTITY_UNVERIFIED')
            record = self._intent(conn, publication_id, idempotency_key)
            dispatch = row(conn, '''SELECT dispatch_id,principal_id FROM effect_dispatches WHERE publication_id=?
                ORDER BY dispatch_sequence DESC LIMIT 1''', (publication_id,))
            if record is None or dispatch is None or dispatch['principal_id'] == identity.principal_id:
                raise PublicationDenied('ACCESS_REQUIRED')
            return record['intent'], dispatch['dispatch_id']
