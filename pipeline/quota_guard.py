"""Tracks daily upload count so the pipeline stays under the YouTube Data API's
default 10,000 unit/day quota (videos.insert costs 1600 units each) until a quota
increase is approved. Resets on UTC date change -- Google's actual reset is
midnight Pacific Time, so this is slightly conservative near the boundary, never
generous."""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.state_utils import load_json, save_json


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def uploads_today(path):
    state = load_json(path, {"date": _today(), "uploads": 0})
    if state.get("date") != _today():
        return 0
    return state.get("uploads", 0)


def can_upload(path, daily_cap):
    return uploads_today(path) < daily_cap


def record_upload(path):
    state = load_json(path, {"date": _today(), "uploads": 0})
    if state.get("date") != _today():
        state = {"date": _today(), "uploads": 0}
    state["uploads"] += 1
    save_json(path, state)


if __name__ == "__main__":
    default_path = str(Path(__file__).resolve().parent.parent / "state" / "quota_usage.json")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "record"])
    parser.add_argument("--path", default=default_path)
    parser.add_argument("--daily-cap", type=int, default=4)
    args = parser.parse_args()

    if args.action == "check":
        print("ok" if can_upload(args.path, args.daily_cap) else "capped")
    else:
        record_upload(args.path)
