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


# A misbehaving or replaying control transport must never monopolize one response.
# Hitting this limit yields without acquiring another job; the backlog remains
# visible to the service and the next authorized response can resume safely.
MAX_CONTROL_OBSERVATIONS = 256


def _observe_bounded(port, response_id, failures, deadline):
    for _ in range(MAX_CONTROL_OBSERVATIONS):
        try:
            control = port.observe_control(response_id)
        except Exception as exc:
            failures.append(dict(stage='CONTROL', error_type=type(exc).__name__))
            return None, False
        if control is None:
            return None, True
        if not isinstance(control, dict) or control.get('action') not in ('STOP', 'DRAIN', 'PAUSE', 'CONTINUE'):
            failures.append(dict(stage='CONTROL', error_type='InvalidControl'))
            return None, False
        if control['action'] != 'CONTINUE':
            return control, True
        if time.monotonic() >= deadline:
            return None, False
    failures.append(dict(stage='CONTROL', error_type='ControlObservationLimit'))
    return None, False


def run_response(port: ResponsePort, response_id, worker_id, department, execute_job,
                 capture_resume, *, max_jobs=20, max_seconds=600):
    if (type(max_jobs) is not int or not 1 <= max_jobs <= 100
            or type(max_seconds) not in (int, float) or not 1 <= max_seconds <= 3600):
        raise ValueError('bounded response budget required')
    if port.start(response_id, worker_id, department) != 'RUNNING':
        raise RuntimeError('a resumed invocation requires a fresh response ID')
    started, attempted, failures = time.monotonic(), 0, []
    deadline = started + max_seconds
    while attempted < max_jobs and time.monotonic() < deadline:
        # Observe in order. A STOP behind CONTINUE remains authoritative, while
        # transport failure, malformed data or an excessive backlog safely YIELD.
        control, observed = _observe_bounded(port, response_id, failures, deadline)
        if not observed:
            return port.finish(response_id, stop=False, resume_capsule=capture_resume(), failures=failures)
        if control and control['action'] in ('STOP', 'DRAIN', 'PAUSE'):
            return port.finish(response_id, stop=control['action'] == 'STOP',
                               resume_capsule=capture_resume(), failures=failures)
        if time.monotonic() >= deadline:
            break
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
    control, observed = _observe_bounded(port, response_id, failures, deadline)
    return port.finish(response_id, stop=bool(observed and control and control['action'] == 'STOP'),
                       resume_capsule=capture_resume(), failures=failures)
