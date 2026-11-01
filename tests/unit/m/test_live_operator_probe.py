"""The live operator probe requires authentic HTTPS and fail-closed auth."""
import io
import json
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from scripts.verify_live_operator import probe_operator


class Response:
    status = 200
    def __init__(self, body):
        self.body = io.BytesIO(json.dumps(body).encode())
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.body.close()
    def read(self, size=-1):
        return self.body.read(size)


def test_live_probe_checks_401_then_authenticated_project_response():
    seen = []
    def opener(request: Request, *, timeout: int):
        seen.append(dict(request.header_items()))
        if len(seen) == 1:
            raise HTTPError(request.full_url, 401, 'Unauthorized', {}, None)
        return Response({'items':[{'project_id':'project-1'}]})
    assert probe_operator('https://localhost:8443', 't'*64, opener=opener) == 1
    assert len(seen) == 2
    assert 'Authorization' not in seen[0]
    assert seen[1]['Authorization'].startswith('Bearer ')


@pytest.mark.parametrize('address', ['http://localhost:8443', 'https://user:password@host',
                                     'https://host/v1', 'https://host?x=1', 'file:///tmp/api'])
def test_live_probe_rejects_non_https_origin(address):
    with pytest.raises(ValueError, match='HTTPS'):
        probe_operator(address, 't'*64, opener=lambda *_a, **_kw: None)


def test_live_probe_rejects_unauthenticated_success():
    with pytest.raises(ValueError, match='unauthenticated'):
        probe_operator('https://localhost', 't'*64, opener=lambda *_a, **_kw:Response({}))


def test_live_probe_rejects_invalid_response_schema():
    def opener(request, *, timeout):
        if 'Authorization' not in dict(request.header_items()):
            raise HTTPError(request.full_url, 401, 'Unauthorized', {}, None)
        return Response({'items':'wrong'})
    with pytest.raises(ValueError, match='invalid projects'):
        probe_operator('https://localhost', 't'*64, opener=opener)
