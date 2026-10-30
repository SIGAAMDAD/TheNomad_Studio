"""Build dispatch claims and CI readback use actual APSW durable state."""
import pytest
apsw=pytest.importorskip("apsw")

from studio.integrations.ci import BuildObservation
from studio.integrations.common import IntegrationError
from studio.storage.builds import BuildAPSWRepository
from studio.storage.workforce import WorkforceStorage


def test_one_remote_dispatch_claim_and_verified_observation(tmp_path):
    path=tmp_path/"build.sqlite"
    writer=WorkforceStorage.open(path,fresh=True)
    try:
        db=writer._connection
        db.execute("INSERT INTO projects(project_id,name,charter_digest,state) "
                   "VALUES('p1','Project','digest','active')")
        db.execute("INSERT INTO project_repositories VALUES('p1','repo','github','main',NULL)")
        db.execute("INSERT INTO source_revisions VALUES(?,?,?,?,?,'canonical')",
                   ("p1","rev1","repo","a"*40,"tree"))
        store=BuildAPSWRepository(writer)
        kw=dict(project_id="p1",request_id="req1",source_revision_id="rev1",build_target="game")
        build=store.prepare(**kw)
        assert build.source_sha=="a"*40
        assert store.prepare(**kw)==build
        assert store.claim_first_dispatch(project_id="p1",effect_id=build.effect_id)
        assert not store.claim_first_dispatch(project_id="p1",effect_id=build.effect_id)
        with pytest.raises(IntegrationError,match="exact source"):
            store.observe(project_id="p1",ci_id=build.ci_id,
                 run=BuildObservation(100,"b"*40,"completed","success","url"))
        store.observe(project_id="p1",ci_id=build.ci_id,run=None)
        row=writer.read("p1",lambda tx:tx.one(
            "SELECT status,remote_run_id FROM ci_runs WHERE project_id=? AND ci_id=?",
            ("p1",build.ci_id)))
        assert row=={"status":"unknown","remote_run_id":""}
        store.observe(project_id="p1",ci_id=build.ci_id,
            run=BuildObservation(100,"a"*40,"completed","success","url"))
        row=writer.read("p1",lambda tx:tx.one(
            "SELECT status,remote_run_id FROM ci_runs WHERE project_id=? AND ci_id=?",
            ("p1",build.ci_id)))
        assert row=={"status":"passed","remote_run_id":"100"}
        with pytest.raises(IntegrationError,match="ambiguous"):
            store.observe(project_id="p1",ci_id=build.ci_id,
                run=BuildObservation(101,"a"*40,"completed","success","url"))
        assert writer.read("p1",lambda tx:tx.one(
            "SELECT state FROM effect_intents WHERE project_id=? AND effect_id=?",
            ("p1",build.effect_id)))["state"]=="observed"
        assert writer.read("p1",lambda tx:tx.one(
            "SELECT COUNT(*) AS n FROM ci_runs WHERE project_id=?",("p1",)))["n"]==1
    finally:
        writer.close()
    reopened=WorkforceStorage.open(path,fresh=False)
    try:
        assert not BuildAPSWRepository(reopened).claim_first_dispatch(
            project_id="p1",effect_id=build.effect_id)
    finally:
        reopened.close()
