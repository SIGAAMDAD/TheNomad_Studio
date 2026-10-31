"""Startup scans one stable C channel inventory before reconciling effects."""
from studio.app.startup import ChannelCursor, StartupRecovery


class ChangingState:
    def __init__(self):
        self.calls = 0
        self.effect_projects = []
        self.cursor_updates = []

    def channels(self):
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("recovery re-enumerated mutable channel set")
        return [ChannelCursor("project", "comms", None)]

    def outstanding_effects(self, *, project_id):
        self.effect_projects.append(project_id)
        return ["effect"]

    def persist_verified_cursor(self, *, project_id, channel_id,
                                expected_ref_sha, new_ref_sha):
        self.cursor_updates.append((project_id, new_ref_sha))
        return True

    def hold_channel_unknown(self, *, project_id, channel_id, reason):
        raise AssertionError(reason)


class FakeGitHub:
    def scan_since(self, *, project_id, channel_id, verified_ref_sha):
        return [], "a" * 40, True

    def reconcile_external_effect(self, *, project_id, effect_id):
        assert (project_id, effect_id) == ("project", "effect")
        return True


class NoProjection:
    def project_verified(self, observation):
        raise AssertionError("empty relay scan must not project")


def test_recovery_uses_one_channel_snapshot_for_cursors_and_effects():
    state = ChangingState()
    assert StartupRecovery(state, FakeGitHub(), NoProjection()).recover() == {
        "observations_projected": 0,
        "channels_held": 0,
        "projects_requiring_reconciliation": 0,
    }
    assert state.calls == 1
    assert state.cursor_updates == [("project", "a" * 40)]
    assert state.effect_projects == ["project"]
