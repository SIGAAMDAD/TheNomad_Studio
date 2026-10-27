#!/usr/bin/env python3
"""Bounded, idempotent GitHub Issue / optional Projects V2 intake synchronization."""
import argparse
import hashlib
import json
import os
import pathlib
import re
from github_api import GitHub, GitHubError, owner_label, parse_project_url

ROOT = pathlib.Path(__file__).resolve().parents[1]
APPROVE = "studio:approved"
ACCEPT = "studio:accepted"
CANCEL = "studio:cancel"
FINALIZE = "studio:finalized"
PRESERVE = {"PRODUCTION", "QA", "REWORK", "OWNER_REVIEW", "READY_FOR_OWNER",
            "OWNER_ACCEPTED", "FINALIZED", "BLOCKED", "CANCEL_REQUESTED"}
PUBLISHABLE = {
    "SCOPING": "Scoping", "PRODUCTION": "Production", "QA": "Internal QA",
    "REWORK": "Rework", "BLOCKED": "Blocked", "OWNER_REVIEW": "Owner Review",
    "READY_FOR_OWNER": "Owner Review", "OWNER_ACCEPTED": "Accepted",
    "FINALIZED": "Finalized"
}


def load(root, filename):
    path = root / "studio" / filename
    return json.loads(path.read_text(encoding="utf-8"))


def save(root, filename, content):
    path = root / "studio" / filename
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def digest_issue(issue):
    body = issue.get("body") or ""
    return hashlib.sha256((issue.get("title", "") + "\n" + body).encode("utf-8")).hexdigest()


def feature_id(number):
    return "FEATURE-" + str(int(number))


def sync_issue(features, issue, events, config, board_item=None):
    n = issue.get("number")
    if not isinstance(n, int):
        raise ValueError("Issue missing integer number")
    repo = config["repo"]
    origin = (issue.get("repository_url") or "")
    if origin and not origin.rstrip("/").endswith("/" + repo):
        return False  # never ingest another repository's project-linked issues
    approved_actors = config.get("trusted_approvers", [])
    approved = owner_label(issue, events, APPROVE, approved_actors)
    accepted = owner_label(issue, events, ACCEPT, approved_actors)
    cancelled = owner_label(issue, events, CANCEL, approved_actors)
    finalized = owner_label(issue, events, FINALIZE, approved_actors)
    fid = feature_id(n)
    entry = next((f for f in features["features"] if f["id"] == fid), None)
    fresh = entry is None
    if entry is None:
        entry = {"id": fid, "source": "github_issue", "issue_number": n,
                 "repo": repo, "state": "SCOPING" if approved else "INBOX",
                 "jobs": [], "approved": False}
        features["features"].append(entry)
    previous_digest = entry.get("issue_digest")
    current_digest = digest_issue(issue)
    if previous_digest and previous_digest != current_digest and entry.get("jobs"):
        entry["changed_after_scoping"] = True
    entry.update({"issue_url": issue.get("html_url") or f"https://github.com/{repo}/issues/{n}",
                  "title": issue.get("title") or f"Issue {n}", "issue_digest": current_digest,
                  "issue_updated_at": issue.get("updated_at"), "approved": approved,
                  "owner_accept_requested": accepted, "owner_finalize_requested": finalized})
    if cancelled:
        entry["state"] = "CANCEL_REQUESTED"
    elif not approved and entry.get("jobs") and entry.get("state") != "FINALIZED":
        entry["state"] = "BLOCKED"
        entry["blocker"] = "Owner approval absent or revoked; manual source-owner fencing required"
    elif approved and (fresh or entry["state"] == "INBOX"):
        entry["state"] = "SCOPING"
    if board_item:
        entry["project_item_id"] = board_item["id"]
        entry["project_status"] = board_item.get("status")
    return fresh or entry.get("issue_digest") != previous_digest


def sync_draft(features, item, project_url):
    content = item.get("content") or {}
    draft_id = content.get("id")
    if not draft_id:
        return
    fid = "BOARD-" + hashlib.sha256(draft_id.encode("utf-8")).hexdigest()[:12].upper()
    entry = next((f for f in features["features"] if f["id"] == fid), None)
    if entry is None:
        features["features"].append({
            "id": fid, "source": "project_draft", "board_item_id": item.get("id"),
            "project_url": project_url, "title": content.get("title") or "Untitled draft",
            "brief": content.get("body") or "", "state": "INBOX", "approved": False,
            "jobs": [], "blocker": "Convert draft to repository issue and obtain owner approval label"
        })
    else:
        entry["title"] = content.get("title") or entry.get("title")
        entry["board_item_id"] = item.get("id")


def sync(features, issues, events_by_number, config, project_items=None):
    if features.get("schema") != 1 or not isinstance(features.get("features"), list):
        raise ValueError("Invalid features.json")
    board_by_issue = {}
    for item in project_items or []:
        content = item.get("content") or {}
        if content.get("number") and (content.get("repository") or {}).get("nameWithOwner") == config["repo"]:
            board_by_issue[int(content["number"])] = {
                "id": item["id"],
                "status": (item.get("fieldValueByName") or {}).get("name")
            }
        elif content.get("id"):
            sync_draft(features, item, config.get("project_url", ""))
    seen = set()
    for issue in issues:
        if "pull_request" in issue:
            continue
        number = int(issue["number"])
        if number in seen:
            raise ValueError("Duplicate issue in GitHub pagination")
        seen.add(number)
        sync_issue(features, issue, events_by_number.get(str(number), []),
                   config, board_item=board_by_issue.get(number))
    ids = [f["id"] for f in features["features"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate feature ID")
    features["features"].sort(key=lambda f: f["id"])
    return features


def github_fetch(gh, config):
    issues = gh.issues(config["repo"])
    events = {}
    target_labels = {APPROVE, ACCEPT, CANCEL, FINALIZE}
    for issue in issues:
        if any((label.get("name") if isinstance(label, dict) else label) in target_labels
               for label in issue.get("labels", [])):
            events[str(issue["number"])] = gh.issue_events(config["repo"], issue["number"])
    items = None
    project_error = None
    url = config.get("project_url")
    if url:
        try:
            owner, number, owner_type = parse_project_url(url)
            items = gh.project_items(owner, number, owner_type)["items"]
        except (GitHubError, ValueError) as exc:
            project_error = str(exc)
    return issues, events, items, project_error


def publish_project_status(gh, features, config):
    """Optional status mirror. Never write owner-input statuses (Inbox/Ready)."""
    if not config.get("project_writeback") or not config.get("project_url"):
        return {"updated": 0, "skipped": "Disabled"}
    owner, number, owner_type = parse_project_url(config["project_url"])
    field = gh.project_status_field(owner, number, owner_type)
    select = field.get("field") if field else None
    if not select:
        raise GitHubError("Project Status select field not accessible")
    names = {option["name"].lower(): option["id"] for option in select["options"]}
    updated = 0
    for feature in features["features"]:
        item_id = feature.get("project_item_id")
        wanted = PUBLISHABLE.get(feature.get("state"))
        if not item_id or not wanted or wanted.lower() not in names:
            continue
        if feature.get("project_status", "").lower() == wanted.lower():
            continue
        gh.set_project_status(field["id"], item_id, select["id"], names[wanted.lower()])
        feature["project_status"] = wanted
        updated += 1
    return {"updated": updated}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--fixture", type=pathlib.Path, help="Offline test JSON with issues/events/project_items")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--publish", action="store_true", help="Write reviewed statuses to Projects V2, only if configured")
    args = parser.parse_args(argv)
    config = load(args.root, "board.json")
    features = load(args.root, "features.json")
    gh = GitHub()
    if args.fixture:
        fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
        issues, events, items = (fixture.get(k) for k in ("issues", "events", "project_items"))
        error = None
    else:
        issues, events, items, error = github_fetch(gh, config)
    sync(features, issues or [], events or {}, config, items)
    output = {"features": len(features["features"]),
              "approved": sum(bool(f.get("approved")) for f in features["features"]),
              "project_items_read": 0 if items is None else len(items),
              "project_error": error}
    if args.write:
        save(args.root, "features.json", features)
    if args.publish and args.write and not args.fixture and not error:
        output["project_writeback"] = publish_project_status(gh, features, config)
        save(args.root, "features.json", features)
    print(json.dumps(output, indent=2))
    if error:
        print("Project connection DEGRADED (Issue intake remains available): " + error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
