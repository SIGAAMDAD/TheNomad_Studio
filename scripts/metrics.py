#!/usr/bin/env python3
"""Zero-token studio production metrics; never invent elapsed times or run results."""
import argparse
import collections
import json
import pathlib
import datetime as dt


def summarize(features, jobs, slots):
    state = collections.Counter(j.get("state", "INVALID") for j in jobs["jobs"])
    feature_states = collections.Counter(f.get("state", "INVALID") for f in features["features"])
    disciplines = {}
    for j in jobs["jobs"]:
        key = j.get("kind", "unknown")
        d = disciplines.setdefault(key, {"total": 0, "submitted": 0,
                                          "verified": 0, "blocked": 0, "rework": 0})
        d["total"] += 1
        if j.get("state") == "SUBMITTED":
            d["submitted"] += 1
        if j.get("state") in ("VERIFIED", "ACCEPTED"):
            d["verified"] += 1
        if j.get("state") == "BLOCKED":
            d["blocked"] += 1
        if j.get("qa_findings") or j.get("state") == "REWORK":
            d["rework"] += 1
    return {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "feature_states": dict(sorted(feature_states.items())),
            "job_states": dict(sorted(state.items())),
            "disciplines": disciplines,
            "active_task_slots": [s["id"] for s in slots["slots"] if s.get("enabled")],
            "occupied_slots": [s["id"] for s in slots["slots"] if s.get("job")],
            "review_pending": [f["id"] for f in features["features"]
                               if f.get("state") == "READY_FOR_OWNER"],
            "blocked_jobs": [{"id": j["id"], "blocker": j.get("blocker", "")}
                             for j in jobs["jobs"] if j.get("state") == "BLOCKED"],
            "verified_deliverables": sum(state.get(x, 0) for x in ("VERIFIED", "ACCEPTED")),
            "note": "Counts are from persisted records; no execution duration, model costs, or inferred tests."}


def markdown(data):
    lines = ["# The Nomad Studio — Operational Report", "",
             "Generated UTC: " + data["generated_utc"], "",
             "Verified deliverables: " + str(data["verified_deliverables"]), "",
             "## Feature states"]
    lines.extend(f"- {key}: {val}" for key, val in data["feature_states"].items())
    lines += ["", "## Department work"]
    lines += ["| Kind | Total | Submitted | Verified | Blocked | Rework |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, row in sorted(data["disciplines"].items()):
        lines.append(f"| {name} | {row['total']} | {row['submitted']} | "
                     f"{row['verified']} | {row['blocked']} | {row['rework']} |")
    lines += ["", "## Needs owner review"]
    lines.extend(f"- {ref}" for ref in data["review_pending"])
    if not data["review_pending"]:
        lines.append("- None")
    lines += ["", "## Blockers"]
    lines.extend(f"- {x['id']}: {x['blocker'] or 'Unspecified'}"
                 for x in data["blocked_jobs"])
    if not data["blocked_jobs"]:
        lines.append("- None")
    lines += ["", "These are ledger facts, not a promise of builds, 3D assets, or completed scheduled runs.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)
    payload = [json.loads((args.root / "studio" / name).read_text())
               for name in ("features.json", "jobs.json", "slots.json")]
    result = summarize(*payload)
    text = markdown(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        args.output.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
