"""The historical S00-S09 scheduler cannot be reintroduced by source packaging."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def test_no_slot_scheduler_or_old_controller():
    obsolete = (
        "studio/slots.json", "studio/jobs.json", "studio/features.json",
        "studio/config.json", "studio/DIRECTOR_CONTRACT.md",
        "scripts/studio.py", "scripts/production.py",
        "scripts/studio_comms.py", "scripts/studio_director_pulse.py",
        "studio/v1", "studio/communications",
        "studio/architecture/hyperscale/v1",
    )
    assert not [p for p in obsolete if (ROOT / p).exists()]
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        assert "schedule:" not in text, workflow
        assert "studio/slots.json" not in text, workflow
        assert "scripts/studio.py" not in text, workflow
    for path in (ROOT / "scripts").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?<![A-Za-z0-9])S0[0-9](?![A-Za-z0-9])", text), path
        assert "scheduled_task_id" not in text, path


def test_active_skills_do_not_require_legacy_scheduler():
    for name in ("studio-director", "rpg-studio-all-roles"):
        text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        for obsolete in ("STUDIO_V1_EXECUTION", "STUDIO_V15_KERNEL", "studio/slots.json", "scheduled_task_id", "studio/config.json"):
            assert obsolete not in text, (name, obsolete)
