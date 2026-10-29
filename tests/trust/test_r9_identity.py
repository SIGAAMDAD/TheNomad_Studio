import hashlib
from nomad_studio.trust.api import IdentityService
from nomad_studio.trust.port_types import Decision
from r9_helpers import SignedNative, Store

D='a'*64

class Leases:
    def read_active(self,job):
        return {'epoch':3,'principal_id':'worker/1','session_id':'session/1','expires_at_us':5_000_000}

class Permission:
    def __init__(self,native):self.native=native
    def readback(self,pid,provider,action,target):
        ref='native/permission'
        self.native.sign(ref,'PROVIDER_PERMISSION',principal_id=pid,action=action,
                         target_ref=target,allowed=True,credential_id='credential/1',epoch=4,valid_until_us=10_000_000)
        return {'proof_ref':ref}

def service(production=True):
    native=SignedNative(production=production)
    store=Store();store.enroll()
    api=IdentityService(verifier=native.verifier,store=store,clock_us=lambda:1_000_000,
                        leases=Leases(),permission_reader=Permission(native))
    return native,store,api

def test_owner_trusted_proof_and_forgery_and_stale_epoch():
    native,store,api=service()
    native.sign('native/owner','OWNER_COMMAND',principal_id='owner/1',credential_id='credential/1',
        epoch=4,valid_until_us=10_000_000,issued_at_us=900_000,
        principal_kind='OWNER',enrollment_ref='enrollment/1',action_digest=D)
    assert api.authenticate_owner('native/owner',D).status=='OK'
    assert api.authenticate_owner('native/owner','b'*64).code=='UNAUTHENTICATED'
    native.receipts['native/owner']['claims']['action_digest']='b'*64
    assert api.authenticate_owner('native/owner','b'*64).code=='UNAUTHENTICATED'
    native.sign('native/stale','OWNER_COMMAND',principal_id='owner/1',credential_id='credential/1',
        epoch=3,valid_until_us=10_000_000,issued_at_us=900_000,
        principal_kind='OWNER',enrollment_ref='enrollment/1',action_digest=D)
    assert api.authenticate_owner('native/stale',D).code=='UNAUTHENTICATED'
    store.db.execute("UPDATE v1_verified_identities SET epoch=5 WHERE principal_id='owner/1'")
    assert api.authenticate_owner('native/stale',D).code=='UNAUTHENTICATED'

def test_simulated_anchor_never_authenticates_owner():
    native,_,api=service(production=False)
    native.sign('native/mock','OWNER_COMMAND',principal_id='owner/1',credential_id='credential/1',
        epoch=4,valid_until_us=10_000_000,issued_at_us=900_000,
        principal_kind='OWNER',enrollment_ref='enrollment/1',action_digest=D)
    assert api.authenticate_owner('native/mock',D).code=='UNAUTHENTICATED'

def test_worker_valid_lease_and_fenced_epoch():
    native,store,api=service()
    store.enroll('worker/1','worker_credential/1')
    native.sign('native/worker','WORKER_SESSION',principal_id='worker/1',credential_id='worker_credential/1',
        epoch=4,valid_until_us=10_000_000,issued_at_us=900_000,
        principal_kind='WORKER',enrollment_ref='enrollment/1',job_id='job/1',
        lease_epoch=3,session_id='session/1')
    assert api.authenticate_worker('native/worker','job/1',3).status=='OK'
    assert api.authenticate_worker('native/worker','job/1',4).code=='UNAUTHENTICATED'
    assert api.authenticate_worker('native/worker','job/other',3).code=='UNAUTHENTICATED'

def test_permission_readback_and_owner_decision_binding():
    native,_,api=service()
    assert api.verify_external_permission('owner/1','fixture/provider','read','repo/x').allowed
    assert not api.verify_external_permission('owner/1','other/provider','read','repo/x').allowed
    native.sign('native/decision','OWNER_DECISION',principal_id='owner/1',credential_id='credential/1',
        epoch=4,valid_until_us=10_000_000,issued_at_us=900_000,principal_kind='OWNER',
        enrollment_ref='enrollment/1',action='RELEASE_PUBLISH',subject_digest=D,
        project_id='project/1',allowed=True)
    assert api.verify_owner_decision('native/decision','RELEASE_PUBLISH',D,'project/1').allowed
    assert not api.verify_owner_decision('native/decision','RELEASE_PUBLISH',D,'project/2').allowed

def test_permission_cannot_outlive_identity_revocation():
    native,store,api=service()
    assert api.verify_external_permission('owner/1','fixture/provider','read','repo/x').allowed
    store.db.execute("UPDATE v1_verified_identities SET epoch=5 WHERE principal_id='owner/1'")
    store.db.commit()
    assert api.verify_external_permission('owner/1','fixture/provider','read','repo/x').code=='UNAUTHENTICATED'
