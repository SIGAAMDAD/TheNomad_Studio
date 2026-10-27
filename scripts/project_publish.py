#!/usr/bin/env python3
"""Push verified Studio status to optional GitHub Project V2 without rescanning Issues."""
import argparse
import json
import pathlib
from board_sync import load, publish_project_status
from github_api import GitHub


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    config = load(args.root, "board.json")
    if not config.get("project_writeback") or not config.get("project_url"):
        print(json.dumps({"updated": 0, "status": "DISABLED"}))
        return 0
    features = load(args.root, "features.json")
    result = publish_project_status(GitHub(), features, config)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
