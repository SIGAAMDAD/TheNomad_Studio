# Source contribution packages

Studio contributions are **changed-files-only** immutable ZIP archives with a detached SHA-256 manifest. The contribution owner is selected from the frozen source-ownership roster. There is no implicit owner, version-specific script, or per-worker branch.

## Encoding and compression

The writer uses standard ZIP **DEFLATE at level 9**, sorted canonical paths, a fixed timestamp, mode `0644`, and fixed platform metadata. It verifies the resulting archive against the source-ownership roster before returning. This gives byte-identical ZIPs for byte-identical inputs while remaining readable with ordinary ZIP tools. Already-compressed binary artifacts may compress weakly; source text and JSON generally compress well.

We do not require Zstandard or 7z, because the receiving handoff validator uses Python's standard ZIP reader and every worker must be able to unpack the package. The writer reports total input bytes, compressed payload bytes, and ratio; ZIP central-directory overhead makes the final archive slightly larger than the payload total.

## Changed-file selection and provenance

- `--source-root` contains the selected owner's current source files; `--baseline-root` optionally points to a verified prior source tree.
- The packer includes only paths whose **content bytes actually changed** relative to that baseline. Deletions are recorded with `after: null` in the detached manifest and have no ZIP entry.
- `--owner` is mandatory and must match the plan's ownership roster. Foreign source files, links and unowned changes are rejected.
- `--base-revision` must be the true source revision or commit SHA used for comparison; it is not inferred from an empty tree.
- The manifest binds the frozen plan digest, owner, baseline revision, before/after SHA-256 for every changed path and any supplied test receipts. The receiving validator rejects unlisted or modified files, duplicate paths, path traversal, and invalid source ownership.

Example:

```sh
PYTHONPATH=src python scripts/package_contribution.py \
  --source-root /path/to/changed-owned-source \
  --baseline-root /path/to/pinned-source \
  --base-revision <pinned-commit-sha> \
  --owner M \
  --packet /path/to/frozen-plan \
  --output /path/to/contribution.zip \
  --manifest /path/to/contribution.json
```

The owner ID in this example is an input to the *existing* ownership contract, not part of the packager's filename or API. The script is generic for all registered owners.

## Limits and verification

The production intake validator enforces 10,000 file entries, a 128 MiB per-file uncompressed limit and a 512 MiB total uncompressed limit. These are safety bounds rather than suggested package sizes. Test evidence must still be checked independently against the exact integrated commit. Compression and manifest verification alone do not prove QA approval, authorship or source integration.
