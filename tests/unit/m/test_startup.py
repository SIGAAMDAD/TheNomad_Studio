from __future__ import annotations

import pytest

from studio.app.errors import StudioError
from studio.app.startup import StartupRecovery, ChannelCursor, RelayObservation

SHA='f'*40
DIGEST='a'*64

class State:
    def __init__(self): self.cursor=None;self.held=[];self.effects=[]
    def channels(self):return [ChannelCursor('p1','relay',self.cursor)]
    def outstanding_effects(self,*,project_id):return self.effects
    def persist_verified_cursor(self,*,project_id,channel_id,expected_ref_sha,new_ref_sha):
        if self.cursor != expected_ref_sha:return False
        self.cursor=new_ref_sha
        return True
    def hold_channel_unknown(self,*,project_id,channel_id,reason):self.held.append(channel_id)

class Github:
    def __init__(self):
        self.ancestry=True;self.observed=True;self.reconciled=True
    def scan_since(self,*,project_id,channel_id,verified_ref_sha):
        return ([RelayObservation('p1','relay','relays/message.json',SHA,DIGEST,self.observed)],
                SHA,self.ancestry)
    def reconcile_external_effect(self,*,project_id,effect_id):return self.reconciled

class Projector:
    def __init__(self):self.observations=[]
    def project_verified(self, observation):self.observations.append(observation)


def test_verified_observation_replayed_before_cursor_update():
    state=State();g=Github();p=Projector()
    assert StartupRecovery(state,g,p).recover()=={'observations_projected':1,'channels_held':0,'projects_requiring_reconciliation':0}
    assert state.cursor==SHA
    assert len(p.observations)==1


def test_rewritten_ref_fails_closed():
    state=State();g=Github();g.ancestry=False;p=Projector()
    result=StartupRecovery(state,g,p).recover()
    assert result['projects_requiring_reconciliation']==1
    assert state.held==['relay']
    assert state.cursor is None


def test_unverified_hello_does_not_become_observed_worker():
    state=State();g=Github();g.observed=False;p=Projector()
    result=StartupRecovery(state,g,p).recover()
    assert result['projects_requiring_reconciliation']==1
    assert p.observations==[]
    assert state.held==['relay']


def test_unknown_provider_effect_not_replayed():
    state=State();state.effects=['effect-1'];g=Github();g.reconciled=False;p=Projector()
    result=StartupRecovery(state,g,p).recover()
    assert result['projects_requiring_reconciliation']==1
    assert state.effects==['effect-1']
