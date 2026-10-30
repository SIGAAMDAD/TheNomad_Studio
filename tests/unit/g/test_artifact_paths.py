"""Negative filesystem tests for the real artifact store, not a mocked filesystem."""
import os
import tempfile
from unittest.mock import patch
import unittest
from pathlib import Path

from studio.integrations import Artifact, ArtifactStore, IntegrationError, ProjectBinding, sha256


class ArtifactPathIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'root'
        self.outside = Path(self.temp.name) / 'outside'
        self.outside.mkdir()
        self.store = ArtifactStore(self.root, ProjectBinding('project', 'account'))
        self.content = b'verified original content'
        self.digest = sha256(self.content)

    def test_empty_project_listing(self):
        self.assertEqual(self.store.list_artifacts(project_id='project'), [])

    def test_blob_without_metadata_lists_no_artifacts(self):
        self.store.put(self.content, project_id='project', expected_digest=self.digest)
        self.assertEqual(self.store.list_artifacts(project_id='project'), [])

    def test_project_directory_symlink_cannot_redirect_artifact_write(self):
        (self.root / 'project').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(IntegrationError):
            self.store.put(self.content, project_id='project', expected_digest=self.digest)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_intermediate_directory_symlink_cannot_redirect_artifact_write(self):
        (self.root / 'project').mkdir()
        (self.root / 'project' / 'sha256').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(IntegrationError):
            self.store.put(self.content, project_id='project', expected_digest=self.digest)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_symlinked_content_file_is_rejected_on_put_and_get(self):
        path = self.store._path(self.digest)
        path.parent.mkdir(parents=True)
        target = self.outside / 'target'
        target.write_bytes(self.content)
        path.symlink_to(target)
        with self.assertRaises(IntegrationError):
            self.store.put(self.content, project_id='project', expected_digest=self.digest)
        with self.assertRaises(IntegrationError):
            self.store.get(project_id='project', key='sha256:' + self.digest)
        self.assertEqual(target.read_bytes(), self.content)

    def test_symlinked_artifact_index_cannot_redirect_metadata_write(self):
        self.store.put(self.content, project_id='project', expected_digest=self.digest)
        index = self.root / 'project' / 'artifacts' / 'artifact.json'
        index.parent.mkdir(parents=True)
        target = self.outside / 'target'
        target.write_bytes(b'unrelated')
        index.symlink_to(target)
        meta = Artifact('project', 'artifact', 'source', self.digest, 'e'*64,
                        None, 'revision', 'canon', None, 'sha256:' + self.digest)
        with self.assertRaises(IntegrationError):
            self.store.register(meta)
        with self.assertRaises(IntegrationError):
            self.store.get_artifact(project_id='project', artifact_id='artifact')
        self.assertEqual(target.read_bytes(), b'unrelated')

    def test_symlinked_index_directory_cannot_expose_external_files(self):
        (self.outside / 'artifact.json').write_text('{}')
        (self.root / 'project').mkdir()
        (self.root / 'project' / 'artifacts').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(IntegrationError):
            self.store.list_artifacts(project_id='project')

    def test_broken_symlinked_index_directory_is_rejected(self):
        (self.root / 'project').mkdir()
        (self.root / 'project' / 'artifacts').symlink_to(self.outside / 'missing', target_is_directory=True)
        with self.assertRaises(IntegrationError):
            self.store.list_artifacts(project_id='project')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'requires POSIX named pipes')
    def test_non_regular_artifact_is_rejected_without_blocking(self):
        path = self.store._path(self.digest)
        path.parent.mkdir(parents=True)
        os.mkfifo(path)
        with self.assertRaises(IntegrationError):
            self.store.put(self.content, project_id='project', expected_digest=self.digest)
        with self.assertRaises(IntegrationError):
            self.store.get(project_id='project', key='sha256:' + self.digest)

    def test_interrupted_publication_has_no_partial_visible_blob(self):
        # A failed atomic link cannot expose partial bytes at the final digest path.
        with patch('studio.integrations.artifacts.os.link', side_effect=OSError('simulated failure')):
            with self.assertRaises(IntegrationError):
                self.store.put(self.content, project_id='project', expected_digest=self.digest)
        target = self.store._path(self.digest)
        self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.iterdir()), [])
        self.store.put(self.content, project_id='project', expected_digest=self.digest)
        self.assertEqual(self.store.get(project_id='project', key='sha256:' + self.digest), self.content)

    def test_repeated_content_put_is_idempotent(self):
        self.assertEqual(self.store.put(self.content, project_id='project', expected_digest=self.digest),
                         self.store.put(self.content, project_id='project', expected_digest=self.digest))
        self.assertEqual(self.store.get(project_id='project', key='sha256:' + self.digest), self.content)


if __name__ == '__main__':
    unittest.main()
