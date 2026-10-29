"""R9 advisory council. Implements frozen CouncilPort, with strictly scoped writes.

Dependencies injected at P2:
  db.read(callback), db.write(callback) -- latter permits only three CAB-F tables;
  records.put(record)->sha256, records.get(sha256)->record -- durable immutable CAS;
  bible.snapshot_active_pair, bible.verify_clause_provenance -- CAB-D port;
  authority.assess_bible_conflict -- CAB-D port;
  clock_us() -- trusted clock, never an agent field.

External authority / CAS reads and CAS writes happen OUTSIDE writer transactions.
The DB transaction rechecks feature lineage, active editions and case state.
No bible, QA, graph, source, or owner-approval table is ever written here.
"""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any

from .records import InvalidRecord, conflict as validate_conflict, dissent as validate_dissent
from .records import digest, ident, refs, selected as validate_selected, sha, proposal as validate_proposal

# Frozen R8/R9 types, exact bytes from CONTRACTS/runtime_ports_v1.py.
# P2 installs this module in the new-only runtime's import path.
from nomad_studio.contracts.runtime_ports_v1 import Decision, Result


class CouncilService:
    """Dependency-injected CouncilPort; no self-proclaimed owner/QA effect proof."""
    def __init__(self, *, db: Any, records: Any, bible: Any, authority: Any, clock_us: Any):
        for dep, methods in ((db, ('read', 'write')), (records, ('put', 'get')),
                             (bible, ('snapshot_active_pair', 'verify_clause_provenance')),
                             (authority, ('assess_bible_conflict',))):
            if not all(callable(getattr(dep, method, None)) for method in methods):
                raise TypeError('CAB-F requires verified injected port dependencies')
        if not callable(clock_us):
            raise TypeError('trusted clock required')
        self._db, self._records = db, records
        self._bible, self._authority, self._clock_us = bible, authority, clock_us

    def _store(self, record: Mapping[str, Any]) -> str:
        expected = digest(record)
        try:
            observed = self._records.put(dict(record))
        except Exception as exc:
            raise CouncilError('NOT_VERIFIED','EVIDENCE_INVALID') from exc
        if observed != expected or self._load(expected) != dict(record):
            raise CouncilError('NOT_VERIFIED','EVIDENCE_INVALID')
        return expected

    def _load(self, record_digest: str) -> dict[str, Any]:
        try:
            record = self._records.get(sha(record_digest))
            if not isinstance(record, Mapping) or digest(record) != record_digest:
                raise CouncilError('NOT_VERIFIED','EVIDENCE_INVALID')
            return dict(record)
        except (InvalidRecord, TypeError, ValueError) as exc:
            raise CouncilError('NOT_VERIFIED','EVIDENCE_INVALID') from exc
        except CouncilError:
            raise
        except Exception as exc:
            raise CouncilError('NOT_VERIFIED','EVIDENCE_INVALID') from exc

    @staticmethod
    def _result(status: str, code: str, record: Mapping[str, Any] | None = None,
                evidence: tuple[str, ...] = ()) -> Result:
        return Result(status=status, code=code, record=record, evidence_refs=evidence)

    def _snapshot(self, project_id: str, feature_id: str, bible_pair_digest: str) -> Mapping[str, Any]:
        result = self._bible.snapshot_active_pair(project_id, {'project_id': project_id, 'feature_id': feature_id})
        if (not isinstance(result, Result) or result.status != 'OK' or result.code != 'OK' or
            not isinstance(result.record, Mapping)):
            raise CouncilError('NOT_VERIFIED', 'EVIDENCE_INVALID')
        snapshot = result.record
        if snapshot.get('project_id') != project_id or snapshot.get('pair_digest') != bible_pair_digest:
            raise CouncilError('CONFLICT', 'STALE_BIBLE')
        for k in ('design_edition_id', 'technical_edition_id'):
            ident(snapshot.get(k))
        return snapshot

    @staticmethod
    def _feature(tx: Any, feature_id: str, project_id: str, bible_pair_digest: str,
                 snapshot: Mapping[str, Any], contract_digest: str | None = None,
                 graph_revision: int | None = None) -> Mapping[str, Any]:
        row = tx.execute('''SELECT f.project_id,f.feature_id,f.bible_pair_digest,
                   f.active_contract_digest,f.graph_revision,p.state
                   FROM v1_feature_runs f JOIN v1_projects p ON p.project_id=f.project_id
                   WHERE f.feature_id=?''', (feature_id,)).fetchone()
        if row is None:
            raise CouncilError('NOT_FOUND', 'NOT_FOUND')
        if row['project_id'] != project_id:
            raise CouncilError('REJECTED', 'WRONG_PROJECT')
        if row['state'] != 'ACTIVE':
            raise CouncilError('BLOCKED_LOCAL', 'LOCAL_RECOVERY_REQUIRED')
        if row['bible_pair_digest'] != bible_pair_digest:
            raise CouncilError('CONFLICT', 'STALE_BIBLE')
        if contract_digest is not None and row['active_contract_digest'] != contract_digest:
            raise CouncilError('CONFLICT', 'REVISION_CONFLICT')
        if graph_revision is not None and row['graph_revision'] != graph_revision:
            raise CouncilError('CONFLICT', 'REVISION_CONFLICT')
        # Check activated editions *inside* the write transaction: a snapshot
        # taken before a parallel bible activation cannot authorize stale cases.
        editions = tx.execute('''SELECT edition_id,kind FROM v1_bible_editions
                                 WHERE project_id=? AND active=1''', (project_id,)).fetchall()
        active = {r['kind']: r['edition_id'] for r in editions}
        if (len(editions) != 2 or active.get('DESIGN') != snapshot['design_edition_id'] or
            active.get('TECHNICAL') != snapshot['technical_edition_id']):
            raise CouncilError('CONFLICT', 'STALE_BIBLE')
        return row

    @staticmethod
    def _check_cabals(tx: Any, project_id: str, pair: str, cabal_ids: set[str]) -> None:
        if not cabal_ids:
            raise InvalidRecord('no proposal cabals')
        rows = tx.execute('''SELECT cabal_id,state,bible_pair_digest FROM v1_cabals
                             WHERE project_id=?''', (project_id,)).fetchall()
        valid = {r['cabal_id'] for r in rows
                 if r['state'] == 'ACTIVE' and r['bible_pair_digest'] == pair}
        if not cabal_ids <= valid:
            raise CouncilError('REJECTED', 'PERMISSION_DENIED')

    def _case(self, tx: Any, case_id: str):
        row = tx.execute('''SELECT c.*, f.project_id,f.active_contract_digest,f.graph_revision
                             FROM v1_council_cases c JOIN v1_feature_runs f
                             ON f.feature_id=c.feature_id WHERE c.case_id=?''', (case_id,)).fetchone()
        if row is None:
            raise CouncilError('NOT_FOUND', 'NOT_FOUND')
        return row

    def open_case(self, feature_id: str, conflict: Mapping[str, Any],
                  bible_pair_digest: str) -> Result:
        try:
            ident(feature_id); sha(bible_pair_digest)
            data = validate_conflict(conflict)
            project_id = data['project_id']
            snap = self._snapshot(project_id, feature_id, bible_pair_digest)
            # Preserve every alternative in immutable storage before committing
            # the three SQL tables, which can only store content hashes.
            conflict_hash = self._store(data)
            proposal_hashes = [(p, self._store(p)) for p in data['proposals']]
            def write(tx):
                self._feature(tx, feature_id, project_id, bible_pair_digest, snap,
                              data['feature_contract_digest'], data['graph_revision'])
                prior = tx.execute('SELECT * FROM v1_council_cases WHERE case_id=?',
                                   (data['case_id'],)).fetchone()
                if prior is not None:
                    if (prior['feature_id'] != feature_id or prior['bible_pair_digest'] != bible_pair_digest
                        or prior['conflict_digest'] != conflict_hash):
                        raise CouncilError('CONFLICT', 'IDEMPOTENCY_CONFLICT')
                    return
                self._check_cabals(tx, project_id, bible_pair_digest,
                                   {p['author_cabal_id'] for p,_ in proposal_hashes})
                # Proposal identity is globally unique in frozen DDL.
                for p,_ in proposal_hashes:
                    if tx.execute('SELECT 1 FROM v1_council_proposals WHERE proposal_id=?',
                                  (p['proposal_id'],)).fetchone():
                        raise CouncilError('CONFLICT', 'IDEMPOTENCY_CONFLICT')
                tx.execute('''INSERT INTO v1_council_cases
                          (feature_id,case_id,bible_pair_digest,state,conflict_digest)
                          VALUES (?,?,?,?,?)''',
                          (feature_id,data['case_id'],bible_pair_digest,'OPEN',conflict_hash))
                for p, h in proposal_hashes:
                    tx.execute('''INSERT INTO v1_council_proposals
                       (proposal_id,case_id,author_cabal_id,proposal_digest,compliance_ref,selected)
                       VALUES (?,?,?,?,NULL,0)''',
                       (p['proposal_id'],data['case_id'],p['author_cabal_id'],h))
            self._db.write(write)
            return self._result('OK','OK', {'case_id':data['case_id'],
                    'feature_id':feature_id,'conflict_digest':conflict_hash},
                    (f'cas:{conflict_hash}',))
        except (InvalidRecord, TypeError, KeyError):
            return self._result('REJECTED', 'INVALID_INPUT')
        except CouncilError as e:
            return self._result(e.status, e.code)

    def record_dissent(self, case_id: str, dissent: Mapping[str, Any]) -> Result:
        try:
            ident(case_id); data=validate_dissent(dissent)
            row=self._db.read(lambda tx: dict(self._case(tx, case_id)))
            original=self._load(row['conflict_digest'])
            snap = self._snapshot(original['project_id'],row['feature_id'],row['bible_pair_digest'])
            h=self._store(data)
            def write(tx):
                case=self._case(tx,case_id)
                if case['conflict_digest'] != row['conflict_digest']:
                    raise CouncilError('CONFLICT','REVISION_CONFLICT')
                self._feature(tx,case['feature_id'], original['project_id'], case['bible_pair_digest'],
                              snap,original['feature_contract_digest'],original['graph_revision'])
                if (data['proposal_id'] not in {p['proposal_id'] for p in original['proposals']}
                    or not set(data['clause_refs']) <= set(original['clause_refs'])):
                    raise CouncilError('REJECTED','SCOPE_EXCEEDED')
                # A dissenter must have an unexpired membership in the same project.
                membership=tx.execute('''SELECT 1 FROM v1_memberships
                     WHERE project_id=? AND worker_id=? AND valid_until_us>?''',
                     (original['project_id'],data['author_id'],self._clock_us())).fetchone()
                if not membership:
                    raise CouncilError('REJECTED','PERMISSION_DENIED')
                prior=tx.execute('SELECT * FROM v1_council_dissent WHERE dissent_id=?',
                                 (data['dissent_id'],)).fetchone()
                if prior is not None:
                    if prior['case_id'] != case_id or prior['reason_digest'] != h:
                        raise CouncilError('CONFLICT','IDEMPOTENCY_CONFLICT')
                    return
                tx.execute('''INSERT INTO v1_council_dissent
                      (dissent_id,case_id,author_id,reason_digest) VALUES (?,?,?,?)''',
                      (data['dissent_id'],case_id,data['author_id'],h))
            self._db.write(write)
            return self._result('OK','OK', {'dissent_id':data['dissent_id'],
                  'case_id':case_id,'reason_digest':h},(f'cas:{h}',))
        except (InvalidRecord,TypeError,KeyError):
            return self._result('REJECTED','INVALID_INPUT')
        except CouncilError as e:
            return self._result(e.status,e.code)

    def resolve_case(self, case_id: str, selected_proposal: Mapping[str, Any],
                     compliance: Decision) -> Result:
        try:
            ident(case_id); selected_id=validate_selected(selected_proposal)
            if (not isinstance(compliance, Decision) or compliance.status not in
                    ('ALLOW','DENY','OWNER_ACTION_REQUIRED','HOLD_LOCAL','UNKNOWN') or
                compliance.allowed != (compliance.status == 'ALLOW')):
                raise InvalidRecord('invalid compliance decision')
            def read(tx):
                row=self._case(tx,case_id)
                prop=tx.execute('''SELECT * FROM v1_council_proposals
                      WHERE case_id=? AND proposal_id=?''',(case_id,selected_id)).fetchone()
                if prop is None:
                    raise CouncilError('NOT_FOUND','NOT_FOUND')
                return dict(row),dict(prop)
            row,prop=self._db.read(read)
            original=self._load(row['conflict_digest'])
            snap=self._snapshot(original['project_id'],row['feature_id'],row['bible_pair_digest'])
            record=self._load(prop['proposal_digest'])
            # The caller-supplied Decision is never itself authority. Re-assess
            # through CAB-D and require exact observed evidence references.
            provenance=self._bible.verify_clause_provenance(
                record['clause_refs'],row['bible_pair_digest'])
            if (not isinstance(provenance, Decision) or provenance.status != 'ALLOW' or
                provenance.allowed is not True or provenance.code != 'OK' or
                not provenance.evidence_refs):
                raise CouncilError('REJECTED','BIBLE_CONFLICT')
            assessment=self._authority.assess_bible_conflict(
                row['feature_id'],record,row['bible_pair_digest'])
            if not isinstance(assessment,Result):
                raise CouncilError('NOT_VERIFIED','EVIDENCE_INVALID')
            if assessment.status != 'OK' or assessment.code != 'OK':
                if assessment.code == 'OWNER_ACTION_REQUIRED':
                    # Preserve the attempted choice, CAB-D's decision and all
                    # evidence even though this council cannot amend a bible.
                    escalation = {'case_id':case_id, 'proposal_digest':prop['proposal_digest'],
                       'status':assessment.status, 'code':assessment.code,
                       'bible_pair_digest':row['bible_pair_digest'],
                       'provenance_refs':list(provenance.evidence_refs),
                       'assessment_refs':list(assessment.evidence_refs)}
                    escalation_hash=self._store(escalation)
                    def escalate(tx):
                        case=self._case(tx,case_id)
                        self._feature(tx,case['feature_id'],original['project_id'],case['bible_pair_digest'],
                                      snap,original['feature_contract_digest'],original['graph_revision'])
                        if case['state'] == 'OPEN':
                            tx.execute('UPDATE v1_council_proposals SET compliance_ref=? WHERE case_id=? AND proposal_id=?',
                                      (f'cas:{escalation_hash}',case_id,selected_id))
                            tx.execute('UPDATE v1_council_cases SET state=? WHERE case_id=?',
                                       ('OWNER_ACTION_REQUIRED',case_id))
                        elif case['state'] != 'OWNER_ACTION_REQUIRED':
                            raise CouncilError('CONFLICT','REVISION_CONFLICT')
                        else:
                            already=tx.execute('SELECT compliance_ref FROM v1_council_proposals WHERE proposal_id=?',
                                               (selected_id,)).fetchone()
                            if already is None or already['compliance_ref'] != f'cas:{escalation_hash}':
                                raise CouncilError('CONFLICT','REVISION_CONFLICT')
                    self._db.write(escalate)
                    return self._result('REJECTED','OWNER_ACTION_REQUIRED',
                                         {'case_id':case_id,'route':'CAB-D/OWNER'},
                                         (f'cas:{escalation_hash}',))
                raise CouncilError('REJECTED', assessment.code if assessment.code in
                                   {'BIBLE_CONFLICT','SCOPE_EXCEEDED'} else 'EVIDENCE_INVALID')
            if (not assessment.evidence_refs or compliance.status != 'ALLOW' or
                compliance.allowed is not True or compliance.code != 'OK' or
                compliance.affected_ref != prop['proposal_digest'] or
                tuple(compliance.evidence_refs) != tuple(assessment.evidence_refs)):
                raise CouncilError('REJECTED','EVIDENCE_INVALID')
            audit={'proposal_digest':prop['proposal_digest'],'case_id':case_id,
                   'bible_pair_digest':row['bible_pair_digest'],
                   'provenance_refs':list(provenance.evidence_refs),
                   'assessment_refs':list(assessment.evidence_refs)}
            audit_hash=self._store(audit)
            def write(tx):
                case=self._case(tx,case_id)
                self._feature(tx,case['feature_id'],original['project_id'],case['bible_pair_digest'],
                              snap,original['feature_contract_digest'],original['graph_revision'])
                if case['conflict_digest'] != row['conflict_digest']:
                    raise CouncilError('CONFLICT','REVISION_CONFLICT')
                chosen=tx.execute('SELECT * FROM v1_council_proposals WHERE case_id=? AND selected=1',
                                  (case_id,)).fetchall()
                if chosen:
                    if len(chosen)!=1 or chosen[0]['proposal_id'] != selected_id or \
                        chosen[0]['compliance_ref'] != f'cas:{audit_hash}':
                        raise CouncilError('CONFLICT','REVISION_CONFLICT')
                    return
                if case['state'] != 'OPEN':
                    raise CouncilError('CONFLICT','REVISION_CONFLICT')
                self._check_cabals(tx,original['project_id'],case['bible_pair_digest'],
                                   {prop['author_cabal_id']})
                tx.execute('''UPDATE v1_council_proposals SET selected=1,compliance_ref=?
                              WHERE proposal_id=? AND case_id=?''',
                              (f'cas:{audit_hash}',selected_id,case_id))
                tx.execute('UPDATE v1_council_cases SET state=? WHERE case_id=?',
                           ('RESOLVED',case_id))
            self._db.write(write)
            return self._result('OK','OK', {'case_id':case_id,'state':'RESOLVED',
                    'selected_proposal_id':selected_id,'advisory_only':True},
                    (f'cas:{audit_hash}',)+tuple(assessment.evidence_refs))
        except (InvalidRecord,TypeError,KeyError):
            return self._result('REJECTED','INVALID_INPUT')
        except CouncilError as e:
            return self._result(e.status,e.code)

    def get_recommendation(self, case_id: str) -> Result:
        try:
            ident(case_id)
            def read(tx):
                case=self._case(tx,case_id)
                proposals=tx.execute('''SELECT * FROM v1_council_proposals
                  WHERE case_id=? ORDER BY proposal_id''',(case_id,)).fetchall()
                dissent=tx.execute('''SELECT * FROM v1_council_dissent
                  WHERE case_id=? ORDER BY dissent_id''',(case_id,)).fetchall()
                return dict(case),[dict(p) for p in proposals],[dict(d) for d in dissent]
            case,proposals,dissent=self._db.read(read)
            original=self._load(case['conflict_digest'])
            restored_proposals=[]
            restored_dissent=[]
            evidence={f"cas:{case['conflict_digest']}"}
            for p in proposals:
                content=self._load(p['proposal_digest'])
                if (content['proposal_id'] != p['proposal_id'] or
                    content['author_cabal_id'] != p['author_cabal_id']):
                    raise CouncilError('NOT_VERIFIED','EVIDENCE_INVALID')
                proof=None
                if p['compliance_ref'] is not None:
                    ref=p['compliance_ref']
                    if not ref.startswith('cas:'):
                        raise CouncilError('NOT_VERIFIED','EVIDENCE_INVALID')
                    proof=self._load(ref[4:]); evidence.add(ref)
                evidence.add(f"cas:{p['proposal_digest']}")
                restored_proposals.append({'proposal':content,'selected':bool(p['selected']),
                                          'compliance':proof})
            for d in dissent:
                content=self._load(d['reason_digest'])
                if content['dissent_id']!=d['dissent_id'] or content['author_id']!=d['author_id']:
                    raise CouncilError('NOT_VERIFIED','EVIDENCE_INVALID')
                evidence.add(f"cas:{d['reason_digest']}")
                restored_dissent.append(content)
            chosen=[p['proposal']['proposal_id'] for p in restored_proposals if p['selected']]
            if len(chosen)>1 or (case['state']=='RESOLVED')!=(len(chosen)==1):
                raise CouncilError('NOT_VERIFIED','EVIDENCE_INVALID')
            return self._result('OK','OK',{'case_id':case_id,'feature_id':case['feature_id'],
                  'project_id':case['project_id'],'bible_pair_digest':case['bible_pair_digest'],
                  'state':case['state'],'conflict':original,'proposals':restored_proposals,
                  'dissent':restored_dissent,'selected_proposal_id':chosen[0] if chosen else None,
                  'advisory_only':True,'owner_route':case['state']=='OWNER_ACTION_REQUIRED'},
                  tuple(sorted(evidence)))
        except (InvalidRecord,TypeError,KeyError):
            return self._result('NOT_VERIFIED','EVIDENCE_INVALID')
        except CouncilError as e:
            return self._result(e.status,e.code)


class CouncilError(Exception):
    def __init__(self,status: str,code: str):
        self.status,self.code=status,code
        super().__init__(code)
