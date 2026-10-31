"""Static and simulated-contract tests for V4 skill release."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from validate_v4 import validate_event, validate_roster, load_json
from audit_ledger import audit, render_md


class StudioV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry=load_json(ROOT/'references/departments.v4.json')
        cls.example=load_json(ROOT/'examples/questline-ledger-event.json')

    def test_174_unique_and_12_departments(self):
        self.assertEqual(validate_roster(),174)
        self.assertEqual(len(self.registry['departments']),12)

    def test_director_roles_operationally_rehomed(self):
        for role,dept in {
            'rpg-design-director':'design',
            'rpg-narrative-director':'narrative',
            'rpg-art-director':'visual_art',
            'rpg-audio-director':'audio',
            'rpg-animation-director':'animation',
            'rpg-technical-director':'engineering',
        }.items():
            self.assertEqual(self.registry['role_to_department'][role],dept)

    def test_all_heads_own_their_departments(self):
        for dept,meta in self.registry['departments'].items():
            self.assertEqual(self.registry['role_to_department'][meta['head_role']],dept)

    def test_reject_legacy_scheduled_slots(self):
        for field, value in (("mode", "scheduled"), ("mode", "event_work"), ("worker_slot", "S01")):
            candidate = copy.deepcopy(self.example)
            candidate[field] = value
            with self.assertRaises(AssertionError):
                validate_event(candidate)

    def test_browser_shift_requires_worker_and_shift(self):
        candidate = copy.deepcopy(self.example)
        candidate["mode"] = "browser_shift"
        with self.assertRaises(AssertionError):
            validate_event(candidate)
        candidate["worker_id"] = "registered-worker"
        candidate["shift_id"] = "verified-shift"
        self.assertTrue(validate_event(candidate))

    def test_example_ledger(self):
        self.assertTrue(validate_event(self.example))

    def test_ledger_rejects_wrong_department(self):
        e=copy.deepcopy(self.example);e['department_id']='engineering'
        with self.assertRaises(AssertionError): validate_event(e)

    def test_ledger_rejects_naive_time(self):
        e=copy.deepcopy(self.example);e['recorded_at_utc']='2026-10-27T12:00:00'
        with self.assertRaises(AssertionError): validate_event(e)

    def test_ledger_rejects_unproven_pass(self):
        e=copy.deepcopy(self.example);e['checks']=[{'name':'build','status':'PASS'}]
        with self.assertRaises(AssertionError): validate_event(e)

    def test_ledger_rejects_issue_event_without_issue(self):
        e=copy.deepcopy(self.example);e['event_type']='ISSUE_REGISTERED';e['issue']=None
        with self.assertRaises(AssertionError): validate_event(e)

    def test_ledger_rejects_path_mismatch(self):
        with self.assertRaises(AssertionError): validate_event(self.example,'other-event.json')

    def test_issue_backfill_and_waiting_matrix(self):
        issues=[{'number':42,'title':'Questline'},{'number':43,'title':'Narrative branch'}]
        out=audit(issues,[self.example])
        self.assertEqual(out['issues_registered'],1)
        self.assertEqual(out['missing_issue_registrations'][0]['number'],43)
        self.assertEqual(out['waiting_on'][0]['to'],'narrative')
        self.assertEqual(out['open_todos'][0]['state'],'BLOCKED')
        self.assertIn('#43',render_md(out))

    def test_latest_todo_event_wins(self):
        later=copy.deepcopy(self.example)
        later.update(event_id='CHAT-20261027T210000Z-QUEST02',recorded_at_utc='2026-10-27T21:00:00Z',todo=[{'id':'Q-002','description':'Integrate dialogue','state':'DONE'}],dependencies=[])
        out=audit([{'number':42,'title':'Questline'}],[later,self.example])
        self.assertFalse(out['open_todos'])

    def test_role_procedures_are_present(self):
        r=load_json(ROOT/'references/roles_manifest.json')['roles']
        for role in r:
            self.assertIn(role['id'],self.registry['role_to_department'])


if __name__=='__main__': unittest.main()
