"""WG-G Tier A tests. No live GitHub/Drive/CI or ChatGPT browser contact."""
import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from studio.integrations import (Artifact, ArtifactStore, BuildTarget, Drive, EffectIntent,
    EffectReconciler, GitHub, GitHubActions, GitHubChannel, IntegrationError,
    PackageVerifier, ProjectBinding, RemoteUnknown, canonical, sha256, validate_relay, validate_receipt, GitHubSource, RetryPolicy, GOperatorPorts, PreparedBuild)
from studio.integrations.common import parse_canonical
from studio.integrations.connectors import ConnectorService

SHA0 = 'a'*40
SHA1 = 'b'*40
SHA2 = 'c'*40
TOKEN = lambda: 'fixture-token-not-real'


def envelope(mid='msg1', project='p1', recipient='s1', kind='NOTE', from_id='s1'):
    return canonical(dict(protocol_version='1', message_id=mid, project_id=project, channel_id='chan',
        from_shift_id=from_id, to_shift_id=recipient, task_id='task1', lease_generation=1,
        kind=kind, issued_at='2026-10-30T00:00:00Z', payload_digest='d'*64, payload_ref='sha256:'+'d'*64,
        canon_snapshot_id='canon1', contract_digest='e'*64))


def receipt(mid='msg1', rid='r1', project='p1', recipient='s1'):
    return canonical(dict(protocol_version='1', receipt_id=rid, project_id=project, channel_id='chan',
        message_id=mid, recipient_shift_id=recipient, kind='SEEN', checkpoint_digest=None,
        issued_at='2026-10-30T00:00:00Z', observed_message_sha256='b'*64, contract_digest='e'*64))


class FakeGitHubHTTP:
    def __init__(self):
        self.head = SHA0
        self.snapshot = {SHA0: {}}
        self.parent = {}
        self.truncated = False
        self.partial = False
        self.timeout_after_write = False
        self.mutations = 0
        self.runs = []
        self.calls = []

    def inject(self, path, data, commit=SHA1):
        self.snapshot[commit] = dict(self.snapshot[self.head], **{path: data})
        self.parent[commit] = self.head
        self.head = commit

    def request(self, method, url, headers, body=None):
        self.calls.append((method, url))
        parsed = urlsplit(url)
        p = unquote(parsed.path)
        q = parse_qs(parsed.query)
        def ok(data, code=200):
            return code, {}, json.dumps(data).encode()
        if method == 'GET' and '/git/ref/heads/' in p:
            return ok({'object': {'sha': self.head}})
        if method == 'GET' and '/git/commits/' in p:
            sha = p.rsplit('/', 1)[-1]
            if sha not in self.snapshot:
                return ok({'message': 'missing'}, 404)
            return ok({'tree': {'sha': sha}})
        if method == 'GET' and '/git/trees/' in p:
            sha = p.rsplit('/', 1)[-1]
            entries = []
            for path, data in self.snapshot[sha].items():
                gitblob = hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()
                entries.append({'path': path, 'type': 'blob', 'sha': gitblob})
            return ok({'truncated': self.truncated, 'tree': entries})
        if method == 'GET' and '/compare/' in p:
            base, tip = p.rsplit('/', 1)[-1].split('...')
            if base == tip:
                status = 'identical'
            else:
                node, status = tip, 'diverged'
                while node in self.parent:
                    node = self.parent[node]
                    if node == base:
                        status = 'ahead'
                        break
            return ok({'status': status})
        if '/contents/' in p:
            path = p.split('/contents/', 1)[1]
            if method == 'GET':
                sha = q.get('ref', [self.head])[0]
                data = self.snapshot[sha].get(path)
                if data is None:
                    return ok({'message': 'missing'}, 404)
                gitblob = hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()
                if self.partial:
                    data = data[:-1]
                return ok({'type': 'file', 'encoding': 'base64', 'sha': gitblob,
                           'content': base64.b64encode(data).decode()})
            if method == 'PUT':
                if path in self.snapshot[self.head]:
                    return ok({'message': 'already exists'}, 422)
                data = base64.b64decode(json.loads(body)['content'])
                self.inject(path, data, SHA1 if self.head == SHA0 else SHA2)
                self.mutations += 1
                if self.timeout_after_write:
                    raise RemoteUnknown('simulated post-write timeout')
                return ok({'commit': {'sha': self.head}}, 201)
        if p.endswith('/dispatches') and method == 'POST':
            return 204, {}, b''
        if p.endswith('/runs') and method == 'GET':
            return ok({'workflow_runs': self.runs})
        if '/actions/runs/' in p:
            return ok(self.runs[0])
        if p.endswith('/user'):
            return ok({'login': 'configured-login'})
        return ok({'message': 'unexpected path'}, 404)


class FakeDriveHTTP:
    def __init__(self):
        self.files = {}
        self.timeout = False
        self.write_count = 0

    def request(self, method, url, headers, body=None):
        p = urlsplit(url).path
        if p.endswith('/about'):
            return 200, {}, json.dumps({'user': {'permissionId': 'verified-google-id'}}).encode()
        if p.endswith('/files') and method == 'GET':
            return 200, {}, json.dumps({'files': [v['meta'] for v in self.files.values()]}).encode()
        if method == 'POST' and p.endswith('/upload/drive/v3/files'):
            boundary = b'--nomad_studio_bytes_boundary'
            sections = body.split(boundary)
            metadata = json.loads(sections[1].split(b'\r\n\r\n', 1)[1].strip())
            data = sections[2].split(b'\r\n\r\n', 1)[1].removesuffix(b'\r\n')
            fid = 'file1'
            self.files[fid] = {'meta': {'id': fid, 'parents': metadata['parents'],
                                      'appProperties': metadata['appProperties']}, 'body': data}
            self.write_count += 1
            if self.timeout:
                raise RemoteUnknown('post-Drive write timeout')
            return 200, {}, json.dumps({'id': fid}).encode()
        if '/files/' in p and method == 'GET':
            fid = p.rsplit('/', 1)[-1]
            if fid not in self.files:
                return 404, {}, b'{}'
            obj = self.files[fid]
            if 'alt=media' in url:
                return 200, {}, obj['body']
            return 200, {}, json.dumps(obj['meta']).encode()
        return 404, {}, b'{}'


class IntentMemory:
    def __init__(self, intent):
        self.intent = intent
        self.observed = []

    def load(self, project_id, effect_id):
        return self.intent

    def store_observation(self, project_id, observation, expected_state):
        self.observed.append((project_id, observation, expected_state))


class ConnectorMemory:
    def __init__(self):
        self.records = []

    def record_connector(self, *args):
        self.records.append(args)


class TestRelay(unittest.TestCase):
    def test_envelope_exact_digest(self):
        raw = envelope()
        parsed, d = validate_relay(raw, project_id='p1', recipient='s1')
        self.assertEqual(d, sha256(raw))
        self.assertEqual(parsed['message_id'], 'msg1')

    def test_receipt_exact_digest(self):
        parsed, d = validate_receipt(receipt(), project_id='p1', recipient='s1')
        self.assertEqual(parsed['kind'], 'SEEN')
        self.assertEqual(d, sha256(receipt()))

    def test_cross_project_and_recipient(self):
        for args in ({'project_id': 'p2'}, {'project_id': 'p1', 'recipient': 's2'}):
            with self.subTest(args=args), self.assertRaises(IntegrationError):
                validate_relay(envelope(), **args)

    def test_hello_cannot_enroll_other_session(self):
        with self.assertRaises(IntegrationError):
            validate_relay(envelope(kind='HELLO', from_id='s2'), project_id='p1')
        self.assertEqual(validate_relay(envelope(kind='HELLO'), project_id='p1')[0]['kind'], 'HELLO')

    def test_canonical_and_duplicate_keys(self):
        for data in [b'{"a":1,"a":2}\n', b'{"z":1,"a":2}\n', b'{"a":1}\n', b'not json']:
            with self.subTest(data=data), self.assertRaises(IntegrationError):
                parse_canonical(data)

    def test_lowercase_receipt_forbidden(self):
        r = json.loads(receipt())
        r['kind'] = 'seen'
        with self.assertRaises(IntegrationError):
            validate_receipt(canonical(r), project_id='p1')


class TestGitHub(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHubHTTP()
        self.channel = GitHubChannel('p1', 'owner', 'repo', 'existing-ref')
        self.git = GitHub(self.fake, TOKEN, self.channel, ProjectBinding('p1', 'account'))
        self.path = self.channel.path('mail', 's1', 'msg1')

    def test_publish_readback_and_immutable_duplicate(self):
        body = envelope()
        result = self.git.publish(self.path, body, expected_digest=sha256(body), effect_intent_recorded=True)
        self.assertEqual(result.body, body)
        self.assertEqual(result.commit_sha, SHA1)
        again = self.git.publish(self.path, body, expected_digest=sha256(body), effect_intent_recorded=True)
        self.assertEqual(again.commit_sha, SHA1)
        self.assertEqual(self.fake.mutations, 1)

    def test_reject_missing_intent_and_noncanonical_payload(self):
        with self.assertRaises(IntegrationError):
            self.git.publish(self.path, envelope(), expected_digest=sha256(envelope()), effect_intent_recorded=False)
        with self.assertRaises(IntegrationError):
            self.git.publish(self.path, b'{}\n', expected_digest=sha256(b'{}\n'), effect_intent_recorded=True)
        self.assertEqual(self.fake.mutations, 0)

    def test_ambiguous_after_write_recovers_with_exact_readback(self):
        self.fake.timeout_after_write = True
        body = envelope()
        self.git.publish(self.path, body, expected_digest=sha256(body), effect_intent_recorded=True)
        self.assertEqual(self.fake.mutations, 1)

    def test_delayed_write_preserves_unknown(self):
        self.fake.timeout_after_write = True
        original = self.fake.request
        def failed(method, url, headers, body=None):
            if method == 'PUT':
                raise RemoteUnknown('timeout before remote commit')
            return original(method, url, headers, body)
        self.fake.request = failed
        with self.assertRaises(RemoteUnknown):
            self.git.publish(self.path, envelope(), expected_digest=sha256(envelope()), effect_intent_recorded=True)
        self.assertEqual(self.fake.mutations, 0)

    def test_conflicting_duplicate(self):
        self.fake.inject(self.path, envelope())
        other = envelope(kind='STOP')
        with self.assertRaises(IntegrationError):
            self.git.publish(self.path, other, expected_digest=sha256(other), effect_intent_recorded=True)
        self.assertEqual(self.fake.mutations, 0)

    def test_poll_pagination_cursor_and_replay(self):
        data1, data2 = envelope(), envelope(mid='msg2')
        self.fake.inject(self.path, data1)
        self.fake.inject(self.channel.path('mail', 's1', 'msg2'), data2, SHA2)
        page = self.git.scan_since(SHA0, limit=1)
        self.assertIsNone(page.complete_cursor)
        self.assertEqual(page.next_offset, 1)
        done = self.git.scan_since(SHA0, limit=1, offset=1, expected_head=page.head_sha)
        self.assertEqual(done.complete_cursor, SHA2)
        self.assertEqual(len(self.git.scan_since(SHA2, limit=3).entries), 0)

    def test_invalid_poll_offset_never_advances_cursor(self):
        self.fake.inject(self.path, envelope())
        with self.assertRaises(IntegrationError):
            self.git.scan_since(SHA0, limit=10, offset=999)

    def test_truncated_tree_and_remote_partial(self):
        self.fake.inject(self.path, envelope())
        self.fake.truncated = True
        with self.assertRaises(RemoteUnknown):
            self.git.scan_since(SHA0, limit=10)
        self.fake.truncated = False
        self.fake.partial = True
        with self.assertRaises(RemoteUnknown):
            self.git.scan_since(SHA0, limit=10)

    def test_rewrite_detected(self):
        self.fake.inject(self.path, envelope())
        self.fake.snapshot[SHA2] = dict(self.fake.snapshot[SHA1])
        self.fake.head = SHA2  # no parent relation to SHA1
        with self.assertRaises(RemoteUnknown):
            self.git.scan_since(SHA1, limit=10)

    def test_existing_relay_mutation_detected(self):
        self.fake.inject(self.path, envelope())
        self.fake.inject(self.path, envelope(kind='STOP'), SHA2)
        with self.assertRaises(RemoteUnknown):
            self.git.scan_since(SHA1, limit=10)

    def test_path_traversal_and_wrong_recipient(self):
        with self.assertRaises(IntegrationError):
            self.channel.path('mail', '..', 'hello')
        with self.assertRaises(IntegrationError):
            self.git.publish(self.channel.path('mail', 's2', 'msg1'), envelope(),
                             expected_digest=sha256(envelope()), effect_intent_recorded=True)

    def test_exact_source_commit_readback(self):
        source = GitHubSource(self.fake, TOKEN, ProjectBinding('p1', 'account'), 'owner', 'repo', 'existing-ref')
        path = 'src/world.cpp'
        self.fake.inject(path, b'int world = 1;')
        snap = source.read_file(project_id='p1', commit_sha=SHA1, path=path)
        self.assertEqual(snap.body, b'int world = 1;')
        self.assertEqual(snap.commit_sha, SHA1)
        with self.assertRaises(IntegrationError):
            source.read_file(project_id='p2', commit_sha=SHA1, path=path)
        with self.assertRaises(IntegrationError):
            source.read_file(project_id='p1', commit_sha=SHA1, path='../secret')
        self.fake.partial = True
        with self.assertRaises(RemoteUnknown):
            source.read_file(project_id='p1', commit_sha=SHA1, path=path)

    def test_receipt_published_with_path_binding(self):
        body = receipt()
        path = self.channel.path('receipts', 's1', 'r1', message_id='msg1')
        self.assertEqual(self.git.publish(path, body, expected_digest=sha256(body), effect_intent_recorded=True).body, body)


class TestStorage(unittest.TestCase):
    def test_project_blob_register_list_and_readback(self):
        with tempfile.TemporaryDirectory() as td:
            s = ArtifactStore(Path(td), ProjectBinding('p1', 'account'))
            raw = b'original glb or other artifact bytes'
            d = sha256(raw)
            s.put(raw, project_id='p1', expected_digest=d)
            manifest_digest = sha256(b'artifact manifest original')
            s.put(b'artifact manifest original', project_id='p1', expected_digest=manifest_digest)
            meta = Artifact('p1', 'art1', 'glb', d, manifest_digest, None, 'rev1', 'canon1', None, 'sha256:'+d)
            s.register(meta, validator=lambda b: self.assertEqual(b, raw))
            self.assertEqual(s.list_artifacts(project_id='p1'), [meta.summary()])
            self.assertEqual(s.get_artifact(project_id='p1', artifact_id='art1'), meta)
            self.assertEqual(s.get(project_id='p1', key='sha256:'+d), raw)
            with self.assertRaises(IntegrationError):
                s.get(project_id='p2', key='sha256:'+d)
            with self.assertRaises(IntegrationError):
                s.get_artifact(project_id='p1', artifact_id='../art1')
            blobpath = s._path(d)
            blobpath.write_bytes(b'tamper')
            with self.assertRaises(IntegrationError):
                s.get(project_id='p1', key='sha256:'+d)

    def test_resource_manifest_pin_and_escapes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/'a.bin').write_bytes(b'exec')
            (root/'args.json').write_bytes(b'{}')
            (root/'out.json').write_bytes(b'{}')
            resources = [{'path': p.name, 'sha256': sha256(p.read_bytes()), 'byte_size': p.stat().st_size}
                         for p in (root/'a.bin', root/'args.json', root/'out.json')]
            manifest = dict(manifest_version='1', package_id='tools', package_version='1',
                kind='tool', capabilities=[], dependencies=[], resources=resources, entrypoints=[dict(
                id='build', executable_digest=resources[0]['sha256'], args_schema_digest=resources[1]['sha256'],
                output_schema_digest=resources[2]['sha256'], required_isolation='sandbox',
                executable_path='a.bin', args_schema_path='args.json', output_schema_path='out.json')])
            raw = canonical(manifest)
            v = PackageVerifier(ProjectBinding('p1', 'account'))
            self.assertEqual(v.verify(project_id='p1', root=root, manifest_bytes=raw, expected_digest=sha256(raw))['kind'], 'tool')
            resources[0]['path'] = '../escape'
            bad = canonical(manifest)
            with self.assertRaises(IntegrationError):
                v.verify(project_id='p1', root=root, manifest_bytes=bad, expected_digest=sha256(bad))


class TestEffects(unittest.TestCase):
    def test_unknown_conflict_verified_and_project_boundary(self):
        data = b'expected'
        intent = EffectIntent('fx1', 'p1', 'github', 'owner/repo@ref', 'mail/s1/f.json', sha256(data), 'req1')
        repo = IntentMemory(intent)
        e = EffectReconciler(ProjectBinding('p1', 'account'), repo)
        self.assertEqual(e.observe_effect(project_id='p1', effect_id='fx1', readback=lambda _: None).state, 'UNKNOWN')
        self.assertEqual(e.reconcile_effect(project_id='p1', effect_id='fx1', readback=lambda _: ('sha1', b'incorrect')).state, 'CONFLICT')
        self.assertEqual(e.reconcile_effect(project_id='p1', effect_id='fx1', readback=lambda _: ('sha2', data)).state, 'VERIFIED')
        with self.assertRaises(IntegrationError):
            e.observe_effect(project_id='p2', effect_id='fx1', readback=lambda _: ('sha', data))
        self.assertEqual(len(repo.observed), 2)


class TestDrive(unittest.TestCase):
    def test_upload_readback_timeout_and_no_duplicate(self):
        mock = FakeDriveHTTP()
        client = Drive(mock, TOKEN, ProjectBinding('p1', 'acct'), 'folder1')
        data = b'published design bible'
        mock.timeout = True
        self.assertEqual(client.publish_new(project_id='p1', intent_id='intent1', name='bible.pdf', body=data,
                         expected_sha256=sha256(data), effect_intent_recorded=True), 'file1')
        self.assertEqual(client.publish_new(project_id='p1', intent_id='intent1', name='bible.pdf', body=data,
                         expected_sha256=sha256(data), effect_intent_recorded=True), 'file1')
        self.assertEqual(mock.write_count, 1)
        with self.assertRaises(IntegrationError):
            client.read_bytes(project_id='p2', file_id='file1')
        mock.files['file1']['meta']['parents'] = ['another_folder']
        with self.assertRaises(IntegrationError):
            client.read_bytes(project_id='p1', file_id='file1')

    def test_attachment_auth_identity_preflight(self):
        mock = FakeDriveHTTP()
        repo = ConnectorMemory()
        service = ConnectorService(mock, ProjectBinding('p1', 'account'), lambda ref: 'secret' if ref == 'credential1' else '', repo)
        self.assertEqual(service.attach(project_id='p1', request_id='req1', provider_key='google_drive',
                                        credential_ref='credential1').account_id, 'verified-google-id')
        self.assertEqual(len(repo.records), 1)
        with self.assertRaises(IntegrationError):
            service.attach(project_id='p2', request_id='req2', provider_key='google_drive', credential_ref='credential1')


class BuildMemory:
    def __init__(self):
        self.record = PreparedBuild('p1', 'ci-local1', 'effect1', SHA0, 'win', 'fx1')
        self.claimed = False
        self.dispatched = 0
        self.runs = []

    def prepare(self, *, project_id, request_id, source_revision_id, build_target):
        return self.record

    def claim_first_dispatch(self, *, project_id, effect_id):
        if self.claimed:
            return False
        self.claimed = True
        self.dispatched += 1
        return True

    def observe(self, *, project_id, ci_id, run):
        self.runs.append(run)


class TestPorts(unittest.TestCase):
    def test_six_frozen_operation_shapes_and_no_double_ci_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            binding = ProjectBinding('p1', 'account')
            artifacts = ArtifactStore(Path(td), binding)
            data = b'original bytes'
            d = sha256(data)
            artifacts.put(data, project_id='p1', expected_digest=d)
            manifest_digest = sha256(b'manifest bytes')
            artifacts.put(b'manifest bytes', project_id='p1', expected_digest=manifest_digest)
            artifacts.register(Artifact('p1', 'a1', 'source', d, manifest_digest, None, 'rev1', 'canon1', None, 'sha256:'+d))
            fake = FakeGitHubHTTP()
            git = GitHub(fake, TOKEN, GitHubChannel('p1', 'owner', 'repo', 'existing-ref'), binding)
            ci = GitHubActions(git, binding, {'win': BuildTarget('win', 'game.yml', 'existing-ref')})
            conn_repo = ConnectorMemory()
            connector = ConnectorService(fake, binding, lambda _: 'configured-token', conn_repo)
            effect_repo = IntentMemory(EffectIntent('effect1', 'p1', 'github', 'repo', 'f', d, 'req1'))
            effects = EffectReconciler(binding, effect_repo)
            builds = BuildMemory()
            ports = GOperatorPorts(binding, artifacts, connector, ci, builds, effects)
            self.assertEqual(ports.list_artifacts(project_id='p1'), {'artifacts': [{'artifact_id': 'a1', 'kind': 'source', 'content_digest': d}]})
            self.assertEqual(ports.get_artifact(project_id='p1', artifact_id='a1'), {'artifact_id': 'a1', 'kind': 'source', 'content_digest': d})
            self.assertEqual(ports.attach_connector(project_id='p1', request_id='req1', provider_key='github', credential_ref='c1'),
                             {'account_id': 'configured-login', 'state': 'attached'})
            once = ports.run_build(project_id='p1', request_id='req2', source_revision_id='rev1', build_target='win')
            twice = ports.run_build(project_id='p1', request_id='req2', source_revision_id='rev1', build_target='win')
            self.assertEqual(once, {'ci_id': 'ci-local1', 'state': 'UNKNOWN'})
            self.assertEqual(once, twice)
            self.assertEqual(builds.dispatched, 1)
            self.assertEqual(ports.observe_effect(project_id='p1', effect_id='effect1', readback=lambda _: ('remote', data)),
                             {'effect_id': 'effect1', 'state': 'VERIFIED'})
            self.assertEqual(ports.reconcile_effect(project_id='p1', effect_id='effect1', request_id='req3',
                readback=lambda _: ('remote', data), expected_state='UNKNOWN'), {'effect_id': 'effect1', 'state': 'VERIFIED'})
            with self.assertRaises(IntegrationError):
                ports.list_artifacts(project_id='p2')


class TestRetry(unittest.TestCase):
    def test_backoff_deterministic_capped_and_retry_after(self):
        p = RetryPolicy(base_seconds=1, cap_seconds=5)
        self.assertEqual(p.delay(attempt=4, stable_key='channel1'), 5)
        self.assertEqual(p.delay(attempt=0, stable_key='channel1', retry_after=3), 3)
        self.assertEqual(p.delay(attempt=2, stable_key='channel1'), p.delay(attempt=2, stable_key='channel1'))
        with self.assertRaises(ValueError):
            p.delay(attempt=-1, stable_key='channel1')


class TestCI(unittest.TestCase):
    def test_dispatch_and_pinned_run(self):
        fake = FakeGitHubHTTP()
        binding = ProjectBinding('p1', 'account')
        git = GitHub(fake, TOKEN, GitHubChannel('p1', 'owner', 'repo', 'existing-ref'), binding)
        actions = GitHubActions(git, binding, {'win': BuildTarget('win', 'game.yml', 'existing-ref')})
        with self.assertRaises(IntegrationError):
            actions.dispatch(project_id='p1', target_id='win', source_sha=SHA0, intent_id='fx1', effect_intent_recorded=False)
        actions.dispatch(project_id='p1', target_id='win', source_sha=SHA0, intent_id='fx1', effect_intent_recorded=True)
        self.assertIsNone(actions.observe(project_id='p1', target_id='win', source_sha=SHA0, intent_id='fx1'))
        fake.runs.append({'id': 10, 'display_title': 'nomad:fx1', 'head_sha': SHA0, 'status': 'completed',
                          'conclusion': 'success', 'path': '.github/workflows/game.yml@existing-ref', 'html_url': 'https://example.invalid/run/10'})
        self.assertEqual(actions.observe(project_id='p1', target_id='win', source_sha=SHA0, intent_id='fx1').run_id, 10)
        self.assertEqual(actions.verify_run(project_id='p1', target_id='win', intent_id='fx1', run_id=10, expected_sha=SHA0).conclusion, 'success')
        with self.assertRaises(IntegrationError):
            actions.dispatch(project_id='p1', target_id='win', source_sha=SHA1, intent_id='fx2', effect_intent_recorded=True)
        fake.runs.append(dict(fake.runs[0], id=11))
        with self.assertRaises(RemoteUnknown):
            actions.observe(project_id='p1', target_id='win', source_sha=SHA0, intent_id='fx1')


if __name__ == '__main__':
    unittest.main()
