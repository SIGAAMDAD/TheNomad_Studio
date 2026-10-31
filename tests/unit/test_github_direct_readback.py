"""Immutable contents readback avoids recursive-tree calls for one relay file."""
import base64
import hashlib
import json

import pytest

from studio.integrations.common import ProjectBinding, RemoteUnknown
from studio.integrations.github import GitHub, GitHubChannel


class ContentsHTTP:
    def __init__(self, body=b"verified message", *, missing=False, bad_blob=False):
        self.body = body
        self.missing = missing
        self.bad_blob = bad_blob
        self.calls = []

    def request(self, method, url, headers, body=None):
        assert method == "GET"
        assert body is None
        self.calls.append(url)
        if "/git/ref/heads/" in url:
            return 200, {}, json.dumps({"object": {"sha": "a" * 40}}).encode()
        if "/contents/" in url:
            assert "?ref=" + "a" * 40 in url
            if self.missing:
                return 404, {}, b'{"message":"Not Found"}'
            blob = hashlib.sha1(b"blob " + str(len(self.body)).encode() + b"\0" + self.body).hexdigest()
            if self.bad_blob:
                blob = "0" * 40
            return 200, {}, json.dumps({
                "type": "file", "encoding": "base64",
                "sha": blob, "content": base64.b64encode(self.body).decode()}).encode()
        raise AssertionError("single-file readback must not enumerate Git trees")


def bound_github(http):
    channel = GitHubChannel("project", "owner", "repo", "studio/comms")
    github = GitHub(http, lambda: "trusted-token", channel,
                    ProjectBinding("project", "account"))
    return github, channel.path("mail", "worker", "command")


def test_exact_readback_uses_two_gets_without_recursive_tree():
    http = ContentsHTTP()
    github, path = bound_github(http)
    observed = github.observe_exact(path)
    assert observed.body == http.body
    assert observed.body_sha256 == hashlib.sha256(http.body).hexdigest()
    assert observed.commit_sha == "a" * 40
    assert len(http.calls) == 2
    assert all("/git/trees/" not in url for url in http.calls)


def test_pinned_commit_avoids_ref_lookup_and_missing_file_is_unknown_to_caller():
    http = ContentsHTTP(missing=True)
    github, path = bound_github(http)
    assert github.observe_exact(path, pinned_commit="a" * 40) is None
    assert len(http.calls) == 1


def test_tampered_git_blob_is_not_verified():
    http = ContentsHTTP(bad_blob=True)
    github, path = bound_github(http)
    with pytest.raises(RemoteUnknown):
        github.observe_exact(path)
