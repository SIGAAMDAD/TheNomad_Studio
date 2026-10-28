# B00 content contract proposal — review required

Status: PROPOSED. This document, its XSD, and its example do not confer authority, authorize an import, approve a mod policy, or change frozen WG-A contracts. Owner and architecture reviewer acceptance is required before B01 may use these as a frozen contract. The example uses dummy IDs and hashes, not game content.

## Identity, document, and parsing

Each package has one UTF-8 XML document conforming to the adjacent proposed XSD. `module` is lowercase ASCII, begins with a letter, and has at most 32 characters. An entity ID is `<module>:<local-id>`; local IDs are lowercase ASCII, begin with a letter, use letters, digits, `_`, `.` or `-`, and contain at most 128 characters. IDs are case-sensitive and immutable. `base_game` is reserved to the base game. Module ownership is external verified authority; XML text is never proof of ownership. Renames or removal require an approved versioned migration/tombstone and save compatibility evidence. No implicit aliases.

`source_sha` is the exact source Git commit OID, not a content digest or authority grant. Each entity's `digest` is SHA-256 of the accepted canonical authored artifact bytes, not a requester's unverified claim. At admission the importer must independently verify repository, branch, commit and content bytes and compute the manifest digest from exact XML bytes. The tuple `(module, source_sha, manifest_digest)` identifies an import; exact replay is idempotent and different content at the same declared revision is a conflict. The XSD fixes structure only. The semantic validator must also check ID/module ownership, uniqueness, references, edge invariants and provenance.

Parse in a bounded isolated process with DTDs, entities, XInclude and network disabled; reject symlinks, traversal, unknown attributes/elements, malformed XML and unbounded input. Proposed limits, subject to owner acceptance: 1 MiB per manifest, 32 entity nesting levels, 10,000 nodes, and 50,000 edges. Resolve files only beneath an authorized source root. XSD imports/includes are disallowed; validator and schema digest must be pinned. A valid declaration creates no grant, job, or publication authority.

## Edge contract

Containment follows XML nesting: `parent CONTAINS child`. It is a forest, never a duplicate subtree or a general graph. Other edges are explicit references with `from` and `to` as follows:

| Type | Direction | Meaning |
| --- | --- | --- |
| REQUIRES | dependent → prerequisite | Required for the dependent's accepted state; hard cycles are rejected. |
| USES | consumer → referenced entity | Optional reference; cycles may be reported and must not schedule mandatory work. |
| PRODUCES | producer → output | Producer owns production of the output; no authority grant follows from this claim. |
| INVALIDATES | input → affected entity | An accepted input revision change requires the affected entity's evidence to be reconsidered. |

Duplicate identical edges and self-edges are rejected. Every `from` must be declared in this package; `to` must resolve to a known accepted entity or an entity in the same atomic import. Unresolved references are rejected, not silently omitted. Impact queries traverse reverse REQUIRES edges and forward INVALIDATES edges; impact is based on accepted digest changes and exact affected revision, not an XML parse alone.

## Mod and override policy proposed for P0

Base and each mod own separate module namespaces. A mod may declare and modify only IDs in its authorized namespace; it may reference accepted IDs in other namespaces. Collision with another namespace is denied. P0 does not support replacement or merging of base or other mods' entities: no implicit last-writer precedence, no protected-ID override, and no grant or owner decision inferred from XML. Install/load order is explicit metadata but cannot alter these ownership rules. Any future replacement, merge or conflict-resolution behavior requires a separately versioned, owner-approved policy and save migration before it is enabled. Rejected imports leave prior accepted revisions intact.

## Readiness, asset and release evidence

`GAME-FEATURE/1` identifies the feature, required/optional gate IDs, accepted input revisions, and immutable evidence references. Readiness is derived from exact current hashes and distinct author, reviewer and owner identities. A submission or inbox ACK is not acceptance; missing, stale, self-reviewed, conflicting, or unverified required evidence blocks the corresponding gate. Owner approval is a separate authenticated decision. `ASSET-RECEIPT/1` binds source and export digests, producer, import settings, tool/build version, license reference and independent validation. Save compatibility binds versioned IDs, explicit migration/tombstone and real game golden-save evidence. `RELEASE-MANIFEST/1` binds code, content, asset/audio hashes, build configuration, save, rights, accessibility, performance and QA evidence. Fixtures are not a release decision.

P0 proposal: validated package import, directed dependency graph, addressed handoffs and scoped readiness with no override support. P1 requires separately approved mod override semantics, real-game save migration, runtime/audio integration and release promotion. Unknown or disputed policy remains blocked. Owner must explicitly accept or amend identity syntax, limits, mod policy, P0 scope and reviewer separation; architecture review must verify the XSD, importer security and edge contracts before freeze.
