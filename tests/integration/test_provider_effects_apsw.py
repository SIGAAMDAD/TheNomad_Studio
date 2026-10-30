"""G effect reconciliation persists readback, UNKNOWN and conflicts in APSW."""
import pytest
apsw=pytest.importorskip("apsw")

from studio.integrations.common import IntegrationError
from studio.integrations.effects import EffectObservation, EffectReconciler
from studio.integrations.common import ProjectBinding
from studio.storage.provider_effects import EffectAPSWRepository
from studio.storage.workforce import WorkforceStorage


def test_exact_effect_readback_and_reconciliation(tmp_path):
    db=WorkforceStorage.open(tmp_path/"effects.sqlite",fresh=True)
    try:
        c=db._connection
        c.execute("INSERT INTO projects VALUES('p1','Project','digest',1,'active')")
        c.execute("INSERT INTO effect_intents(project_id,effect_id,subject_type,subject_id,"
                  "action,effect_digest,approval_required,state,provider_key,request_id) "
                  "VALUES('p1','effect','task','t1','build.dispatch',?,0,'dispatched','github','req')",
                  ("a"*64,))
        repo=EffectAPSWRepository(db,lambda project,provider,typ,subject,action:
                                  ("repo@main",".nomad/effects/effect.json"))
        binding=ProjectBinding("p1","github-account")
        rec=EffectReconciler(binding,repo)
        intent=repo.load("p1","effect")
        assert intent.expected_sha256=="a"*64
        assert intent.path==".nomad/effects/effect.json"
        assert rec.reconcile_effect(project_id="p1",effect_id="effect",
                readback=lambda _:None,expected_state="PENDING").state=="UNKNOWN"
        assert db.read("p1",lambda tx:tx.one(
            "SELECT state FROM effect_intents WHERE project_id=? AND effect_id=?",
            ("p1","effect")))["state"]=="unknown"
        with pytest.raises(IntegrationError,match="changed"):
            rec.reconcile_effect(project_id="p1",effect_id="effect",
                readback=lambda _:("remote",b"wrong"),expected_state="PENDING")
        # Exact bytes must match source SHA; a mismatching body is a persisted
        # conflict, never silently treated as successful readback.
        conflict=rec.reconcile_effect(project_id="p1",effect_id="effect",
            readback=lambda _:("remote",b"wrong"),expected_state="UNKNOWN")
        assert conflict.state=="CONFLICT"
        assert db.read("p1",lambda tx:tx.one(
            "SELECT state FROM effect_intents WHERE project_id=? AND effect_id=?",
            ("p1","effect")))["state"]=="failed"
        with pytest.raises(IntegrationError,match="changed"):
            repo.store_observation("p1",EffectObservation("effect","VERIFIED","remote","a"*64),"PENDING")
        with pytest.raises(IntegrationError,match="does not exist"):
            repo.load("p2","effect")
    finally:
        db.close()
