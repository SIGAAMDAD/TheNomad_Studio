"""Drive v3 project-folder-scoped immutable byte upload and ambiguity reconciliation."""
from __future__ import annotations

import json
import re
from typing import Callable
from urllib.parse import quote

from .common import IntegrationError, ProjectBinding, ProviderFailure, RemoteUnknown, digest, identifier, sha256
from .http import HttpPort, JsonProvider


class Drive:
    def __init__(self, http: HttpPort, token_supplier: Callable[[], str], binding: ProjectBinding, project_folder_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", project_folder_id):
            raise IntegrationError("invalid", "project Drive folder ID required")
        self.binding = binding
        self.folder_id = project_folder_id
        self.api = JsonProvider(http, token_supplier, "https://www.googleapis.com")

    def read_bytes(self, *, project_id: str, file_id: str) -> bytes:
        self.binding.require(project_id)
        if not file_id or "/" in file_id or ".." in file_id:
            raise IntegrationError("invalid", "invalid Drive file ID")
        result = self.api.request("GET", "/drive/v3/files/" + quote(file_id, safe="") + "?fields=id,appProperties,parents,trashed")
        if (not isinstance(result, dict) or result.get("trashed") or
            result.get("appProperties", {}).get("nomad_project_id") != project_id or
            self.folder_id not in result.get("parents", [])):
            raise IntegrationError("forbidden", "Drive file is outside configured project folder")
        token = self.api.token_supplier()
        url = self.api.base_url + "/drive/v3/files/" + quote(file_id, safe="") + "?alt=media"
        status, _, body = self.api.http.request("GET", url, {"Authorization": "Bearer " + token, "Accept": "application/octet-stream"})
        if status != 200:
            raise ProviderFailure("Drive readback failed", status)
        return body

    def verify_bytes(self, *, project_id: str, file_id: str, expected_sha256: str) -> bytes:
        data = self.read_bytes(project_id=project_id, file_id=file_id)
        if sha256(data) != digest(expected_sha256):
            raise IntegrationError("conflict", "Drive published bytes do not match pinned digest")
        return data

    def find_uploaded(self, *, project_id: str, intent_id: str, expected_sha256: str) -> str | None:
        self.binding.require(project_id)
        identifier(intent_id)
        expected_sha256 = digest(expected_sha256)
        # Exact, deterministic intent identity ensures retries reconcile rather than duplicate.
        q = (f"'{self.folder_id}' in parents and trashed = false and "
             f"appProperties has {{ key='nomad_project_id' and value='{project_id}' }} and "
             f"appProperties has {{ key='nomad_intent_id' and value='{intent_id}' }}")
        result = self.api.request("GET", "/drive/v3/files?q=" + quote(q, safe="") + "&fields=nextPageToken,files(id,appProperties,parents,trashed)&pageSize=100")
        if not isinstance(result, dict) or not isinstance(result.get("files"), list) or result.get("nextPageToken"):
            raise RemoteUnknown("Drive intent listing incomplete")
        matches = result["files"]
        if len(matches) > 1:
            raise RemoteUnknown("multiple Drive files with identical immutable intent")
        if not matches:
            return None
        item = matches[0]
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item.get("appProperties", {}).get("nomad_sha256") != expected_sha256:
            raise IntegrationError("conflict", "Drive intent exists with different digest")
        self.verify_bytes(project_id=project_id, file_id=item["id"], expected_sha256=expected_sha256)
        return item["id"]

    def publish_new(self, *, project_id: str, intent_id: str, name: str, body: bytes, expected_sha256: str, effect_intent_recorded: bool) -> str:
        self.binding.require(project_id)
        identifier(intent_id)
        if not effect_intent_recorded:
            raise IntegrationError("forbidden", "durable Drive intent required")
        if sha256(body) != digest(expected_sha256):
            raise IntegrationError("invalid", "Drive body/digest mismatch")
        if not name or "/" in name or len(name) > 200:
            raise IntegrationError("invalid", "invalid Drive destination")
        existing = self.find_uploaded(project_id=project_id, intent_id=intent_id, expected_sha256=expected_sha256)
        if existing is not None:
            return existing
        boundary = "nomad_studio_bytes_boundary"
        metadata = {"name": name, "parents": [self.folder_id], "appProperties": {"nomad_project_id": project_id,
                    "nomad_intent_id": intent_id, "nomad_sha256": expected_sha256}}
        multi = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() +
                 json.dumps(metadata, separators=(",", ":")).encode() +
                 f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode() +
                 body + f"\r\n--{boundary}--\r\n".encode())
        headers = {"Authorization": "Bearer " + self.api.token_supplier(), "Content-Type": f"multipart/related; boundary={boundary}"}
        try:
            code, _, response = self.api.http.request("POST", self.api.base_url + "/upload/drive/v3/files?uploadType=multipart&fields=id", headers, multi)
            if code not in (200, 201):
                raise ProviderFailure("Drive upload failed", code)
            file_id = json.loads(response)["id"]
        except (RemoteUnknown, ProviderFailure, ValueError, KeyError, TypeError) as exc:
            observed = self.find_uploaded(project_id=project_id, intent_id=intent_id, expected_sha256=expected_sha256)
            if observed is not None:
                return observed
            raise RemoteUnknown("Drive upload state unknown; reconcile same intent before retry") from exc
        self.verify_bytes(project_id=project_id, file_id=file_id, expected_sha256=expected_sha256)
        return file_id
