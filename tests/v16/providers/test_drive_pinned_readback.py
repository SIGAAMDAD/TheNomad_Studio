"""A07 revision-pin isolation and fail-closed Drive readback requirements."""
import hashlib
import unittest

from studio_v16.providers.drive_provider import DriveRevisionProvider, ProviderBlocked


class CurrentOnly:
    def readback(self, file_id):
        return {'revision': 'rev-2', 'content': b'correct', 'idempotency_key': 'id'}


class Historical:
    def read_revision(self, file_id, revision):
        if revision != 'rev-1':
            raise LookupError('missing revision')
        return {'revision': 'rev-1', 'content': b'original'}


class PinTests(unittest.TestCase):
    def test_current_readback_is_not_accepted_as_pinned_history(self):
        provider = DriveRevisionProvider(CurrentOnly())
        with self.assertRaisesRegex(ProviderBlocked, 'DOCUMENT_CONFLICT'):
            provider.read_pinned('f', 'rev-2', hashlib.sha256(b'correct').hexdigest())

    def test_exact_pinned_revision_readback(self):
        provider = DriveRevisionProvider(Historical())
        self.assertEqual(provider.read_pinned('f', 'rev-1', hashlib.sha256(b'original').hexdigest()), b'original')
        with self.assertRaisesRegex(ProviderBlocked, 'DOCUMENT_CONFLICT'):
            provider.read_pinned('f', 'rev-2', hashlib.sha256(b'original').hexdigest())
        with self.assertRaisesRegex(ProviderBlocked, 'DOCUMENT_CONFLICT'):
            provider.read_pinned('f', 'rev-1', hashlib.sha256(b'tampered').hexdigest())


if __name__ == '__main__':
    unittest.main()
