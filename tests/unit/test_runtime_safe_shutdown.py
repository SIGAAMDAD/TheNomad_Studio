"""A blocked publisher must not lose C while its thread may still be active."""
import pytest
from studio.app.errors import StudioError
from studio.app.lifecycle import Component, StudioRuntime


def test_failed_publisher_stop_preserves_db_until_retry():
    calls = []
    stopped = [False]

    def stop_publisher():
        calls.append('publisher')
        if not stopped[0]:
            stopped[0] = True
            raise RuntimeError('publisher still in flight')

    runtime = StudioRuntime([
        Component('C-APSW', lambda: calls.append('recover-db'),
                  lambda: calls.append('start-db'),
                  lambda: calls.append('stop-db')),
        Component('G-I-publisher', lambda: calls.append('recover-publisher'),
                  lambda: calls.append('start-publisher'), stop_publisher),
    ])
    runtime.start()
    with pytest.raises(StudioError, match='G-I-publisher failed to stop'):
        runtime.stop()
    assert 'stop-db' not in calls
    assert len(runtime._started) == 2
    runtime.stop()
    assert calls[-2:] == ['publisher', 'stop-db']
    assert not runtime._started


def test_stop_idempotent_when_fully_stopped():
    calls = []
    runtime = StudioRuntime([
        Component('db', lambda: None, lambda: None,
                  lambda: calls.append('close')),
    ])
    runtime.start()
    runtime.stop()
    runtime.stop()
    assert calls == ['close']
