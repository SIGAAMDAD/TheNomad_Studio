import pathlib
import sys
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts'))
import work_event_router
import studio_work_orders

class RetirementTests(unittest.TestCase):
    def test_no_event_route_workflow_pr_trigger(self):
        text=(ROOT/'.github/workflows/studio-event-route.yml').read_text()
        self.assertIn('workflow_dispatch:',text)
        self.assertNotIn('pull_request:',text)
        self.assertNotIn('work_event_router.py route',text)
    def test_legacy_router_no_candidate_success_branch(self):
        # No valid combination of historical config, label and model flags
        # can return a native Work activation candidate.
        for path in ('scripts/work_event_router.py','scripts/studio_work_orders.py'):
            text=(ROOT/path).read_text()
            self.assertNotIn('"CANDIDATE_FOR_NATIVE_WORK_SUBSCRIPTION"',text)
            self.assertIn('MANUAL_CHAT_ONLY',text)
    def test_even_all_historical_flags_cannot_launch(self):
        import copy
        from tests.test_work_event_router import WorkerRouterTests
        from tests.test_studio_work_orders import WorkOrderRoutingTests
        fixture=WorkerRouterTests()
        policy=work_event_router.read_json(ROOT/'studio/workforce/hierarchy.v2.json')
        policy['controls']['auto_spend_enabled']=True
        source_job=fixture.job()['jobs'][0]
        config={'auto_ai_enabled':True,'auto_dispatch_enabled':True,
                'source_writes_enabled':True,
                'repos':[{'repo':source_job['repository'],'branch':source_job['branch']}]}
        x=work_event_router.route_event(fixture.event(),policy,fixture.job(),config)
        self.assertEqual('BLOCKED',x['status'])
        self.assertEqual('MANUAL_CHAT_ONLY',x['reason'])
        fo=WorkOrderRoutingTests()
        target=fo.job()
        config['repos']=[{'repo':target['repository'],'branch':target['branch']}]
        y=studio_work_orders.route_work_order(fo.event(target),policy,{'jobs':[target]},config)
        self.assertEqual('BLOCKED',y['status'])
        self.assertEqual('MANUAL_CHAT_ONLY',y['reason'])

    def test_no_pr_to_ai_write_credentials(self):
        text=(ROOT/'.github/workflows/studio-event-route.yml').read_text()
        self.assertIn('contents: read',text)
        self.assertNotIn('contents: write',text)
    def test_privileged_jobs_pin_reviewed_controller(self):
        for name in ('studio-reconcile.yml','studio-comms-broker.yml','studio-ci-evidence-relay.yml'):
            self.assertIn('ref: main',(ROOT/'.github/workflows'/name).read_text())

if __name__=='__main__':unittest.main()
