"""Bounded response fault/continuation checks; no external provider attestation."""
import unittest

from studio_v16.adapters.response import run_response


class MemoryPort:
    def __init__(self, jobs=(), controls=()):
        self.jobs = list(jobs)
        self.controls = list(controls)
        self.events = []
        self.records = []
        self.finished = None

    def start(self, response_id, worker_id, department):
        self.events.append('START')
        return 'RUNNING'

    def observe_control(self, response_id):
        self.events.append('OBSERVE')
        return self.controls.pop(0) if self.controls else None

    def next_claim(self, response_id):
        self.events.append('CLAIM')
        return self.jobs.pop(0) if self.jobs else None

    def record_job(self, response_id, attempt_id, acceptance_ids, evidence):
        self.events.append('RECORD')
        self.records.append((attempt_id, acceptance_ids, evidence))

    def finish(self, response_id, *, stop, resume_capsule, failures):
        self.events.append('FINISH')
        self.finished = dict(stop=stop, resume_capsule=resume_capsule,
                             failures=list(failures), records=list(self.records))
        return self.finished


class ResponseFaultsC8(unittest.TestCase):
    def run_port(self, port, executor=None, **kwargs):
        return run_response(port, 'response-c8', 'worker-c8', 'design',
                            executor or (lambda job: {'acceptance_ids': [job['job_id']],
                                                       'evidence': {'source_sha': 'a' * 40}}),
                            lambda: {'checkpoint': 'durable'}, **kwargs)

    def test_two_authorized_jobs_continue_within_one_response(self):
        port = MemoryPort([{'job_id': 'one', 'attempt_id': 'attempt-one'},
                           {'job_id': 'two', 'attempt_id': 'attempt-two'}])
        result = self.run_port(port)
        self.assertEqual([x[0] for x in result['records']], ['attempt-one', 'attempt-two'])
        self.assertEqual(result['failures'], [])
        self.assertFalse(result['stop'])
        self.assertEqual(port.events.count('START'), 1)
        self.assertEqual(port.events.count('CLAIM'), 3)
        self.assertEqual(port.events[-1], 'FINISH')

    def test_stop_before_next_claim_preserves_unstarted_jobs(self):
        port = MemoryPort([{'job_id': 'one', 'attempt_id': 'attempt-one'}],
                          [{'action': 'STOP', 'control_epoch': 1}])
        result = self.run_port(port)
        self.assertTrue(result['stop'])
        self.assertEqual(result['records'], [])
        self.assertEqual(port.events, ['START', 'OBSERVE', 'FINISH'])
        self.assertEqual(len(port.jobs), 1)

    def test_continue_then_stop_cannot_skip_stop(self):
        port = MemoryPort([{'job_id': 'one', 'attempt_id': 'attempt-one'}],
                          [{'action': 'CONTINUE'}, {'action': 'STOP'}])
        result = self.run_port(port)
        self.assertTrue(result['stop'])
        self.assertNotIn('CLAIM', port.events)
        self.assertEqual(port.events.count('OBSERVE'), 2)

    def test_execute_failure_is_not_recorded_as_completed(self):
        port = MemoryPort([{'job_id': 'one', 'attempt_id': 'attempt-one'},
                           {'job_id': 'two', 'attempt_id': 'attempt-two'}])
        def fail(job):
            raise RuntimeError('lost provider after claim')
        result = self.run_port(port, fail)
        self.assertEqual(result['records'], [])
        self.assertEqual(result['failures'], [{'attempt_id': 'attempt-one', 'error_type': 'RuntimeError'}])
        self.assertFalse(result['stop'])
        self.assertEqual(result['resume_capsule'], {'checkpoint': 'durable'})
        self.assertEqual(len(port.jobs), 1)

    def test_control_pause_yields_without_second_claim(self):
        port = MemoryPort([{'job_id': 'one', 'attempt_id': 'attempt-one'}],
                          [{'action': 'PAUSE'}])
        result = self.run_port(port)
        self.assertFalse(result['stop'])
        self.assertEqual(result['resume_capsule']['checkpoint'], 'durable')
        self.assertNotIn('CLAIM', port.events)

    def test_job_budget_forces_checkpoint_without_false_stop(self):
        port = MemoryPort([{'job_id': 'one', 'attempt_id': 'attempt-one'},
                           {'job_id': 'two', 'attempt_id': 'attempt-two'}])
        result = self.run_port(port, max_jobs=1)
        self.assertEqual([x[0] for x in result['records']], ['attempt-one'])
        self.assertFalse(result['stop'])
        self.assertEqual(result['resume_capsule'], {'checkpoint': 'durable'})
        self.assertEqual(len(port.jobs), 1)

    def test_invalid_time_budget_rejected_before_start(self):
        for value in (None, '20', True, 0, 3601):
            with self.subTest(value=value):
                port = MemoryPort()
                with self.assertRaises(ValueError):
                    self.run_port(port, max_seconds=value)
                self.assertEqual(port.events, [])


if __name__ == '__main__':
    unittest.main()
