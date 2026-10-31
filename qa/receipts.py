"""Independent evidence consistency checks; receipts are *not* identity credentials."""
from __future__ import annotations
from pathlib import Path
from .core import digest, read_json, safe_relative


class ReceiptError(ValueError):
    pass


def validate_review(candidate_sha: str, review_path: Path, artifacts_root: Path) -> dict:
    """Validate bytes, independence and verdict; not remote/authentication proof.

    The trusted E service must separately authenticate principals and bind a
    review to original source/submission in its production state.
    """
    doc = read_json(review_path)
    if doc.get('candidate_tree_sha256') != candidate_sha:
        raise ReceiptError('review belongs to a different candidate revision')
    if not doc.get('submission_id') or not doc.get('acceptance_digest'):
        raise ReceiptError('missing submission/acceptance binding')
    author = doc.get('author_principal_id')
    reviewer = doc.get('reviewer_principal_id')
    if not isinstance(author, str) or not isinstance(reviewer, str) or not author or author == reviewer:
        raise ReceiptError('reviewer lacks author independence')
    if doc.get('verdict') != 'PASS':
        raise ReceiptError('QA PASS not observed')
    if not doc.get('validator_digest') or not doc.get('source_revision_id'):
        raise ReceiptError('missing validator and source revision proof')
    receipts = doc.get('receipts')
    if not isinstance(receipts, list) or not receipts:
        raise ReceiptError('no linked validator/artifact receipts')
    verified = []
    for item in receipts:
        path = artifacts_root / safe_relative(item['artifact_path'])
        log = artifacts_root / safe_relative(item['log_path'])
        if not path.is_file() or path.is_symlink() or digest(path.read_bytes()) != item.get('artifact_sha256'):
            raise ReceiptError(f'invalid artifact bytes: {path}')
        if not log.is_file() or log.is_symlink() or digest(log.read_bytes()) != item.get('log_sha256'):
            raise ReceiptError(f'invalid validator output: {log}')
        if not isinstance(item.get('command'), list) or not item['command'] or item.get('exit_code') != 0:
            raise ReceiptError('validator command/exit evidence missing or failed')
        if item.get('validator_digest') != doc['validator_digest']:
            raise ReceiptError('validator digest mismatch')
        verified.append({'artifact_sha256': item['artifact_sha256'], 'log_sha256': item['log_sha256']})
    return {'review_sha256': digest(review_path.read_bytes()), 'candidate_tree_sha256': candidate_sha,
            'submission_id': doc['submission_id'], 'reviewer_principal_id': reviewer, 'verified_receipts': verified,
            'classification': 'byte_consistency_only; trusted runtime identity/readback separately required'}
