"""Deterministic abuse matrix: pure controller checks against concrete R9 services.

No results are represented as real host/owner/provider proof.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import random

import pytest

from nomad_studio.contracts.runtime_ports_v1 import Result, Decision
from nomad_studio.production.director.api import _TRANSITIONS, _GATES
from nomad_studio.production.director.commands import CommandService, OPERATIONS
from nomad_studio.production.director.wire import digest


REQUEST = 'b' * 64
PROOF = 'proof-native:owner1'
COMMAND = {
    'command_id':'cmd1','operation':'production.submit_brief','owner_principal_id':'owner1',
    'request_digest':REQUEST,'auth_proof_ref':PROOF,'idempotency_key':'idem1',
    'issued_at_us':1234,'project_id':'project1',
}
VERIFIED = Result('OK','OK',{
    'principal_id':'owner1','principal_kind':'OWNER','action_digest':REQUEST,
    'simulated':False,
},receipt_ref=PROOF)


def test_canonical_result_and_decision_are_immutable():
    with pytest.raises((FrozenInstanceError,AttributeError)):
        VERIFIED.code = 'OK_FAKE'
    with pytest.raises((FrozenInstanceError,AttributeError)):
        Decision(True,'ALLOW','OK','x').allowed = False


@pytest.mark.parametrize('operation', sorted(OPERATIONS))
def test_all_approved_commands_require_proof_binding(operation):
    command = {**COMMAND,'operation':operation}
    if operation in ('production.pause','production.resume','production.cancel',
                     'production.status','production.inspect','production.request_changes'):
        command['feature_id'] = 'feature1'
    assert CommandService._valid(command)
    assert CommandService._authenticated(command,VERIFIED)


@pytest.mark.parametrize('field,bad', [
    ('command_id',''),('command_id','../path'),('owner_principal_id',None),
    ('owner_principal_id','bad owner'),('auth_proof_ref',''),
    ('idempotency_key','../../oops'),('request_digest','a'*40),
    ('request_digest','z'*64),('issued_at_us',0),('issued_at_us',1.5),
    ('operation','arbitrary.action'),('project_id','a b'),
])
def test_malformed_owner_commands_rejected(field,bad):
    assert not CommandService._valid({**COMMAND,field:bad})


@pytest.mark.parametrize('change', [
    {'principal_id':'impostor'}, {'principal_kind':'WORKER'},
    {'action_digest':'c'*64}, {'simulated':True}, {'simulated':'true'},
])
def test_improper_owner_authentication_rejected(change):
    identity = Result('OK','OK',{**VERIFIED.record,**change},receipt_ref=PROOF)
    assert not CommandService._authenticated(COMMAND,identity)


@pytest.mark.parametrize('receipt', ['bad-proof','',None])
def test_owner_proof_reference_cannot_be_rebound(receipt):
    identity = Result('OK','OK',dict(VERIFIED.record),receipt_ref=receipt)
    assert not CommandService._authenticated(COMMAND,identity)


def test_command_new_field_is_rejected_and_cannot_smuggle_scope():
    assert not CommandService._valid({**COMMAND,'owner_override':True})
    assert not CommandService._valid({**COMMAND,'feature_id':'../other'})


def test_lifecycle_allows_no_direct_terminal_state_escape():
    assert not _TRANSITIONS['MERGED']
    assert not _TRANSITIONS['CANCELLED']
    assert 'QA_PASSED' not in _TRANSITIONS['RECEIVED']
    assert 'MERGED' not in _TRANSITIONS['IN_PRODUCTION']
    assert 'QA_READY' not in _TRANSITIONS['PLANNED']
    assert 'MERGED' not in _TRANSITIONS['QA_PASSED']
    assert 'MERGED' in _TRANSITIONS['MERGE_REQUESTED']
    assert 'QA_READY' in _TRANSITIONS['VERIFYING']
    assert _GATES['QA_READY'] == 'quality'
    assert _GATES['MERGED'] == 'repository'


def test_state_transition_graph_contains_realistic_success_path():
    states = ['RECEIVED','CONTEXT_PINNED','CONTRACT_READY','PLANNED',
              'IN_PRODUCTION','INTEGRATING','VERIFYING','QA_READY',
              'QA_IN_PROGRESS','QA_PASSED','MERGE_REQUESTED','MERGED']
    assert all(b in _TRANSITIONS[a] for a,b in zip(states, states[1:]))
    assert all(a in _TRANSITIONS for a in states)


@pytest.mark.parametrize('seed',range(70))
def test_random_lifecycle_walk_never_escapes_declared_states(seed):
    rng = random.Random(seed)
    state = 'RECEIVED'
    for _ in range(100):
        valid = sorted(_TRANSITIONS[state])
        if not valid:
            assert state in ('CANCELLED','MERGED')
            break
        following = rng.choice(valid)
        assert following in _TRANSITIONS[state]
        state = following


def test_authenticated_request_binding_is_exact_under_mutation():
    for i in range(100):
        forged = deepcopy(COMMAND)
        forged['request_digest'] = digest({'nonce':i})
        assert not CommandService._authenticated(forged,VERIFIED)
