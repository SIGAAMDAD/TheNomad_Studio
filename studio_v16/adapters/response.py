"""Manually invoked bounded multi-job response runner over an authenticated port.

The port is a service client, never a database connection. execute_job performs
real work/checkpoints and returns exact evidence; this runner never invents it.
"""
import time
from typing import Protocol


class ResponsePort(Protocol):
    def start(self, response_id, worker_id, department): ...
    def observe_control(self, response_id): ...
    def next_claim(self, response_id): ...
    def record_job(self, response_id, attempt_id, acceptance_ids, evidence): ...
    def finish(self, response_id, *, stop, resume_capsule, failures): ...


def run_response(port: ResponsePort, response_id, worker_id, department, execute_job,
                 capture_resume, *, max_jobs=20, max_seconds=600):
    if type(max_jobs) is not int or not 1 <= max_jobs <= 100 or not 1 <= max_seconds <= 3600:
        raise ValueError('bounded response budget required')
    if port.start(response_id, worker_id, department) != 'RUNNING':
        raise RuntimeError('a resumed invocation requires a fresh response ID')
    start, attempted, failures = time.monotonic(), 0, []
    while attempted < max_jobs and time.monotonic() - start < max_seconds:
        # Drain pending CONTINUE observations before taking another claim. STOP
        # cannot be skipped if several controls arrived between checkpoints.
        control = port.observe_control(response_id)
        while control and control['action'] == 'CONTINUE':
            control = port.observe_control(response_id)
        if control and control['action'] in ('STOP', 'DRAIN', 'PAUSE'):
            return port.finish(response_id, stop=control['action'] == 'STOP',
                               resume_capsule=capture_resume(), failures=failures)
        try:
            claim = port.next_claim(response_id)
        except Exception as exc:
            failures.append(dict(stage='CLAIM', error_type=type(exc).__name__))
            break
        if claim is None:
            break
        attempted += 1
        try:
            result = execute_job(claim)
            port.record_job(response_id, claim['attempt_id'], result['acceptance_ids'], result['evidence'])
        except Exception as exc:
            # The service already persisted the attempt before handing it out.
            # A missing outcome remains UNKNOWN in the final report card.
            failures.append(dict(attempt_id=claim['attempt_id'], error_type=type(exc).__name__))
            break
    control = port.observe_control(response_id)
    while control and control['action'] == 'CONTINUE':
        control = port.observe_control(response_id)
    return port.finish(response_id, stop=bool(control and control['action'] == 'STOP'),
                       resume_capsule=capture_resume(), failures=failures)
