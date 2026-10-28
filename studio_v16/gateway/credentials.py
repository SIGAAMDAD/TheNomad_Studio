"""Internal-only GitHub App installation credential issuer (no owner PAT fallback).

The gateway process alone owns the PEM and installation token. Never serialize
returned tokens to worker API, receipts, traces or exception messages.
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass


class CredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstallationScope:
    installation_id: int
    repository_ids: tuple[int, ...]
    # App must additionally be installed with least-privilege permissions.


class GitHubAppCredentials:
    def __init__(self, app_id: int, private_key_pem: bytes, scope: InstallationScope,
                 opener=None, clock=time.time):
        if not app_id or not scope.installation_id or not scope.repository_ids or not private_key_pem:
            raise CredentialError("GitHub App configuration missing")
        self._app_id = app_id
        self._key = private_key_pem
        self._scope = scope
        self._opener = opener or urllib.request.urlopen
        self._clock = clock

    def _signed_jwt(self) -> str:
        import base64
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        key = serialization.load_pem_private_key(self._key, password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise CredentialError("RSA GitHub App key required")
        now = int(self._clock())
        def enc(data):
            return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":"),
                   sort_keys=True).encode()).rstrip(b"=").decode()
        signed = enc({"alg": "RS256", "typ": "JWT"}) + "." + enc({
            "iat": now - 30, "exp": now + 540, "iss": str(self._app_id)})
        sig = key.sign(signed.encode(), padding.PKCS1v15(), hashes.SHA256())
        return signed + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

    def installation_token(self, repository_id: int) -> str:
        if repository_id not in self._scope.repository_ids:
            raise CredentialError("Repository not authorized for installation")
        body = json.dumps({"repository_ids": [repository_id],
                           "permissions": {"contents": "write"}}).encode()
        request = urllib.request.Request(
            f"https://api.github.com/app/installations/{self._scope.installation_id}/access_tokens",
            data=body, method="POST", headers={"Authorization": "Bearer " + self._signed_jwt(),
                    "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                    "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with self._opener(request, timeout=10) as response:
                data = json.loads(response.read(16384))
        except Exception as exc:
            # No upstream error bodies; may contain credentials.
            raise CredentialError("GitHub App token issuance failed") from None
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise CredentialError("GitHub App token response invalid")
        return token
