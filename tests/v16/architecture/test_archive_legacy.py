"""A02 offline evidence export verifies complete original bytes."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.archive_legacy import snapshot, verify


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.repo = self.path / 'repo'
        self.repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.PIPE)
        self.git = git
        git('init', '-q')
        git('config', 'user.name', 'Evidence Fixture')
        git('config', 'user.email', 'fixture@example.invalid')
        (self.repo / 'source.json').write_bytes(b'{"name":"original"}\n')
        git('add', '.')
        git('commit', '-qm', 'evidence')
        self.commit = git('rev-parse', 'HEAD').decode().strip()

    def test_snapshot_verifies_raw_bytes_and_is_archive_only(self):
        target = self.path / 'archive'
        result = snapshot(self.repo, ['HEAD'], target, expected={'HEAD': self.commit})
        self.assertEqual(result['signature'], 'UNSIGNED')
        self.assertEqual(result['snapshots'][0]['count'], 1)
        self.assertEqual(verify(target)['snapshots'][0]['head'], self.commit)
        self.assertEqual((target / 'raw' / result['snapshots'][0]['label'] / 'source.json').read_bytes(), b'{"name":"original"}\n')

    def test_reject_source_sha_mismatch(self):
        with self.assertRaisesRegex(ValueError, 'unverified source ref'):
            snapshot(self.repo, ['HEAD'], self.path / 'archive', expected={'HEAD': '0'*40})

    def test_reject_modified_evidence(self):
        target = self.path / 'archive'
        result = snapshot(self.repo, ['HEAD'], target)
        file = target / 'raw' / result['snapshots'][0]['label'] / 'source.json'
        file.chmod(0o600)
        file.write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'archive file integrity'):
            verify(target)

    def test_reject_ref_traversal(self):
        with self.assertRaisesRegex(ValueError, 'unsafe Git ref'):
            snapshot(self.repo, ['../HEAD'], self.path / 'archive')


if __name__ == '__main__':
    unittest.main()
