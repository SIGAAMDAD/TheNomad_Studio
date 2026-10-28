"""Isolated Git remote CAS writer for WG-D's GitSource contract.

Production deployment must supply a server-controlled remote mapping and isolated
GitHub App credential helper; this code never accepts a token or URL from a worker.
Publishing is disabled by default and additionally requires a trusted admission
callback, an exact source base, and an atomic remote compare-and-swap push.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from studio_v16.gateway.interfaces import GitEntry, SourceSnapshot, InspectedPatch

SHA40=re.compile(r'^[0-9a-f]{40}$')
REPO=re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
BRANCH=re.compile(r'^studio/workorders/[A-Za-z][A-Za-z0-9_.-]{2,79}/[A-Za-z0-9_.-]{1,80}$')


class ProviderBlocked(RuntimeError):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git(cwd, *args, input=None, env=None):
    try:
        proc=subprocess.run(('git',*args),cwd=cwd,input=input,capture_output=True,
                            timeout=45,env=env,check=False)
    except subprocess.TimeoutExpired as exc:
        raise ProviderBlocked('PROVIDER_TIMEOUT') from exc
    if proc.returncode:
        raise ProviderBlocked('GIT_COMMAND_FAILED')
    return proc.stdout


def _entries(cwd):
    output=git(cwd,'ls-tree','-rz','HEAD')
    result=[]
    for raw in output.split(b'\0'):
        if not raw: continue
        mode_kind, path = raw.split(b'\t',1)
        mode=mode_kind.split(b' ',1)[0].decode('ascii')
        result.append(GitEntry(path.decode('utf-8','strict'),mode))
    return tuple(result)


def _tree_digest(cwd):
    return sha256(git(cwd,'ls-tree','-rz','HEAD'))


class IsolatedGitProvider:
    def __init__(self, remotes: dict[str,str], *, authorized=None, enable_external_effects=False):
        if not remotes or any(not REPO.fullmatch(k) or not isinstance(v,str) or not v for k,v in remotes.items()):
            raise ValueError('trusted repository map required')
        self._remotes=dict(remotes)
        self._authorized=authorized
        self._enabled=enable_external_effects

    def _remote(self, repo_id):
        if repo_id not in self._remotes:
            raise ProviderBlocked('INVALID_SCOPE')
        return self._remotes[repo_id]

    @contextmanager
    def _checkout(self, repo_id, sha=None):
        remote=self._remote(repo_id)
        with tempfile.TemporaryDirectory(prefix='nomad-git-provider-') as tmp:
            # Deliberately no command shell or credential from the intent.
            git(tmp,'clone','-q','--no-checkout','--',remote,'repo')
            cwd=Path(tmp)/'repo'
            if sha:
                if not SHA40.fullmatch(sha): raise ProviderBlocked('SOURCE_SHA_MISMATCH')
                git(cwd,'cat-file','-e',sha+'^{commit}')
                git(cwd,'checkout','-q','--detach',sha)
            yield cwd

    def snapshot(self, repo_id, branch):
        if not BRANCH.fullmatch(branch): raise ProviderBlocked('INVALID_SCOPE')
        with self._checkout(repo_id) as cwd:
            head=git(cwd,'ls-remote','--heads','origin','refs/heads/'+branch).decode().strip().split()
            if len(head)!=2 or not SHA40.fullmatch(head[0]):
                raise ProviderBlocked('REBASE_REQUIRED')
            git(cwd,'checkout','-q','--detach',head[0])
            return SourceSnapshot(repo_id,branch,head[0],_entries(cwd))

    def inspect_patch(self, repo_id, base_sha, patch):
        if not isinstance(patch, bytes) or not 0<len(patch)<=32*1024*1024:
            raise ProviderBlocked('INVALID_PATCH')
        with self._checkout(repo_id,base_sha) as cwd:
            # Git checks filenames/binary patch syntax; no executable hooks run.
            git(cwd,'apply','--cached','--check','--binary','-',input=patch)
            git(cwd,'apply','--cached','--binary','-',input=patch)
            paths=git(cwd,'diff','--cached','--name-only','-z','--no-renames').split(b'\0')
            changed=tuple(p.decode('utf-8','strict') for p in paths if p)
            if not changed: raise ProviderBlocked('EMPTY_PATCH')
            tree=git(cwd,'write-tree').decode().strip()
            git(cwd,'checkout','-q','--detach',base_sha)
            git(cwd,'read-tree',tree)
            # Read the proposed index rather than untouched working HEAD.
            modes=[]
            for raw in git(cwd,'ls-files','--stage','-z').split(b'\0'):
                if raw:
                    metadata,path=raw.split(b'\t',1)
                    modes.append(GitEntry(path.decode('utf-8','strict'),metadata.split(b' ',1)[0].decode('ascii')))
            proposed_tree=sha256(git(cwd,'ls-tree','-rz',tree))
            return InspectedPatch(sha256(patch), proposed_tree, changed, tuple(modes))

    def _observed(self, intent):
        target=intent['target']; repo_id=target['repo_id']; branch=target['work_branch']
        snap=self.snapshot(repo_id,branch)
        with self._checkout(repo_id,snap.head_sha) as cwd:
            parent=git(cwd,'rev-parse','HEAD^').decode().strip() if snap.head_sha!=target['source_base_sha'] else None
            msg=git(cwd,'show','-s','--format=%B','HEAD').decode('utf-8','replace')
            if (parent!=target['source_base_sha'] or
                'Nomad-Publication: '+intent['publication_id']+'\n' not in msg or
                'Nomad-Idempotency: '+intent['idempotency_key']+'\n' not in msg or
                'Nomad-Artifact: '+intent['artifact_sha256']+'\n' not in msg or
                _tree_digest(cwd)!=target['proposed_tree_sha256']):
                return None
            return dict(repo_id=repo_id,work_branch=branch,commit_sha=snap.head_sha,
                        tree_sha256=target['proposed_tree_sha256'],artifact_sha256=intent['artifact_sha256'])

    def readback(self, intent):
        try:
            return self._observed(intent)
        except (ProviderBlocked, KeyError, ValueError, OSError):
            return None

    def recovery_readback(self, intent):
        """Read one pinned remote head; no provider writes or retry authorization."""
        target = intent['target']
        repo, branch, base = target['repo_id'], target['work_branch'], target['source_base_sha']
        if not BRANCH.fullmatch(branch):
            raise ProviderBlocked('INVALID_SCOPE')
        with self._checkout(repo) as cwd:
            parts = git(cwd, 'ls-remote', '--heads', 'origin', 'refs/heads/' + branch).decode().strip().split()
            if len(parts) != 2 or not SHA40.fullmatch(parts[0]):
                return {'state': 'UNKNOWN'}
            head = parts[0]
            if head == base:
                return {'state': 'ABSENT', 'evidence': dict(repo_id=repo, work_branch=branch, head_sha=head)}
            git(cwd, 'checkout', '-q', '--detach', head)
            parent = git(cwd, 'rev-parse', 'HEAD^').decode().strip()
            message = git(cwd, 'show', '-s', '--format=%B', 'HEAD').decode('utf-8', 'replace')
            tree = _tree_digest(cwd)
            expected_trailers = (f'Nomad-Publication: {intent["publication_id"]}\n',
                                 f'Nomad-Idempotency: {intent["idempotency_key"]}\n',
                                 f'Nomad-Artifact: {intent["artifact_sha256"]}\n')
            if parent != base or tree != target['proposed_tree_sha256'] or any(t not in message for t in expected_trailers):
                return {'state': 'CONFLICT'}
            return {'state': 'PRESENT', 'observed': dict(repo_id=repo, work_branch=branch, commit_sha=head,
                    tree_sha256=tree, artifact_sha256=intent['artifact_sha256'])}

    def publish(self, intent, patch):
        """Call from trusted outbox AFTER gateway admission; no worker direct access.

        Ambiguous network result never retries blindly: subsequent call first reads
        the exact previously published commit. A non-fast-forward branch move
        fails with REBASE_REQUIRED instead of overwriting competing work.
        """
        if not self._enabled or self._authorized is None:
            raise ProviderBlocked('CUTOVER_NOT_APPROVED')
        target=intent['target']
        repo_id,branch,base=target['repo_id'],target['work_branch'],target['source_base_sha']
        if not BRANCH.fullmatch(branch): raise ProviderBlocked('INVALID_SCOPE')
        if sha256(patch)!=intent['artifact_sha256'] or target['patch_sha256']!=sha256(patch):
            raise ProviderBlocked('SOURCE_SHA_MISMATCH')
        observed=self.readback(intent)
        if observed is not None: return observed
        snap=self.snapshot(repo_id,branch)
        if snap.head_sha!=base: raise ProviderBlocked('REBASE_REQUIRED')
        inspected=self.inspect_patch(repo_id,base,patch)
        if (inspected.proposed_tree_sha256!=target['proposed_tree_sha256'] or
            set(inspected.changed_paths)!=set(target['changed_paths'])):
            raise ProviderBlocked('SOURCE_SHA_MISMATCH')
        with self._checkout(repo_id,base) as cwd:
            git(cwd,'apply','--cached','--binary','-',input=patch)
            tree=git(cwd,'write-tree').decode().strip()
            msg=('Nomad gateway authorized publication\n\n'
                 f'Nomad-Publication: {intent["publication_id"]}\n'
                 f'Nomad-Idempotency: {intent["idempotency_key"]}\n'
                 f'Nomad-Artifact: {intent["artifact_sha256"]}\n')
            env={**os.environ,'GIT_AUTHOR_NAME':'nomad-studio-gateway',
                'GIT_AUTHOR_EMAIL':'gateway@users.noreply.github.com',
                'GIT_COMMITTER_NAME':'nomad-studio-gateway',
                'GIT_COMMITTER_EMAIL':'gateway@users.noreply.github.com'}
            candidate=git(cwd,'commit-tree',tree,'-p',base,input=msg.encode(),env=env).decode().strip()
            # Admission commits dispatch evidence immediately before remote I/O,
            # after potentially slow clone/inspection and before the conditional push.
            if self._authorized(intent) is not True:
                raise ProviderBlocked('CUTOVER_NOT_APPROVED')
            # Lease verifies old ref at server atomically; cannot force overwrite drift.
            try:
                git(cwd,'push','--porcelain',f'--force-with-lease=refs/heads/{branch}:{base}',
                    'origin',f'{candidate}:refs/heads/{branch}')
            except ProviderBlocked:
                observed=self.readback(intent)
                if observed is not None: return observed
                raise ProviderBlocked('RECONCILE_REQUIRED')
        observed=self.readback(intent)
        if observed is None: raise ProviderBlocked('RECONCILE_REQUIRED')
        return observed
