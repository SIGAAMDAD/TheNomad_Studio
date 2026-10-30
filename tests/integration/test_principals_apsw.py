"""J identity provisioning binds cryptographic bearer to physical APSW principal."""
import hashlib
import pytest
apsw = pytest.importorskip("apsw")

from studio.api import ConfiguredOpaqueVerifier, VerifiedPrincipal
from studio.api.identity import PrincipalProvisioner
from studio.api.errors import ApiError
from studio.storage.principals import PrincipalAPSWStore
from studio.storage.workforce import WorkforceStorage


def test_authenticated_principal_is_immutable_and_bootstrap_authorized(tmp_path):
    writer=WorkforceStorage.open(tmp_path/"identity.sqlite",fresh=True)
    try:
        token="high-entropy." + "x"*60
        verifier=ConfiguredOpaqueVerifier({
            hashlib.sha256(token.encode()).hexdigest():
                VerifiedPrincipal("human","oidc","real-subject","verified-proof",True)})
        store=PrincipalAPSWStore(writer)
        provisioner=PrincipalProvisioner(verifier,store)
        assert provisioner.provision_bearer(token).principal_id=="human"
        assert provisioner.provision_bearer(token).principal_id=="human"
        assert writer._connection.execute(
            "SELECT auth_issuer,auth_subject,kind FROM principals WHERE principal_id='human'"
        ).fetchone()==("oidc","real-subject","human")
        with pytest.raises(ApiError,match="bound to other identity"):
            store.ensure_trusted_principal(principal_id="human",auth_issuer="other",
                                           auth_subject="real-subject",kind="human")
        with pytest.raises(ApiError,match="already assigned"):
            store.ensure_trusted_principal(principal_id="another",auth_issuer="oidc",
                                           auth_subject="real-subject",kind="human")
        with pytest.raises(ApiError,match="authentication proof"):
            provisioner.provision_bearer("untrusted."+"z"*60)
        assert writer._connection.execute("SELECT COUNT(*) FROM principals").fetchone()==(1,)
    finally:
        writer.close()
