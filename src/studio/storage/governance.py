"""Production A governance backed by C's single APSW writer and frozen tables.

J authenticates bearer identity before entry. Every command rechecks current
principal/project grants, writes its audit event and idempotent result together.
Original Bible bytes are verified by G's trusted content-addressed blob source.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from studio.governance.model import GovernanceError, Intent
from studio.governance.service import (_id, _text, _hex, digest, parse_manifest,
                                       ALLOWED_KINDS, SUBJECT_TYPES, DISPOSITIONS)
from studio.workforce import WorkforceFault
from .workforce import WorkforceStorage


class GovernanceAPSWService:
    operation_ids = (
        "listBibles", "createBible", "stageBible", "diffBible", "publishBible",
        "getCanon", "requestDecision", "resolveDecision", "listDecisions",
        "getProjectPolicy", "setProjectPolicy", "listProjectGrants",
        "grantProjectAccess", "revokeProjectAccess",
    )

    def __init__(self, writer: WorkforceStorage, *, blobs, impacts):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError("A requires real C APSW writer")
        if not callable(getattr(blobs,"read_verified",None)):
            raise TypeError("A requires verified original Bible bytes")
        if not callable(getattr(impacts,"referenced_tasks",None)):
            raise TypeError("A requires actual task impact catalog")
        self.writer,self.blobs,self.impacts=writer,blobs,impacts

    @staticmethod
    def _actor(tx,actor,project,owner=False):
        if not isinstance(actor,dict):
            raise GovernanceError("forbidden","authenticated J actor required")
        principal=actor.get("principal_id")
        issuer=actor.get("issuer")
        proof=actor.get("trusted_proof_ref")
        projects=actor.get("allowed_project_ids")
        if not all(isinstance(v,str) and v for v in (principal,issuer,proof)):
            raise GovernanceError("forbidden","incomplete authenticated identity")
        if not isinstance(projects,(list,tuple,set,frozenset)) or project not in projects:
            raise GovernanceError("forbidden","project not authorized by J")
        row=tx.one("SELECT kind FROM principals WHERE principal_id=? AND auth_issuer=?",
                   (principal,issuer))
        if row is None:
            raise GovernanceError("forbidden","principal identity is not registered")
        if owner and (row["kind"]!="human" or actor.get("owner_verified") is not True):
            raise GovernanceError("requires_owner","verified human owner required")
        condition=" AND grant_code='owner'" if owner else ""
        membership=tx.one("SELECT 1 AS ok FROM project_grants "
                          "WHERE project_id=? AND principal_id=?"+condition+" LIMIT 1",
                          (project,principal))
        if membership is None:
            raise GovernanceError("requires_owner" if owner else "forbidden",
                                  "current project grant is absent")
        return principal

    def _read(self,actor,project,fn,*,owner=False):
        return self.writer.read(project,lambda tx:fn(tx,self._actor(tx,actor,project,owner)))

    def _mutate(self,actor,project,name,request,fn,*,owner=False):
        request_id=_text(request.get("request_id"),"request id")
        request_digest=digest(request)
        # Include principal in the idempotency preimage. An owner-scoped key
        # cannot be replayed as another authenticated principal.
        principal=actor.get("principal_id") if isinstance(actor,dict) else None
        identity_hash=digest([principal,name,request_digest])
        def action(tx):
            verified=self._actor(tx,actor,project,owner)
            return fn(tx,verified)
        try:
            return self.writer.command(project,"A."+name,request_id,identity_hash,action)
        except WorkforceFault as exc:
            raise GovernanceError(
                {"conflict":"conflict","unknown":"unknown_effect",
                 "invalid":"invalid","forbidden":"forbidden"}.get(exc.code,"internal"),
                str(exc)) from exc

    @staticmethod
    def _audit(tx,project,principal,event,subject,payload):
        row=tx.one("SELECT COALESCE(MAX(event_seq),0)+1 AS n "
                   "FROM audit_events WHERE project_id=?", (project,))
        tx.change("INSERT INTO audit_events(project_id,event_seq,event_type,subject_type,"
                  "subject_id,payload_digest,actor_principal_id) VALUES(?,? ,?,'governance',?,?,?)",
                  (project,row["n"],event,subject,digest(payload),principal))

    @staticmethod
    def _current(tx,project,kind):
        return tx.one("SELECT a.bible_id,a.edition_id FROM active_bible_editions a "
                      "WHERE a.project_id=? AND a.kind=? ORDER BY a.rowid DESC LIMIT 1",
                      (project,kind))

    @staticmethod
    def _snapshot(tx,project):
        return tx.one("SELECT * FROM canon_snapshots WHERE project_id=? "
                      "ORDER BY rowid DESC LIMIT 1",(project,))

    @staticmethod
    def _make_snapshot(tx,project):
        design=GovernanceAPSWService._current(tx,project,"design")
        technical=GovernanceAPSWService._current(tx,project,"technical")
        if design is None or technical is None:
            return ""
        policy=tx.one("SELECT policy_digest FROM project_policies WHERE project_id=? "
                      "AND active=1 ORDER BY revision DESC LIMIT 1",(project,))
        policy_digest=policy["policy_digest"] if policy else digest({"policy":"unconfigured"})
        pairs=[(design["bible_id"],design["edition_id"]),
               (technical["bible_id"],technical["edition_id"])]
        manifest=digest({"project_id":project,"editions":pairs,"policy":policy_digest})
        snapshot_id=_id("canon",project,manifest)
        tx.change("INSERT OR IGNORE INTO canon_snapshots(project_id,snapshot_id,design_bible_id,"
                  "design_edition_id,technical_bible_id,technical_edition_id,policy_digest,"
                  "manifest_digest) VALUES(?,?,?,?,?,?,?,?)",
                  (project,snapshot_id,pairs[0][0],pairs[0][1],pairs[1][0],pairs[1][1],
                   policy_digest,manifest))
        return snapshot_id

    def listBibles(self,actor,project_id):
        def run(tx,p):
            rows=tx.all("SELECT bible_id,kind FROM bibles WHERE project_id=? ORDER BY bible_id",
                        (project_id,))
            for row in rows:
                active=self._current(tx,project_id,row["kind"])
                row["active_edition_id"]=active["edition_id"] if active and active["bible_id"]==row["bible_id"] else ""
            return {"bibles":rows}
        return self._read(actor,project_id,run)

    def createBible(self,actor,project_id,request):
        def run(tx,p):
            kind=request.get("kind")
            if kind not in ALLOWED_KINDS:
                raise GovernanceError("invalid","Bible kind must be design or technical")
            title=_text(request.get("title"),"Bible title")
            bible_id=_id("bible",project_id,request["request_id"])
            tx.change("INSERT INTO bibles(project_id,bible_id,kind,title) VALUES(?,?,?,?)",
                      (project_id,bible_id,kind,title))
            self._audit(tx,project_id,p,"bible.created",bible_id,{"kind":kind,"title":title})
            return {"bible_id":bible_id,"kind":kind}
        return self._mutate(actor,project_id,"createBible",request,run)

    def stageBible(self,actor,project_id,bible_id,request):
        def run(tx,p):
            bible=tx.one("SELECT kind FROM bibles WHERE project_id=? AND bible_id=?",
                         (project_id,bible_id))
            if bible is None:
                raise GovernanceError("missing","Bible does not exist")
            active=self._current(tx,project_id,bible["kind"])
            expected=request.get("expected_edition_id") or None
            if (active["edition_id"] if active else None)!=expected:
                raise GovernanceError("stale_revision","staged Bible base revision mismatch")
            content=_hex(request.get("content_digest"))
            manifest=_hex(request.get("manifest_digest"))
            for sha,purpose in ((content,"bible-content"),(manifest,"bible-manifest")):
                row=tx.one("SELECT 1 AS verified FROM project_blobs p JOIN blobs b "
                           "ON b.content_digest=p.content_digest WHERE p.project_id=? "
                           "AND p.content_digest=? AND p.purpose=? AND b.durability='verified'",
                           (project_id,sha,purpose))
                if row is None:
                    raise GovernanceError("forbidden","Bible byte association not verified")
            content_bytes=self.blobs.read_verified(project_id,content,"bible-content")
            manifest_bytes=self.blobs.read_verified(project_id,manifest,"bible-manifest")
            if hashlib.sha256(content_bytes).hexdigest()!=content or hashlib.sha256(manifest_bytes).hexdigest()!=manifest:
                raise GovernanceError("conflict","verified Bible bytes differ")
            content_bytes.decode("utf-8",errors="strict")
            parse_manifest(manifest_bytes)
            intent_id=_id("intent",project_id,bible_id,request["request_id"])
            tx.change("INSERT INTO bible_publication_intents(project_id,intent_id,bible_id,"
                      "content_digest,manifest_digest,expected_edition_id,request_id,state)"
                      " VALUES(?,?,?,?,?,?,?,'verified')",
                      (project_id,intent_id,bible_id,content,manifest,expected,request["request_id"]))
            self._audit(tx,project_id,p,"bible.staged_verified",intent_id,{"content_digest":content,"manifest_digest":manifest})
            return {"intent_id":intent_id,"state":"verified"}
        return self._mutate(actor,project_id,"stageBible",request,run)

    def diffBible(self,actor,project_id,bible_id):
        def run(tx,p):
            bible=tx.one("SELECT kind FROM bibles WHERE project_id=? AND bible_id=?",
                         (project_id,bible_id))
            if bible is None:
                raise GovernanceError("missing","Bible not found")
            staged=tx.one("SELECT manifest_digest FROM bible_publication_intents "
                          "WHERE project_id=? AND bible_id=? AND state='verified' "
                          "ORDER BY rowid DESC LIMIT 1",(project_id,bible_id))
            if staged is None:
                raise GovernanceError("missing","no verified staged Bible edition")
            incoming=parse_manifest(self.blobs.read_verified(project_id,staged["manifest_digest"],"bible-manifest"))
            active=self._current(tx,project_id,bible["kind"])
            old={}
            if active:
                row=tx.one("SELECT manifest_digest FROM bible_editions WHERE project_id=? "
                           "AND bible_id=? AND edition_id=?",
                           (project_id,active["bible_id"],active["edition_id"]))
                manifest=parse_manifest(self.blobs.read_verified(project_id,row["manifest_digest"],"bible-manifest"))
                old={clause.clause_key:clause for clause in manifest.clauses}
            new={clause.clause_key:clause for clause in incoming.clauses}
            changed={key for key in old.keys()|new.keys() if old.get(key)!=new.get(key)}
            impacted=set(self.impacts.referenced_tasks(project_id,changed))
            for key in changed:
                for source in (old,new):
                    if key in source:
                        impacted.update(source[key].impacted_task_ids)
            return {"changed_clause_ids":sorted(changed),"impacted_task_ids":sorted(impacted)}
        return self._read(actor,project_id,run)

    def publishBible(self,actor,project_id,bible_id,request):
        def run(tx,p):
            bible=tx.one("SELECT kind FROM bibles WHERE project_id=? AND bible_id=?",
                         (project_id,bible_id))
            intent=tx.one("SELECT * FROM bible_publication_intents WHERE project_id=? "
                          "AND intent_id=? AND bible_id=? AND state='verified'",
                          (project_id,request.get("intent_id"),bible_id))
            if bible is None or intent is None:
                raise GovernanceError("missing","verified publication intent absent")
            kind=bible["kind"]
            active=self._current(tx,project_id,kind)
            expected=request.get("expected_edition_id") or None
            if expected!=intent["expected_edition_id"] or expected!=(active["edition_id"] if active else None):
                raise GovernanceError("stale_revision","Bible base changed")
            obj=Intent(intent["intent_id"],bible_id,intent["request_id"],intent["content_digest"],
                       intent["manifest_digest"],expected,"verified",0)
            from studio.governance.service import Governance
            effect_digest=_hex(request.get("effect_digest"))
            if Governance.publication_effect(project_id,bible_id,obj)!=effect_digest:
                raise GovernanceError("conflict","publication effect digest differs")
            decision_id=_text(request.get("decision_id"),"decision id")
            current=self._snapshot(tx,project_id)
            decision=tx.one("SELECT * FROM owner_decisions WHERE project_id=? AND decision_id=?",
                            (project_id,decision_id))
            if decision is None or decision["state"]!="approved" or (
                    decision["subject_type"],decision["subject_id"],decision["action"],decision["effect_digest"]
                    )!=("bible",bible_id,"bible.publish",effect_digest):
                raise GovernanceError("requires_owner","exact approved owner decision required")
            if decision["snapshot_id"]!=(current["snapshot_id"] if current else None):
                raise GovernanceError("stale_snapshot","approval Canon snapshot changed")
            if tx.one("SELECT 1 AS consumed FROM approval_consumptions "
                      "WHERE project_id=? AND decision_id=?",(project_id,decision_id)):
                raise GovernanceError("conflict","owner approval already consumed")
            manifest=parse_manifest(self.blobs.read_verified(project_id,intent["manifest_digest"],"bible-manifest"))
            original=self.blobs.read_verified(project_id,intent["content_digest"],"bible-content")
            if hashlib.sha256(original).hexdigest()!=intent["content_digest"]:
                raise GovernanceError("conflict","Bible content changed")
            edition_id=_id("edition",project_id,bible_id,intent["content_digest"],intent["manifest_digest"])
            effect_id=_id("effect",project_id,request["request_id"],effect_digest)
            tx.change("INSERT INTO effect_intents(project_id,effect_id,subject_type,subject_id,action,"
                      "effect_digest,snapshot_id,decision_id,approval_required,state,provider_key,request_id)"
                      " VALUES(?,?,'bible',?,'bible.publish',?,?,?,1,'prepared','github',?)",
                      (project_id,effect_id,bible_id,effect_digest,decision["snapshot_id"],
                       decision_id,"A.bible.publish."+request["request_id"]))
            tx.change("INSERT INTO approval_consumptions(project_id,decision_id,effect_id,consumed_at)"
                      " VALUES(?,?,?,datetime('now'))",(project_id,decision_id,effect_id))
            tx.change("INSERT INTO bible_editions(project_id,bible_id,edition_id,kind,content_digest,"
                      "manifest_digest,owner_decision_id,publication_effect_digest,published_at)"
                      " VALUES(?,?,?,?,?,?,?,?,datetime('now'))",
                      (project_id,bible_id,edition_id,kind,intent["content_digest"],
                       intent["manifest_digest"],decision_id,effect_digest))
            for clause in manifest.clauses:
                tx.change("INSERT INTO bible_clauses(project_id,bible_id,edition_id,clause_key,"
                          "classification,text_digest,scope_digest,provenance_digest) "
                          "VALUES(?,?,?,?,?,?,?,?)",
                          (project_id,bible_id,edition_id,clause.clause_key,clause.classification,
                           clause.text_digest,clause.scope_digest,clause.provenance_digest))
            # Only one active Bible per kind; historical editions remain immutable.
            tx.change("DELETE FROM active_bible_editions WHERE project_id=? AND kind=?",
                      (project_id,kind))
            tx.change("INSERT INTO active_bible_editions(project_id,bible_id,edition_id,kind)"
                      " VALUES(?,?,?,?)",(project_id,bible_id,edition_id,kind))
            tx.change("UPDATE bible_publication_intents SET state='committed' WHERE "
                      "project_id=? AND intent_id=?",(project_id,intent["intent_id"]))
            snapshot_id=self._make_snapshot(tx,project_id)
            self._audit(tx,project_id,p,"bible.published",edition_id,{"snapshot_id":snapshot_id,"effect_digest":effect_digest})
            return {"edition_id":edition_id,"snapshot_id":snapshot_id}
        return self._mutate(actor,project_id,"publishBible",request,run)

    def getCanon(self,actor,project_id):
        def run(tx,p):
            row=self._snapshot(tx,project_id)
            if row is None:
                raise GovernanceError("missing","published Canon does not exist")
            return {"snapshot_id":row["snapshot_id"],"design_edition_id":row["design_edition_id"],
                    "technical_edition_id":row["technical_edition_id"]}
        return self._read(actor,project_id,run)

    def requestDecision(self,actor,project_id,request):
        def run(tx,p):
            typ=request.get("subject_type")
            subject=_text(request.get("subject_id"),"decision subject")
            action=_text(request.get("action"),"decision action")
            sha=_hex(request.get("effect_digest"))
            tables={"project":("projects","project_id"),"bible":("bibles","bible_id"),
                    "feature":("features","feature_id"),"task":("tasks","task_id"),
                    "cabal":("cabals","cabal_id"),"artifact":("artifacts","artifact_id"),
                    "release":("releases","release_id")}
            if typ not in SUBJECT_TYPES:
                raise GovernanceError("invalid","unknown decision subject type")
            table,col=tables[typ]
            if tx.one(f"SELECT 1 AS ok FROM {table} WHERE project_id=? AND {col}=?",
                      (project_id,subject)) is None:
                raise GovernanceError("missing","decision subject not found")
            snapshot_id=request.get("snapshot_id") or None
            if snapshot_id and tx.one("SELECT 1 AS ok FROM canon_snapshots "
                                      "WHERE project_id=? AND snapshot_id=?",
                                      (project_id,snapshot_id)) is None:
                raise GovernanceError("stale_snapshot","decision Canon unknown")
            decision_id=_id("decision",project_id,request["request_id"])
            tx.change("INSERT INTO decision_proposals(project_id,proposal_id,subject_type,"
                      "subject_id,action,effect_digest,snapshot_id,requester_principal_id,"
                      "state,created_at) VALUES(?,?,?,?,?,?,?,?,'pending',datetime('now'))",
                      (project_id,decision_id,typ,subject,action,sha,snapshot_id,p))
            self._audit(tx,project_id,p,"owner_decision.requested",decision_id,{"effect_digest":sha})
            return {"decision_id":decision_id,"state":"pending"}
        return self._mutate(actor,project_id,"requestDecision",request,run)

    def resolveDecision(self,actor,project_id,decision_id,request):
        def run(tx,p):
            proposal=tx.one("SELECT * FROM decision_proposals WHERE project_id=? AND proposal_id=?",
                            (project_id,decision_id))
            if proposal is None:
                raise GovernanceError("missing","decision proposal absent")
            if request.get("expected_revision")!=proposal["revision"]:
                raise GovernanceError("stale_revision","decision revision mismatch")
            if request.get("expected_effect_digest")!=proposal["effect_digest"]:
                raise GovernanceError("conflict","decision effect identity changed")
            choice=request.get("decision")
            if choice not in ("approve","reject","revoke"):
                raise GovernanceError("invalid","invalid owner resolution")
            if choice=="revoke":
                prior=tx.one("SELECT state FROM owner_decisions WHERE project_id=? AND decision_id=?",
                             (project_id,decision_id))
                if prior is None or prior["state"]!="approved" or tx.one(
                        "SELECT 1 AS ok FROM approval_consumptions WHERE project_id=? AND decision_id=?",
                        (project_id,decision_id)) is not None:
                    raise GovernanceError("conflict","owner decision cannot be revoked")
                tx.change("UPDATE owner_decisions SET state='revoked' "
                          "WHERE project_id=? AND decision_id=?",(project_id,decision_id))
                tx.change("UPDATE decision_proposals SET revision=revision+1 "
                          "WHERE project_id=? AND proposal_id=?",(project_id,decision_id))
                state="revoked"
            else:
                if proposal["state"]!="pending":
                    raise GovernanceError("conflict","owner proposal already resolved")
                current=self._snapshot(tx,project_id)
                if proposal["snapshot_id"] is not None and proposal["snapshot_id"]!=(
                        current["snapshot_id"] if current else None):
                    raise GovernanceError("stale_snapshot","proposal Canon changed")
                state="approved" if choice=="approve" else "rejected"
                proof=digest({"issuer":actor["issuer"],"proof_ref":actor["trusted_proof_ref"],
                              "decision_id":decision_id,"effect_digest":proposal["effect_digest"]})
                tx.change("INSERT INTO owner_decisions(project_id,decision_id,subject_type,"
                          "subject_id,action,effect_digest,snapshot_id,verified_owner_principal_id,"
                          "proof_digest,state) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (project_id,decision_id,proposal["subject_type"],proposal["subject_id"],
                           proposal["action"],proposal["effect_digest"],proposal["snapshot_id"],
                           p,proof,state))
            self._audit(tx,project_id,p,"owner_decision.resolved",decision_id,{"state":state,"effect_digest":proposal["effect_digest"]})
            return {"decision_id":decision_id,"state":state}
        return self._mutate(actor,project_id,"resolveDecision",request,run,owner=True)

    def listDecisions(self,actor,project_id):
        def run(tx,p):
            rows=tx.all("SELECT p.proposal_id AS decision_id,p.subject_type,p.subject_id,"
                        "p.action,p.effect_digest,COALESCE(d.state,p.state) AS state "
                        "FROM decision_proposals p LEFT JOIN owner_decisions d "
                        "ON d.project_id=p.project_id AND d.decision_id=p.proposal_id "
                        "WHERE p.project_id=? ORDER BY p.proposal_id",(project_id,))
            return {"decisions":rows}
        return self._read(actor,project_id,run)

    def getProjectPolicy(self,actor,project_id):
        def run(tx,p):
            row=tx.one("SELECT policy_id,revision,policy_digest FROM project_policies "
                       "WHERE project_id=? AND active=1 ORDER BY revision DESC LIMIT 1",
                       (project_id,))
            if row is None:
                raise GovernanceError("missing","project policy not selected")
            return row
        return self._read(actor,project_id,run)

    def setProjectPolicy(self,actor,project_id,request):
        def run(tx,p):
            policy=tx.one("SELECT revision FROM project_policies WHERE project_id=? "
                          "AND active=1 ORDER BY revision DESC LIMIT 1",(project_id,))
            revision=policy["revision"] if policy else 0
            if type(request.get("expected_revision")) is not int or request["expected_revision"]!=revision:
                raise GovernanceError("stale_revision","policy revision changed")
            raw=request.get("rules")
            if not isinstance(raw,list) or len(raw)>10000:
                raise GovernanceError("invalid","policy rule list invalid")
            seen=set()
            entries=[]
            for rule in raw:
                if not isinstance(rule,dict):
                    raise GovernanceError("invalid","invalid rule")
                key=_text(rule.get("rule_id"),"rule id")
                if key in seen:
                    raise GovernanceError("conflict","duplicate policy rule")
                seen.add(key)
                disp=rule.get("disposition")
                if disp not in DISPOSITIONS:
                    raise GovernanceError("invalid","unknown policy disposition")
                entries.append({"rule_id":key,
                    "subject_type":_text(rule.get("subject_type"),"subject type"),
                    "action":_text(rule.get("action"),"rule action"),
                    "scope_digest":_hex(rule.get("scope_digest")),
                    "disposition":disp})
            entries.sort(key=lambda row:row["rule_id"])
            computed=digest(entries)
            if request.get("policy_digest")!=computed:
                raise GovernanceError("conflict","policy digest and rules differ")
            policy_id=_id("policy",project_id,revision+1,computed)
            tx.change("UPDATE project_policies SET active=0 WHERE project_id=? AND active=1",(project_id,))
            tx.change("INSERT INTO project_policies(project_id,policy_id,revision,policy_digest,active) "
                      "VALUES(?,?,?,?,1)",(project_id,policy_id,revision+1,computed))
            for rule in entries:
                tx.change("INSERT INTO project_policy_rules(project_id,policy_id,rule_id,subject_type,"
                          "action,scope_digest,disposition) VALUES(?,?,?,?,?,?,?)",
                          (project_id,policy_id,rule["rule_id"],rule["subject_type"],
                           rule["action"],rule["scope_digest"],rule["disposition"]))
            self._make_snapshot(tx,project_id)
            self._audit(tx,project_id,p,"policy.updated",policy_id,
                        {"revision":revision+1,"digest":computed})
            return {"policy_id":policy_id,"revision":revision+1,"policy_digest":computed}
        return self._mutate(actor,project_id,"setProjectPolicy",request,run,owner=True)

    def listProjectGrants(self,actor,project_id):
        def run(tx,p):
            is_owner=tx.one("SELECT 1 AS ok FROM project_grants WHERE project_id=? "
                            "AND principal_id=? AND grant_code='owner'",(project_id,p)) is not None
            if is_owner:
                rows=tx.all("SELECT principal_id,grant_code FROM project_grants WHERE project_id=? "
                            "ORDER BY principal_id,grant_code",(project_id,))
            else:
                rows=tx.all("SELECT principal_id,grant_code FROM project_grants WHERE project_id=? "
                            "AND principal_id=? ORDER BY grant_code",(project_id,p))
            return {"grants":rows}
        return self._read(actor,project_id,run)

    def grantProjectAccess(self,actor,project_id,request):
        def run(tx,p):
            target=_text(request.get("principal_id"),"target principal")
            code=_text(request.get("grant_code"),"grant code")
            if tx.one("SELECT 1 AS ok FROM principals WHERE principal_id=?",(target,)) is None:
                raise GovernanceError("missing","target principal not registered")
            tx.change("INSERT INTO project_grants(project_id,principal_id,grant_code)"
                      " VALUES(?,?,?)",(project_id,target,code))
            self._audit(tx,project_id,p,"project_grant.created",target,{"grant_code":code})
            return {"principal_id":target,"grant_code":code}
        return self._mutate(actor,project_id,"grantProjectAccess",request,run,owner=True)

    def revokeProjectAccess(self,actor,project_id,request):
        def run(tx,p):
            target=_text(request.get("principal_id"),"target principal")
            code=_text(request.get("grant_code"),"grant code")
            if target==p and code=="owner":
                raise GovernanceError("forbidden","owner cannot remove own last grant")
            if tx.change("DELETE FROM project_grants WHERE project_id=? AND principal_id=? "
                         "AND grant_code=?",(project_id,target,code))!=1:
                raise GovernanceError("missing","project grant does not exist")
            self._audit(tx,project_id,p,"project_grant.revoked",target,{"grant_code":code})
            return {"principal_id":target,"grant_code":code}
        return self._mutate(actor,project_id,"revokeProjectAccess",request,run,owner=True)
