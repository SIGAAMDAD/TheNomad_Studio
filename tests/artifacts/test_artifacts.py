from __future__ import annotations
from _studio_v1_fixture import sql_for

import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
import wave
import zlib
from dataclasses import asdict

from nomad_studio.artifacts.api import build_component
from nomad_studio.contracts.v1 import CommandEnvelope, QueryEnvelope, Outcome, VerifiedIdentity


NOW = 1_800_000_000_000_000
FOLDERS = {'asset:image': 'drive-images', 'asset:audio': 'drive-audio', 'asset:mesh': 'drive-meshes', 'asset:fmod_bank': 'drive-fmod', 'asset:fmod_project': 'drive-fmod', 'asset:build': 'drive-builds', 'document:design': 'drive-design-documents', 'document:narrative': 'drive-narrative-documents'}


def png_bytes() -> bytes:
    def chunk(tag, payload):
        return struct.pack('>I', len(payload)) + tag + payload + struct.pack('>I', zlib.crc32(tag + payload) & 0xffffffff)
    raw = b'\x00\xff\x00\x00\xff'
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def wav_bytes() -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as wavefile:
        wavefile.setnchannels(1)
        wavefile.setsampwidth(2)
        wavefile.setframerate(44100)
        wavefile.writeframes(b'\x00\x00' * 64)
    return stream.getvalue()


def glb_bytes() -> bytes:
    raw = b'{"asset":{"version":"2.0"}}'
    raw += b' ' * ((-len(raw)) % 4)
    return b'glTF' + struct.pack('<II', 2, 20 + len(raw)) + struct.pack('<I4s', len(raw), b'JSON') + raw


class Store:
    """WG-A StateStore fake with a real SQLite transaction and append_event stub."""
    def __init__(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.in_tx = False
        self.events = []

    def migrate(self, component):
        self.conn.executescript(sql_for('artifacts'))
        self.conn.commit()

    def read(self, fn):
        assert not self.in_tx
        return fn(self)

    def transact(self, fn, *, idempotency_key=None):
        assert not self.in_tx
        self.in_tx = True
        try:
            result = fn(self)
            self.conn.commit()
            return result
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self.in_tx = False

    def fetch_one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, sql, params=()):
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params).rowcount

    def append_event(self, event):
        self.events.append(event)
        return event.event_id

    def close(self):
        self.conn.close()


class Provider:
    """Read-only fake provider: returns actual independent temp-file bytes, not payload bytes."""
    def __init__(self, root, store):
        self.root = root
        self.store = store
        self.revision_override = None
        self.parent_override = None
        self.byte_override = None
        self.raise_describe = False
        self.raise_read = False
        self.drift = False
        self.describe_count = 0
        self.read_count = 0
        self.stream = False

    def describe(self, manifest):
        assert not self.store.in_tx, 'external network call inside state transaction'
        if self.raise_describe: raise ConnectionError('simulated outage')
        self.describe_count += 1
        obj = self.root / manifest['artifact_id']
        return {'provider': manifest['provider'], 'object_id': manifest['object_id'], 'revision': self.revision_override or (manifest['revision'] + '-drift' if self.drift and self.describe_count > 1 else manifest['revision']), 'parent_id': self.parent_override or manifest.get('parent_id'), 'size': obj.stat().st_size}

    def read_exact(self, manifest):
        assert not self.store.in_tx, 'external network call inside state transaction'
        if self.raise_read: raise ConnectionError('simulated read outage')
        self.read_count += 1
        data = self.byte_override if self.byte_override is not None else (self.root / manifest['artifact_id']).read_bytes()
        if self.stream: return iter((data[:7], data[7:]))
        return data


class Review:
    def __init__(self):
        self.requests = []
        self.verdict = 'PASS'
        self.reviewer = 'reviewer'
        self.subject_override = None
        self.fail_once = False
        self.verdict_receipt = 'qa-verdict-1'

    def execute(self, command, identity):
        self.requests.append(command)
        if self.fail_once:
            self.fail_once = False
            raise ConnectionError('timeout after maybe accepted')
        return Outcome('ACCEPTED', 'QA_REQUESTED', receipt_id='qa-req-1')

    def query(self, q, identity):
        return Outcome('ACCEPTED', 'VERDICT', receipt_id=self.verdict_receipt, payload={'verdict': self.verdict, 'artifact_id': q.payload['artifact_id'], 'job_id': q.job_id, 'subject_sha256': self.subject_override or q.payload['subject_sha256'], 'qa_receipt_id': q.payload['qa_receipt_id'], 'reviewer_principal': self.reviewer})


class Publication:
    def __init__(self):
        self.requests = []
        self.fail = False

    def execute(self, command, identity):
        self.requests.append(command)
        if self.fail: raise TimeoutError('ambiguous prepare')
        return Outcome('ACCEPTED', 'PREPARED', receipt_id='pub-prepared-1')


def manifest(data, *, kind='image', filename='hero.png', mime='image/png', artifact_id='hero-v1', provider='drive', parent='drive-images', **updates):
    result = {'artifact_id': artifact_id, 'kind': kind, 'provider': provider, 'object_id': 'provider-obj', 'revision': 'rev-1', 'filename': filename, 'parent_id': parent, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'mime': mime, 'license': 'SPDX:CC0-1.0', 'origin': 'artist-provided:ticket-1'}
    result.update(updates)
    return result


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store()
        self.addCleanup(self.store.close)
        self.provider = Provider(self.root, self.store)
        self.review = Review()
        self.publication = Publication()
        self.deps = {'store': self.store, 'readback': self.provider, 'authorize': lambda identity, scope, resource: True, 'folder_registry': FOLDERS, 'review': self.review, 'publication': self.publication, 'now_us': lambda: NOW}
        self.comp = build_component(self.deps)
        self.store.migrate(self.comp)
        self.ident = VerifiedIdentity('asset-author', 'c1', 'trusted', 'gateway', frozenset({'artifacts:register', 'artifacts:verify', 'artifacts:attach', 'artifacts:read', 'artifacts:publish_intent'}), NOW + 999999, 1)
        self.count = 0

    def command(self, op, payload, job_id=None, key=None):
        self.count += 1
        n = str(self.count)
        return CommandEnvelope('NOMAD/CMD/1', 'req-'+n, 'artifacts', 'artifacts.'+op, key or 'idem-'+n, job_id=job_id, payload=payload)

    def call(self, op, payload, job_id=None, key=None):
        return self.comp.execute(self.command(op, payload, job_id, key), self.ident)

    def put(self, data, **kwargs):
        m = manifest(data, **kwargs)
        (self.root / m['artifact_id']).write_bytes(data)
        self.assertEqual(self.call('register', {'manifest': m}).state, 'ACCEPTED')
        return m

    def complete(self, data=None, **kwargs):
        m = self.put(png_bytes() if data is None else data, **kwargs)
        self.assertEqual(self.call('verify_bytes', {'artifact_id': m['artifact_id']}).code, 'BYTES_VERIFIED')
        return m

    def test_verified_real_binary_and_format_evidence(self):
        m = self.complete()
        validated = self.call('validate_format', {'artifact_id': m['artifact_id']})
        self.assertEqual(validated.code, 'FORMAT_VALIDATED')
        self.assertIn('builtin:.png', validated.payload['validator'])
        proof = self.comp.query(QueryEnvelope('NOMAD/QUERY/1', 'query-1', 'artifacts', 'artifacts.proof', payload={'artifact_id': m['artifact_id']}), self.ident)
        self.assertEqual(proof.payload['byte_proof_sha256'], m['sha256'])
        self.assertEqual(proof.payload['readback_evidence']['readback']['revision'], 'rev-1')
        self.assertEqual(proof.payload['state'], 'VALIDATED')
        self.assertEqual(len(self.store.events), 3)

    def test_streaming_real_temp_bytes(self):
        self.provider.stream = True
        self.complete()
        self.assertEqual(self.provider.read_count, 1)

    def test_fake_hash_rejected(self):
        m = self.put(png_bytes(), sha256='a' * 64)
        bad = self.call('verify_bytes', {'artifact_id': m['artifact_id']})
        self.assertEqual((bad.state, bad.code), ('CONFLICT', 'SOURCE_SHA_MISMATCH'))
        self.assertEqual(self.store.fetch_one('SELECT state FROM artifacts_manifests WHERE artifact_id=?', ('hero-v1',))['state'], 'REGISTERED')

    def test_zero_bytes_rejected(self):
        raw = manifest(b'')
        self.assertEqual(self.call('register', {'manifest': raw}).code, 'INVALID_SCHEMA')

    def test_disguised_markdown_rejected_for_real_assets(self):
        for i, (kind, extension, mime, parent) in enumerate((('mesh', 'glb', 'model/gltf-binary', 'drive-meshes'), ('fmod_bank', 'bank', 'application/x-fmod-bank', 'drive-fmod'), ('audio', 'wav', 'audio/wav', 'drive-audio'))):
            with self.subTest(kind=kind):
                content = b'# A plain Markdown description is not a binary asset\n'
                extra = {'fmod_contract': {'bank_name': 'Master', 'events': [{'path':'event:/impact'}], 'parameters': [], 'audio_review_required': True}} if kind == 'fmod_bank' else {}
                m = self.put(content, kind=kind, filename='fake.'+extension, mime=mime, artifact_id='fake-'+str(i), parent=parent, **extra)
                self.assertEqual(self.call('verify_bytes', {'artifact_id': m['artifact_id']}).state, 'ACCEPTED')
                val = self.call('validate_format', {'artifact_id': m['artifact_id']})
                # FMOD opaque format has no validator and is therefore not claimed as a valid bank.
                self.assertIn(val.code, ('INVALID_FORMAT', 'BLOCKED_MISSING_TOOL'))
                self.assertNotEqual(val.state, 'ACCEPTED')

    def test_wrong_drive_parent_and_revision(self):
        m = manifest(png_bytes(), parent='generic-migration-folder')
        self.assertEqual(self.call('register', {'manifest': m}).code, 'INVALID_SCOPE')
        m = self.put(png_bytes())
        self.provider.revision_override = 'different-revision'
        res = self.call('verify_bytes', {'artifact_id': m['artifact_id']})
        self.assertEqual((res.state, res.code), ('CONFLICT', 'DOCUMENT_CONFLICT'))
        self.provider.revision_override = None
        self.provider.parent_override = 'other-parent'
        self.assertEqual(self.call('verify_bytes', {'artifact_id': m['artifact_id']}).code, 'DOCUMENT_CONFLICT')

    def test_mid_read_revision_race(self):
        m = self.put(png_bytes())
        self.provider.drift = True
        self.assertEqual(self.call('verify_bytes', {'artifact_id': m['artifact_id']}).code, 'DOCUMENT_CONFLICT')

    def test_malformed_manifest_closed_fields_and_wrong_mime(self):
        for patch in ({'arbitrary_claim':'PASS'}, {'mime':'text/markdown'}, {'sha256':'SHA256'}, {'filename':'../hero.png'}, {'provider': 'unapproved'}):
            with self.subTest(patch=patch):
                m = manifest(png_bytes(), **patch)
                self.assertEqual(self.call('register', {'manifest': m}).state, 'DENIED')
        self.assertEqual(self.call('register', {'manifest': manifest(png_bytes()), 'unknown': 1}).code, 'INVALID_SCHEMA')

    def test_missing_semantic_validator_is_not_run(self):
        self.complete(glb_bytes(), kind='mesh', filename='hero.glb', mime='model/gltf-binary', parent='drive-meshes')
        res = self.call('validate_format', {'artifact_id': 'hero-v1'})
        self.assertEqual((res.state, res.code), ('NOT_RUN', 'BLOCKED_MISSING_TOOL'))
        self.assertEqual(self.store.fetch_one('SELECT state FROM artifacts_manifests WHERE artifact_id=?', ('hero-v1',))['state'], 'VERIFIED')

    def test_external_semantic_validator_evidence_required(self):
        self.complete(glb_bytes(), kind='mesh', filename='hero.glb', mime='model/gltf-binary', parent='drive-meshes')
        self.comp.tools = {'mesh': lambda data, info: True}
        self.assertEqual(self.call('validate_format', {'artifact_id': 'hero-v1'}).code, 'INVALID_FORMAT')
        self.comp.tools = {'mesh': lambda data, info: {'valid': True, 'tool_version': 'gltf-validator-test-v1'}}
        self.assertEqual(self.call('validate_format', {'artifact_id': 'hero-v1'}).code, 'FORMAT_VALIDATED')

    def test_document_dedicated_drive_folder(self):
        data = b'# Verified game design\n'
        self.complete(data, kind='document', filename='design.md', mime='text/markdown', parent='drive-design-documents', document_domain='design')
        self.assertEqual(self.call('validate_format', {'artifact_id':'hero-v1'}).code, 'FORMAT_VALIDATED')
        malformed = manifest(data, kind='document', filename='design.md', mime='text/markdown', parent='drive-images', artifact_id='doc-wrong', document_domain='design')
        self.assertEqual(self.call('register', {'manifest':malformed}).code, 'INVALID_SCOPE')

    def test_audio_wav_binary_builtin(self):
        self.complete(wav_bytes(), kind='audio', filename='sound.wav', mime='audio/wav', parent='drive-audio')
        self.assertEqual(self.call('validate_format', {'artifact_id':'hero-v1'}).code, 'FORMAT_VALIDATED')

    def test_fmod_contract_requires_audio_review_and_tool(self):
        raw = b'BANK\x00\x00\x00sample-binary'
        contract = {'bank_name':'Master', 'events':[{'path':'event:/weapon/shoot'}], 'parameters':[{'name':'Intensity','type':'continuous','minimum':0,'maximum':1,'default':0.5}], 'audio_review_required':True}
        self.complete(raw, kind='fmod_bank', filename='Master.bank', mime='application/x-fmod-bank', parent='drive-fmod', fmod_contract=contract)
        self.assertEqual(self.call('validate_format', {'artifact_id': 'hero-v1'}).code, 'BLOCKED_MISSING_TOOL')
        self.comp.tools = {'fmod_bank': lambda d, info: {'valid':True,'tool_version':'test-fmod-inspector-1'}}
        self.assertEqual(self.call('validate_format', {'artifact_id': 'hero-v1'}).code, 'FORMAT_VALIDATED')
        self.assertEqual(self.call('attach', {'artifact_id': 'hero-v1', 'job_id': 'job-audio'}, job_id='job-audio').code, 'QA_REQUESTED')
        self.assertTrue(self.review.requests[-1].payload['audio_review_required'])
        contract['audio_review_required'] = False
        altered = manifest(raw, kind='fmod_bank', filename='Master.bank', mime='application/x-fmod-bank', parent='drive-fmod', artifact_id='invalid-bank', fmod_contract=contract)
        self.assertEqual(self.call('register', {'manifest':altered}).code, 'INVALID_SCHEMA')

    def test_qa_and_publication_are_separate_no_direct_dispatch(self):
        m = self.complete()
        self.call('validate_format', {'artifact_id': m['artifact_id']})
        att = self.call('attach', {'artifact_id': m['artifact_id'], 'job_id': 'job-1'}, job_id='job-1')
        self.assertEqual(att.code, 'QA_REQUESTED')
        self.assertEqual(self.review.requests[-1].operation, 'review.request_qa')
        pub = self.call('proof', {'artifact_id': m['artifact_id'], 'job_id': 'job-1'}, job_id='job-1')
        self.assertEqual(pub.code, 'PUBLICATION_INTENT_PREPARED')
        self.assertEqual(pub.payload['publication_state'], 'PREPARED_NOT_PUBLISHED')
        self.assertEqual(self.publication.requests[-1].operation, 'publication.prepare')
        self.assertEqual(self.publication.requests[-1].payload['source_sha256'], m['sha256'])
        self.assertEqual(self.publication.requests[-1].payload['expected_revision'], m['revision'])
        self.assertNotIn('publication.dispatch', [x.operation for x in self.publication.requests])
        self.assertEqual(self.call('proof', {'artifact_id': m['artifact_id'], 'job_id': 'job-1'}, job_id='job-1').code, 'PUBLICATION_ALREADY_PREPARED')
        self.assertEqual(len(self.publication.requests), 1)

    def test_self_review_or_mismatched_sha_blocks_publication(self):
        m = self.complete()
        self.call('validate_format', {'artifact_id': m['artifact_id']})
        self.call('attach', {'artifact_id': m['artifact_id'], 'job_id': 'job-1'}, job_id='job-1')
        self.review.reviewer = 'asset-author'
        self.assertEqual(self.call('proof', {'artifact_id':'hero-v1','job_id':'job-1'},job_id='job-1').code, 'SELF_REVIEW')
        self.review.reviewer = 'independent-reviewer'
        self.review.subject_override = 'b' * 64
        self.assertEqual(self.call('proof', {'artifact_id':'hero-v1','job_id':'job-1'},job_id='job-1').code, 'DOCUMENT_CONFLICT')
        self.review.subject_override = None
        self.review.verdict = 'REWORK'
        self.assertEqual(self.call('proof', {'artifact_id':'hero-v1','job_id':'job-1'},job_id='job-1').code, 'QA_PENDING')
        self.assertEqual(self.publication.requests, [])

    def test_ambiguous_qa_request_retries_without_duplicate_attachment(self):
        m = self.complete()
        self.call('validate_format', {'artifact_id': m['artifact_id']})
        self.review.fail_once = True
        first = self.call('attach', {'artifact_id':'hero-v1','job_id':'job-1'},job_id='job-1')
        self.assertEqual(first.state, 'UNKNOWN')
        second = self.call('attach', {'artifact_id':'hero-v1','job_id':'job-1'},job_id='job-1')
        self.assertEqual(second.code, 'QA_REQUESTED')
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM artifacts_attachments')), 1)
        self.assertEqual(self.review.requests[0].idempotency_key, self.review.requests[1].idempotency_key)

    def test_provider_faults_and_missing_port(self):
        m = self.put(png_bytes())
        self.provider.raise_read = True
        self.assertEqual(self.call('verify_bytes', {'artifact_id': m['artifact_id']}).state, 'UNKNOWN')
        self.provider.raise_read = False
        self.assertEqual(self.call('verify_bytes', {'artifact_id': m['artifact_id']}).state, 'ACCEPTED')
        self.comp.readback = None
        self.assertEqual(self.call('validate_format', {'artifact_id':'hero-v1'}).code, 'NOT_CONFIGURED')

    def test_idempotency_collision(self):
        m = manifest(png_bytes())
        cmd = self.command('register', {'manifest': m}, key='same-key')
        first = self.comp.execute(cmd,self.ident)
        second = self.comp.execute(cmd,self.ident)
        self.assertEqual((first.code, first.receipt_id),(second.code, second.receipt_id))
        altered = dict(m, origin='different')
        self.assertEqual(self.comp.execute(self.command('register', {'manifest': altered}, key='same-key'), self.ident).code, 'IDEMPOTENCY_CONFLICT')

    def test_expired_or_untrusted_authorization_denied(self):
        command = self.command('register', {'manifest':manifest(png_bytes())})
        expired = VerifiedIdentity('actor','c','issuer','gateway',self.ident.scopes,NOW-1,1)
        self.assertEqual(self.comp.execute(command,expired).code, 'EXPIRED_GRANT')
        no_scope = VerifiedIdentity('actor','c','issuer','gateway',frozenset(),NOW+99999,1)
        self.assertEqual(self.comp.execute(command,no_scope).code, 'INVALID_SCOPE')
        self.comp.authorize = lambda identity, scope, resource: False
        self.assertEqual(self.comp.execute(command,self.ident).code, 'INVALID_SCOPE')

    def test_attach_no_review_records_local_not_external_pass(self):
        self.complete()
        self.call('validate_format', {'artifact_id': 'hero-v1'})
        self.comp.review = None
        o = self.call('attach', {'artifact_id':'hero-v1','job_id':'job1'},job_id='job1')
        self.assertEqual((o.state,o.code), ('NOT_RUN','NOT_CONFIGURED'))
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM artifacts_attachments')), 1)

    def test_bytes_change_after_registration_rejected(self):
        self.complete()
        (self.root / 'hero-v1').write_bytes(b'evil replacement bytes')
        bad = self.call('validate_format', {'artifact_id': 'hero-v1'})
        self.assertIn(bad.code, ('DOCUMENT_CONFLICT', 'SOURCE_SHA_MISMATCH'))
        self.assertNotEqual(bad.state,'ACCEPTED')

    def test_size_ceiling(self):
        data = png_bytes()
        self.put(data)
        self.comp.max_bytes = len(data)-1
        self.assertEqual(self.call('verify_bytes', {'artifact_id': 'hero-v1'}).code, 'BACKPRESSURE')

    def test_closed_commands_query_and_no_text_proof(self):
        self.assertEqual(self.call('verify_bytes', {'artifact_id':'hero-v1','fake_proof':'PASS'}).code,'INVALID_SCHEMA')
        bad = self.comp.query(QueryEnvelope('NOMAD/QUERY/1','r','artifacts','artifacts.proof',payload={'artifact_id':'hero-v1','extra':True}),self.ident)
        self.assertEqual(bad.code,'INVALID_SCHEMA')
        self.assertEqual(self.call('proof', {'artifact_id':'hero-v1','job_id':'job1'},job_id='job1').state,'DENIED')

    def test_missing_publication_port_truthful(self):
        self.complete()
        self.call('validate_format', {'artifact_id': 'hero-v1'})
        self.call('attach', {'artifact_id':'hero-v1','job_id':'job1'},job_id='job1')
        self.comp.publication = None
        self.assertEqual(self.call('proof', {'artifact_id':'hero-v1','job_id':'job1'},job_id='job1').state,'NOT_RUN')

    def test_publication_timeout_never_verified(self):
        self.complete()
        self.call('validate_format', {'artifact_id': 'hero-v1'})
        self.call('attach', {'artifact_id':'hero-v1','job_id':'job1'},job_id='job1')
        self.publication.fail = True
        self.assertEqual(self.call('proof', {'artifact_id':'hero-v1','job_id':'job1'},job_id='job1').state,'UNKNOWN')
        self.assertIsNone(self.store.fetch_one('SELECT publication_receipt_id FROM artifacts_attachments')['publication_receipt_id'])


# Additional independent fault-injection suite.
class AdditionalArtifactTests(unittest.TestCase):
    """Additional regression fixtures reuse the core suite methods without repeating core tests."""
    setUp = ArtifactTests.setUp
    command = ArtifactTests.command
    call = ArtifactTests.call
    put = ArtifactTests.put
    complete = ArtifactTests.complete

    def test_cached_validation_does_not_skip_recheck(self):
        self.complete()
        self.assertEqual(self.call('validate_format', {'artifact_id':'hero-v1'}).state, 'ACCEPTED')
        self.provider.revision_override = 'stale'
        self.assertEqual(self.call('verify_bytes', {'artifact_id':'hero-v1'}).code, 'DOCUMENT_CONFLICT')
        self.assertEqual(self.call('validate_format', {'artifact_id':'hero-v1'}).code, 'DOCUMENT_CONFLICT')
        self.assertEqual(self.call('attach', {'artifact_id':'hero-v1','job_id':'j'},job_id='j').code, 'DOCUMENT_CONFLICT')

    def test_stale_bytes_fence_publication_intent(self):
        self.complete()
        self.call('validate_format', {'artifact_id':'hero-v1'})
        self.call('attach', {'artifact_id':'hero-v1','job_id':'j'},job_id='j')
        self.provider.revision_override = 'changed'
        self.assertEqual(self.call('proof', {'artifact_id':'hero-v1','job_id':'j'},job_id='j').code, 'DOCUMENT_CONFLICT')
        self.assertEqual(self.publication.requests, [])

    def test_format_tool_crash_is_not_run(self):
        self.complete(glb_bytes(),kind='mesh',filename='hero.glb',mime='model/gltf-binary',parent='drive-meshes')
        def broken(data,info):
            raise FileNotFoundError('mesh inspector binary missing')
        self.comp.tools = {'mesh':broken}
        o = self.call('validate_format', {'artifact_id':'hero-v1'})
        self.assertEqual((o.state,o.code),('NOT_RUN','TOOL_FAILED'))

    def test_crc_tampering_rejected(self):
        source = bytearray(png_bytes())
        source[40] ^= 0x44
        self.complete(bytes(source))
        result = self.call('validate_format', {'artifact_id':'hero-v1'})
        self.assertEqual((result.state,result.code),('DENIED','INVALID_FORMAT'))

    def test_unauthorized_metadata_not_persisted(self):
        self.complete()
        proof = self.comp.query(QueryEnvelope('NOMAD/QUERY/1', 'q1', 'artifacts','artifacts.proof',payload={'artifact_id':'hero-v1'}),self.ident)
        self.assertNotIn('token', json.dumps(proof.payload))

    def test_unexpected_provider_metadata_blocked(self):
        self.put(png_bytes())
        original = self.provider.describe
        def malicious(args):
            observed = original(args)
            observed['access_token'] = 'secret-must-not-be-saved'
            return observed
        self.provider.describe = malicious
        out = self.call('verify_bytes', {'artifact_id':'hero-v1'})
        self.assertEqual(out.code,'PROVIDER_UNKNOWN')
        proof = self.comp.query(QueryEnvelope('NOMAD/QUERY/1', 'q2', 'artifacts','artifacts.proof',payload={'artifact_id':'hero-v1'}),self.ident)
        self.assertNotIn('secret-must-not-be-saved',str(proof.payload))

class ProviderFormatIntegrationTests(unittest.TestCase):
    """Additional provider/format coverage with isolated real backing files."""
    setUp = ArtifactTests.setUp
    command = ArtifactTests.command
    call = ArtifactTests.call
    put = ArtifactTests.put
    complete = ArtifactTests.complete

    def test_github_exact_blob_read(self):
        data = png_bytes()
        m = self.put(data, provider='github', parent=None, object_id='art/hero.png')
        self.assertEqual(self.call('verify_bytes', {'artifact_id':m['artifact_id']}).code,'BYTES_VERIFIED')
        self.assertEqual(self.call('validate_format', {'artifact_id':m['artifact_id']}).code,'FORMAT_VALIDATED')
        bad = manifest(data, artifact_id='bad-github', provider='github', parent=None, object_id='content/../token')
        self.assertEqual(self.call('register', {'manifest':bad}).code,'INVALID_PATH')

    def test_lfs_readback(self):
        data = png_bytes()
        m = self.put(data, provider='lfs', parent=None, object_id=hashlib.sha256(data).hexdigest())
        self.assertEqual(self.call('verify_bytes', {'artifact_id':m['artifact_id']}).code,'BYTES_VERIFIED')

    def test_real_docx_file_structure(self):
        buf = io.BytesIO()
        import zipfile
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr('[Content_Types].xml','<Types></Types>')
            z.writestr('word/document.xml','<w:document/>')
        self.complete(buf.getvalue(),kind='document',filename='narrative.docx',mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',parent='drive-narrative-documents',document_domain='narrative')
        self.assertEqual(self.call('validate_format', {'artifact_id':'hero-v1'}).code,'FORMAT_VALIDATED')

    def test_zip_traversal_denied_before_build_validation(self):
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:z.writestr('../evil.txt','hazard')
        self.complete(buf.getvalue(),kind='build',filename='build.zip',mime='application/zip',parent='drive-builds')
        self.comp.tools = {'build':lambda b,m: {'valid':True,'tool_version':'CI-runner-v1'}}
        self.assertEqual(self.call('validate_format',{'artifact_id':'hero-v1'}).code,'INVALID_FORMAT')

    def test_proof_request_idempotency_conflict(self):
        self.complete()
        self.call('validate_format',{'artifact_id':'hero-v1'})
        self.call('attach',{'artifact_id':'hero-v1','job_id':'job1'},job_id='job1')
        cmd = self.command('proof',{'artifact_id':'hero-v1','job_id':'job1'},job_id='job1',key='proof-one')
        self.assertEqual(self.comp.execute(cmd,self.ident).code, 'PUBLICATION_INTENT_PREPARED')
        self.assertEqual(self.comp.execute(cmd,self.ident).code, 'PUBLICATION_INTENT_PREPARED')
        another = self.command('proof',{'artifact_id':'hero-v1','job_id':'jobX'},job_id='jobX',key='proof-one')
        # Same key cannot be used for a different job, even if no such attachment exists.
        self.assertEqual(self.comp.execute(another,self.ident).code, 'IDEMPOTENCY_CONFLICT')

    def test_wrong_job_scope_cannot_attach(self):
        self.complete()
        self.call('validate_format',{'artifact_id':'hero-v1'})
        self.assertEqual(self.call('attach', {'artifact_id':'hero-v1','job_id':'job-2'},job_id='job-1').code,'INVALID_SCOPE')
        self.assertEqual(self.store.fetch_all('SELECT * FROM artifacts_attachments'), [])

    def test_bare_operation_name_rejected(self):
        cmd = CommandEnvelope('NOMAD/CMD/1','bare','artifacts','register','key',payload={'manifest':manifest(png_bytes())})
        self.assertEqual(self.comp.execute(cmd,self.ident).code,'MALFORMED_REQUEST')


if __name__ == '__main__':
    unittest.main()
