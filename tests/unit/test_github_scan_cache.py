"""Repeated pages share one immutable tree snapshot, but recheck branch tip."""
import hashlib

import pytest

from studio.integrations.common import ProjectBinding, RemoteUnknown
from studio.integrations.github import GitHub, GitHubChannel, RemoteFile


def make_github():
    github = GitHub(object(), lambda: 'token',
                    GitHubChannel('project', 'owner', 'repo', 'comms'),
                    ProjectBinding('project', 'account'))
    state = {'tip': 'a' * 40, 'tips': 0, 'trees': [], 'ancestry': []}
    old = 'b' * 40
    body = b'{"project_id":"project"}'
    paths = {github.channel.path('handoffs', 'worker', f'item{i}'): 'd' * 40
             for i in range(4)}

    def tip():
        state['tips'] += 1
        return state['tip']

    def tree(commit):
        state['trees'].append(commit)
        return paths if commit == state['tip'] else {}

    def read(path, commit, blob):
        assert path in paths
        return RemoteFile('project', path, commit, hashlib.sha256(body).hexdigest(), body)

    github.branch_tip = tip
    github._tree = tree
    github._read_at = read
    github._is_descendant = lambda before, after: state['ancestry'].append((before, after))
    return github, old, state


def test_same_snapshot_pages_require_only_one_pair_of_tree_fetches():
    github, old, state = make_github()
    p1 = github.scan_since(old, limit=2)
    assert p1.next_offset == 2
    assert p1.complete_cursor is None
    p2 = github.scan_since(old, limit=2, offset=2, expected_head=p1.head_sha)
    assert p2.complete_cursor == 'a' * 40
    assert len(p1.entries + p2.entries) == 4
    assert state['trees'] == [old, 'a' * 40]
    assert len(state['ancestry']) == 1
    assert state['tips'] == 2


def test_head_change_during_pagination_is_rejected_before_tree_cache():
    github, old, state = make_github()
    first = github.scan_since(old, limit=2)
    state['tip'] = 'c' * 40
    with pytest.raises(RemoteUnknown, match='head changed'):
        github.scan_since(old, limit=2, offset=2, expected_head=first.head_sha)
    assert state['trees'] == [old, 'a' * 40]
