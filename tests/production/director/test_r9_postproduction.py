"""Pipeline negative cases: no implied host/QA, fail-fast external effects."""
from nomad_studio.production.director.postproduction import PostProductionPipeline
from nomad_studio.production.director.wire import Result

D='a'*64
C='b'*64
SHA='a'*40

class Director:
    def get_feature_status(self,feature,owner):
        return Result('OK','OK',{'project_id':'p','feature_id':'f','status':'INTEGRATING',
                                  'revision':2,'feature_contract_digest':C,
                                  'bible_pair_digest':D})

class Context:
    def observe(self,feature,stage,status):
        return Result('OK','OK',{'project_id':'p','feature_id':'f','bible_pair_digest':D,
                                 'feature_contract_digest':C,'source_commit':SHA,
                                 'technical_edition_id':'tech', 'trusted_runner':{},
                                 'acceptance_ids':['smoke']},'context:verified')

class Adapter:
    def __init__(self):self.calls=[]
    def resolve_adapter(self,project,tech,sha):
        self.calls.append('resolve')
        return Result('OK','OK',{'project_id':'p','feature_id':'f','bible_pair_digest':D,
                                 'feature_contract_digest':C,'adapter_ref':'adapter'},'adapter:verified')
    def run_import(self,*args):
        self.calls.append('import')
        return Result('REJECTED','TEST_FAILED')
    def run_build(self,*args):self.calls.append('build');raise AssertionError('Build after failed import')
    def run_feature_smoke(self,*args):self.calls.append('smoke');raise AssertionError('Smoke after failed import')

def test_import_failure_fences_build_and_smoke():
    adapter=Adapter()
    p=PostProductionPipeline(director=Director(),artifacts=None,repository=None,
                             adapter=adapter,quality=None,trusted_context=Context())
    res=p.tick('owner','f')
    assert res.status=='BLOCKED_LOCAL' and res.code=='TEST_FAILED'
    assert adapter.calls==['resolve','import']

def test_fake_context_cannot_drive_effects():
    class FakeContext(Context):
        def observe(self,*args):
            result=super().observe(*args)
            return Result('OK','OK',{**result.record,'simulated':True},'context:verified')
    adapter=Adapter()
    p=PostProductionPipeline(director=Director(),artifacts=None,repository=None,
                             adapter=adapter,quality=None,trusted_context=FakeContext())
    assert p.tick('owner','f').code=='EVIDENCE_INVALID'
    assert adapter.calls==[]
