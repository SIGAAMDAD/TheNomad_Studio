"""WG-D isolated gateway behavioral tests: fake trust/PG/provider, zero network."""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from studio_v16.gateway.contracts import validate, ContractError, canonical_hash
from studio_v16.gateway.gateway import Gateway
from studio_v16.gateway.interfaces import (Principal, State, VerifiedQA, SourceSnapshot,
                                           InspectedPatch, GitEntry)
from studio_v16.gateway.paths import validate_paths
from studio_v16.gateway.transport import MTLSIdentity, server_context
from studio_v16.gateway.credentials import GitHubAppCredentials, InstallationScope, CredentialError

POS = json.loads((ROOT / 'tests/v16/contracts/fixtures/positive.json').read_text())
NOW = datetime(2026, 10, 27, 20, 0, tzinfo=timezone.utc)
PATCH = b'diff --git a/studio/v16/example.txt b/studio/v16/example.txt\n+hello\n'
H = hashlib.sha256(PATCH).hexdigest()
TREE = 'd'*64
FILE = 'studio/v16/example.txt'
BASE = 'a'*40
BRANCH = 'studio/workorders/JOB-V16-A/contract'

class FakeAuthority:
    def __init__(self, state):
        self.state = state
        self.reserved = {}
        self.effects = set()
        self.missing = False
        self.fail_atomic = False

    def load(self, job_id, grant_id):
        if self.missing:
            raise ConnectionError('db offline')
        if job_id != self.state.grant['job_id'] or grant_id != self.state.grant['grant_id']:
            raise LookupError('no trusted grant')
        return self.state

    def reserve(self, intent, principal_id, state_revision, payload_sha256):
        if self.fail_atomic or state_revision != self.state.state_revision or self.state.revoked:
            return 'RETRYABLE_BLOCKED'
        if (principal_id != self.state.grant['principal_id'] or
            intent['assignment_epoch'] != self.state.assignment_epoch or
            intent['lease_generation'] != self.state.lease_generation):
            return 'STALE_LEASE'
        key = intent['idempotency_key']
        if key in self.reserved:
            old = self.reserved[key]
            return 'DUPLICATE' if canonical_hash(old) == payload_sha256 else 'IDEMPOTENCY_CONFLICT'
        self.reserved[key] = copy.deepcopy(intent)
        return 'COMMITTED'

    def recorded(self, publication_id, idempotency_key):
        candidate = self.reserved.get(idempotency_key)
        return candidate if candidate and candidate['publication_id'] == publication_id else None

    def reconcile(self, publication_id, idempotency_key, state_revision, receipt):
        if self.fail_atomic or state_revision != self.state.state_revision:
            return False
        self.effects.add((publication_id, idempotency_key))
        return True

class FakeGit:
    def __init__(self):
        self.head_sha = BASE
        self.extras = ()
        self.proposed_entries = (GitEntry(FILE, '100644'),)
        self.inspected_paths = (FILE,)
        self.read = None
        self.calls = 0

    def snapshot(self, repo_id, branch):
        self.calls += 1
        return SourceSnapshot(repo_id, branch, self.head_sha, (GitEntry(FILE, '100644'),)+self.extras)

    def inspect_patch(self, repo_id, base_sha, patch):
        self.calls += 1
        return InspectedPatch(hashlib.sha256(patch).hexdigest(), TREE,
                              self.inspected_paths, self.proposed_entries+self.extras)

    def readback(self, intent):
        return self.read

class FakeDrive:
    def __init__(self):
        self.rev = 'rev-17'
        self.read = None
    def revision(self, file_id): return self.rev
    def readback(self, intent): return self.read

class FakeAudit:
    def __init__(self): self.events = []
    def emit(self, record): self.events.append(record)


def make_suite():
    grant = copy.deepcopy(POS['auth-grant'])
    grant['owned_path_prefixes'] = ['studio/v16/']
    state = State(grant=grant, grant_issuer_verified=True, owner_approved=True,
        approved_scope_sha256=grant['approved_scope_sha256'],
        policy_revision=grant['policy_revision'], authority_revision=grant['authority_revision'],
        assignment_epoch=grant['assignment_epoch'], lease_generation=grant['lease_generation'],
        holder_attempt_id='ATTEMPT-20261027-A', work_branch=BRANCH, source_base_sha=BASE,
        revoked=False, budget_authorized=True, source_writes_enabled=True,
        qa=VerifiedQA('REVIEW-A', 'principal-qa-002', H, BASE, 'artifact:qa-report-A'),
        state_revision='state:stable1', drive_file_id='drive-file-example-1',
        drive_document_owner_ref='owner-principal-001', drive_expected_revision='rev-17',
        document_writes_enabled=True, evidence_sha256='f'*64)
    a, g, d, audit = FakeAuthority(state), FakeGit(), FakeDrive(), FakeAudit()
    gateway = Gateway(authority=a, git=g, drive=d, audit=audit, clock=lambda: NOW)
    request = copy.deepcopy(POS['auth-evaluate'])
    request['target_paths'] = [FILE]
    intent = copy.deepcopy(POS['publish-intent'])
    intent['artifact_sha256'] = H
    intent['independent_qa']['subject_sha256'] = H
    intent['independent_qa']['source_sha'] = BASE
    intent['target']['changed_paths'] = [FILE]
    intent['target']['patch_sha256'] = H
    intent['target']['proposed_tree_sha256'] = TREE
    intent['idempotency_key'] = '0'*64
    return gateway, a, g, d, audit, request, intent

class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.gw, self.a, self.g, self.d, self.audit, self.req, self.intent = make_suite()
        self.principal = Principal('principal-worker-001')

    def evaluate(self, **changes):
        req = dict(self.req, **changes)
        result = self.gw.evaluate(req, self.principal)
        validate(result, 'AUTH-V16/DECISION/1')
        return result['denial_code'] or result['decision']

    def prepare(self, **changes):
        intent = copy.deepcopy(self.intent)
        intent.update(changes)
        result = self.gw.prepare(intent, PATCH, self.principal)
        validate(result, 'PUBLISH-V16/READBACK/1')
        return result['denial_code'] or result['status']

    def test_valid_request_is_allowed_with_verified_identity(self):
        self.assertEqual(self.evaluate(), 'ALLOW')
        self.assertEqual(self.prepare(), 'PREPARED')
        self.assertEqual(len(self.a.reserved), 1)
        self.assertEqual(len(self.a.effects), 0)

    def test_bad_json_fields_return_schema_safe_denials(self):
        invalid = {'schema_id': 'PUBLISH-V16/INTENT/99', 'idempotency_key': 4,
                   'publication_id': [], 'target': None}
        receipt = self.gw.prepare(invalid, PATCH, self.principal)
        self.assertEqual(receipt['status'], 'BLOCKED')
        validate(receipt, 'PUBLISH-V16/READBACK/1')
        bad_recon = self.gw.reconcile([], 'bogus', self.principal)
        self.assertEqual(bad_recon['denial_code'], 'MALFORMED_REQUEST')
        validate(bad_recon, 'PUBLISH-V16/READBACK/1')

    def test_default_no_adapters_denies(self):
        gw = Gateway(clock=lambda: NOW)
        self.assertEqual(gw.evaluate(self.req, self.principal)['denial_code'], 'GATEWAY_UNAVAILABLE')
        self.assertEqual(gw.prepare(self.intent, PATCH, self.principal)['status'], 'BLOCKED')

    def test_missing_certificate_denies_even_if_claimed_owner(self):
        self.assertEqual(self.gw.evaluate(self.req, None)['denial_code'], 'IDENTITY_UNVERIFIED')
        self.assertEqual(self.gw.prepare(self.intent, PATCH, None)['denial_code'], 'IDENTITY_UNVERIFIED')
        self.assertEqual(self.gw.evaluate(self.req, Principal('owner-principal-001'))['denial_code'], 'IDENTITY_UNVERIFIED')

    def test_shared_connector_not_mtls(self):
        self.assertEqual(self.gw.evaluate(self.req, Principal('principal-worker-001', 'SHARED_OWNER_CONNECTOR'))['decision'], 'DENY')

    def test_stale_epoch_and_lease(self):
        self.assertEqual(self.evaluate(assignment_epoch=3), 'STALE_ASSIGNMENT')
        self.assertEqual(self.evaluate(lease_generation=5), 'STALE_LEASE')
        self.a.state = replace(self.a.state, holder_attempt_id='ATTEMPT-OTHER')
        self.assertEqual(self.evaluate(), 'STALE_LEASE')

    def test_no_grant_or_issuer_proof(self):
        self.a.state = replace(self.a.state, grant_issuer_verified=False)
        self.assertEqual(self.evaluate(), 'ACCESS_REQUIRED')
        self.assertEqual(self.prepare(), 'ACCESS_REQUIRED')

    def test_owner_approval_revocation_and_expiry(self):
        self.a.state = replace(self.a.state, owner_approved=False)
        self.assertEqual(self.evaluate(), 'OWNER_APPROVAL_REQUIRED')
        self.a.state = replace(self.a.state, owner_approved=True, revoked=True)
        self.assertEqual(self.evaluate(), 'REVOKED_GRANT')
        self.a.state = replace(self.a.state, revoked=False, grant=dict(self.a.state.grant, expires_at='2026-10-27T20:00:00Z'))
        self.assertEqual(self.evaluate(), 'EXPIRED_GRANT')

    def test_policy_revision_and_grant_scope(self):
        self.assertEqual(self.evaluate(policy_revision='policy:different'), 'POLICY_REVISION_MISMATCH')
        self.assertEqual(self.evaluate(authority_revision='authority:different'), 'AUTHORITY_REVISION_MISMATCH')
        self.a.state = replace(self.a.state, approved_scope_sha256='0'*64)
        self.assertEqual(self.evaluate(), 'INVALID_SCOPE')

    def test_permission_restrictions(self):
        self.assertEqual(self.evaluate(operation='SOURCE_MERGE'), 'INVALID_SCOPE')
        self.a.state = replace(self.a.state, source_writes_enabled=False)
        self.assertEqual(self.evaluate(), 'CUTOVER_NOT_APPROVED')
        self.assertEqual(self.prepare(), 'CUTOVER_NOT_APPROVED')

    def test_provider_branch_drift(self):
        self.g.head_sha = 'b'*40
        self.assertEqual(self.evaluate(), 'REBASE_REQUIRED')
        self.assertEqual(self.prepare(), 'REBASE_REQUIRED')
        self.assertEqual(len(self.a.reserved), 0)

    def test_branch_targets_restricted(self):
        self.assertEqual(self.evaluate(target_branch='main'), 'INVALID_SCOPE')
        self.assertEqual(self.evaluate(target_branch='studio/workorders/JOB-other/bad'), 'INVALID_SCOPE')

    def test_path_traversal_and_dotfiles(self):
        for path in ('../file.txt', '.github/workflows/run.yml', 'studio/v16/../x',
                     'studio/v16/%2e%2e/x', 'studio/v16/x\\y', 'studio/v16/密钥.txt',
                     'studio/v16//x', 'studio/v16/.env', 'studio/v16/id_rsa',
                     'studio/v16/secrets/pass.txt', 'studio/v16/private.key'):
            with self.subTest(path=path):
                self.assertNotEqual(self.evaluate(target_paths=[path]), 'ALLOW')

    def test_protected_path_and_prefix_boundary(self):
        bad = validate_paths(['.github/workflows/test.yml'], ['.github/'],
            (GitEntry('.github/workflows/test.yml','100644'),),
            (GitEntry('.github/workflows/test.yml','100644'),))
        self.assertEqual(bad, 'PROTECTED_PATH')
        self.assertEqual(validate_paths(['studio/v160/b.txt'], ['studio/v16/'],
            (GitEntry('studio/v160/b.txt','100644'),), (GitEntry('studio/v160/b.txt','100644'),)), 'INVALID_SCOPE')

    def test_symlink_submodule_and_mode_bypass(self):
        for mode in ('120000', '160000'):
            self.g.proposed_entries = (GitEntry(FILE, mode),)
            self.assertEqual(self.prepare(), 'INVALID_PATH')
        self.g.proposed_entries = (GitEntry(FILE, '100644'),)
        self.g.extras = (GitEntry('studio/v16/link', '120000'),)
        self.g.inspected_paths = ('studio/v16/link/secret.txt',)
        intent = copy.deepcopy(self.intent)
        intent['target']['changed_paths'] = ['studio/v16/link/secret.txt']
        self.assertEqual(self.gw.prepare(intent, PATCH, self.principal)['status'], 'BLOCKED')

    def test_test_evidence_must_match_trusted_ci(self):
        self.assertEqual(self.prepare(evidence_sha256="a"*64), "QA_PENDING")

    def test_case_collision(self):
        self.g.extras = (GitEntry('studio/v16/EXAMPLE.txt', '100644'),)
        self.assertEqual(self.prepare(), 'INVALID_PATH')
        self.g.extras = (GitEntry('studio/V16/other.txt', '100644'),)
        self.assertEqual(self.prepare(), 'INVALID_PATH')

    def test_patch_must_be_trusted_provider_inspected(self):
        self.g.inspected_paths = ('studio/v16/other.txt',)
        self.assertEqual(self.prepare(), 'SOURCE_SHA_MISMATCH')
        self.assertEqual(len(self.a.reserved), 0)

    def test_tampered_artifact_and_qa_subject(self):
        self.assertEqual(self.prepare(artifact_sha256='e'*64), 'SOURCE_SHA_MISMATCH')
        altered = copy.deepcopy(self.intent)
        altered['independent_qa']['reviewer_principal_id'] = 'fake-qa'
        self.assertEqual(self.gw.prepare(altered, PATCH, self.principal)['denial_code'], 'QA_PENDING')

    def test_qa_self_review_and_missing(self):
        self.a.state = replace(self.a.state, qa=replace(self.a.state.qa, reviewer_principal_id='principal-worker-001'))
        self.assertEqual(self.prepare(), 'SELF_REVIEW')
        self.a.state = replace(self.a.state, qa=None)
        self.assertEqual(self.prepare(), 'QA_PENDING')

    def test_idempotency_replay_and_conflict(self):
        self.assertEqual(self.prepare(), 'PREPARED')
        self.assertEqual(self.prepare(), 'PREPARED')
        altered = copy.deepcopy(self.intent)
        altered['requested_at'] = '2026-10-27T20:01:00Z'
        self.assertEqual(self.gw.prepare(altered, PATCH, self.principal)['denial_code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(len(self.a.reserved), 1)

    def test_atomic_revocation_race(self):
        self.a.fail_atomic = True
        self.assertEqual(self.prepare(), 'RETRYABLE_BLOCKED')
        self.assertFalse(self.a.reserved)

    def test_missing_authority_and_audit_safe(self):
        self.a.missing = True
        self.assertEqual(self.evaluate(), 'GATEWAY_UNAVAILABLE')
        self.assertEqual(self.prepare(), 'GATEWAY_UNAVAILABLE')
        self.assertNotIn('artifact_base64', json.dumps(self.audit.events))
        self.assertNotIn('gateway-session-opaque-001', json.dumps(self.audit.events))

    def test_successful_provider_readback_is_required(self):
        self.assertEqual(self.prepare(), 'PREPARED')
        first = self.gw.reconcile(self.intent['publication_id'], self.intent['idempotency_key'], self.principal)
        self.assertEqual(first['status'], 'EXTERNAL_OBSERVED')
        self.assertFalse(self.a.effects)
        self.g.read = dict(repo_id=self.intent['target']['repo_id'], work_branch=BRANCH,
                           tree_sha256=TREE, artifact_sha256=H, commit_sha='b'*40)
        result = self.gw.reconcile(self.intent['publication_id'], self.intent['idempotency_key'], self.principal)
        validate(result, 'PUBLISH-V16/READBACK/1')
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertEqual(len(self.a.effects), 1)

    def test_readback_mismatch_and_failed_reconcile(self):
        self.assertEqual(self.prepare(), 'PREPARED')
        self.g.read = dict(repo_id='intruder/repo', work_branch=BRANCH, tree_sha256=TREE,
                           artifact_sha256=H, commit_sha='b'*40)
        self.assertEqual(self.gw.reconcile(self.intent['publication_id'], self.intent['idempotency_key'], self.principal)['denial_code'], 'SOURCE_SHA_MISMATCH')
        self.assertFalse(self.a.effects)
        self.g.read['repo_id'] = self.intent['target']['repo_id']
        self.a.fail_atomic = True
        self.assertEqual(self.gw.reconcile(self.intent['publication_id'], self.intent['idempotency_key'], self.principal)['status'], 'BLOCKED')

    def test_unknown_schema_and_closed_request(self):
        self.assertEqual(self.evaluate(schema_id='AUTH-V16/EVALUATE/2'), 'UNKNOWN_SCHEMA_VERSION')
        self.assertEqual(self.evaluate(extra='forgery'), 'MALFORMED_REQUEST')

    def test_drive_revision_cas_and_readback(self):
        intent = copy.deepcopy(POS['publish-intent-drive'])
        content = b'exported drive bytes'
        digest = hashlib.sha256(content).hexdigest()
        intent['artifact_sha256'] = digest
        intent['target']['exported_sha256'] = digest
        intent['independent_qa']['subject_sha256'] = digest
        updated_grant = dict(self.a.state.grant, allowed_operations=['SOURCE_BRANCH_UPDATE', 'DRIVE_REVISION_UPDATE'])
        self.a.state = replace(self.a.state, grant=updated_grant, qa=replace(self.a.state.qa, subject_sha256=digest))
        self.assertEqual(self.gw.prepare(intent, content, self.principal)['status'], 'PREPARED')
        self.assertFalse(self.a.effects)
        self.d.rev = 'rev-18'
        self.assertEqual(self.gw.prepare(intent, content, self.principal)['denial_code'], 'DOCUMENT_CONFLICT')
        self.d.read = dict(file_id=intent['target']['file_id'], exported_sha256=digest, revision='rev-18')
        result = self.gw.reconcile(intent['publication_id'], intent['idempotency_key'], self.principal)
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertTrue(self.a.effects)

class CredentialsAndTransportTests(unittest.TestCase):
    def test_external_mtls_mapping_and_no_fallback(self):
        self.assertRaises(ValueError, MTLSIdentity, {})
        mapping = MTLSIdentity({'a'*64: 'principal-worker-001'})
        self.assertIsNone(mapping.authenticate(object()))
        self.assertRaises(ValueError, server_context, certfile='', keyfile='', cafile='')


    def test_live_loopback_mtls_certificate_identity(self):
        """Exercise TLS chain + pinned leaf + handler, without outside network."""
        import ssl
        import tempfile
        import threading
        import urllib.request
        import urllib.error
        from http.server import ThreadingHTTPServer
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        from datetime import timedelta
        from studio_v16.gateway.transport import make_handler
        def key(): return rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime(2026, 10, 27, tzinfo=timezone.utc)
        ca_key = key()
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Unit Test CA')])
        ca_cert = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
            .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False), critical=True)
            .sign(ca_key, hashes.SHA256()))
        def issued(common_name):
            k = key()
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
            cert = (x509.CertificateBuilder().subject_name(name).issuer_name(ca_name)
                .public_key(k.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=365))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(common_name)]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))
            return k, cert
        server_key, server_cert = issued('localhost')
        client_key, client_cert = issued('allowed-client')
        _, other_cert = issued('unauthorized-client')
        pem = lambda cert: cert.public_bytes(serialization.Encoding.PEM)
        def private(k): return k.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
        gw, _, _, _, _, req, _ = make_suite()
        identity = MTLSIdentity({hashlib.sha256(client_cert.public_bytes(
            serialization.Encoding.DER)).hexdigest(): 'principal-worker-001'})
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            for name, data in [('ca.pem', pem(ca_cert)), ('server.pem', pem(server_cert)),
                               ('server.key', private(server_key)), ('client.pem', pem(client_cert)),
                               ('client.key', private(client_key))]:
                (root/name).write_bytes(data)
            sslctx = server_context(certfile=str(root/'server.pem'), keyfile=str(root/'server.key'),
                                    cafile=str(root/'ca.pem'))
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(gw, identity))
            server.socket = sslctx.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f'https://localhost:{server.server_address[1]}/v1/auth/evaluate'
                raw = json.dumps(req).encode()
                request = urllib.request.Request(url, data=raw,
                    headers={'Content-Type':'application/json'}, method='POST')
                clientctx = ssl.create_default_context(cafile=str(root/'ca.pem'))
                clientctx.load_cert_chain(str(root/'client.pem'), str(root/'client.key'))
                with urllib.request.urlopen(request, context=clientctx, timeout=5) as response:
                    verdict = json.loads(response.read())
                self.assertEqual(verdict['decision'], 'ALLOW')
                forged = dict(req, claimed_principal_id='owner-principal-001')
                with urllib.request.urlopen(urllib.request.Request(url,
                    data=json.dumps(forged).encode(), method='POST'), context=clientctx, timeout=5) as response:
                    self.assertEqual(json.loads(response.read())['denial_code'], 'IDENTITY_UNVERIFIED')
                unauthctx = ssl.create_default_context(cafile=str(root/'ca.pem'))
                with self.assertRaises((urllib.error.URLError, ssl.SSLError)):
                    urllib.request.urlopen(request, context=unauthctx, timeout=5)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_github_app_token_scoped_request(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = rsa_key.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        captured = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return b'{"token":"test-token-not-logged"}'
        def opener(request, timeout):
            captured.append(request)
            return Response()
        creds = GitHubAppCredentials(321, pem, InstallationScope(654, (789,)),
                                      opener=opener, clock=lambda: 1793120000)
        self.assertEqual(creds.installation_token(789), 'test-token-not-logged')
        self.assertEqual(len(captured), 1)
        self.assertEqual(json.loads(captured[0].data), {
            'repository_ids': [789], 'permissions': {'contents': 'write'}})
        self.assertIn('/installations/654/access_tokens', captured[0].full_url)

    def test_github_app_scope_fails_before_issuing_token(self):
        creds = GitHubAppCredentials(123, b'not-a-real-key', InstallationScope(456, (123,)))
        with self.assertRaises(CredentialError):
            creds.installation_token(999)

if __name__ == '__main__':
    unittest.main()
