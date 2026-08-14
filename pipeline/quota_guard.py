"""Tracks daily upload activity against the YouTube Data API's default 10,000
unit/day quota (videos.insert costs 1600 units each) until a quota increase is
approved.

Reactive, not just predictive: rather than guessing a conservative safe number and
never trying past it, this lets uploads actually attempt each day and only marks the
day "exhausted" once YouTube's API itself hard-rejects a call with a quota error (see
pipeline/upload.py). That's the authoritative signal, not a local guess -- it means
some days get more real uploads than a fixed conservative cap would ever allow.
`daily_cap` still exists as a sanity ceiling on attempts (defense in depth against a
runaway loop), not the primary mechanism.

Resets on UTC date change -- Google's actual reset is midnight Pacific Time, so this
is slightly conservative near the boundary, never generous.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.state_utils import load_json, save_json


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _state_for_today(path):
    state = load_json(path, {"date": _today(), "uploads": 0, "exhausted": False})
    if state.get("date") != _today():
        state = {"date": _today(), "uploads": 0, "exhausted": False}
    return state


def uploads_today(path):
    return _state_for_today(path).get("uploads", 0)


def is_exhausted_today(path):
    return _state_for_today(path).get("exhausted", False)


def can_upload(path, daily_cap):
    state = _state_for_today(path)
    if state.get("exhausted"):
        return False
    return state.get("uploads", 0) < daily_cap


def record_upload(path):
    state = _state_for_today(path)
    state["uploads"] += 1
    save_json(path, state)


def record_quota_exhausted(path):
    state = _state_for_today(path)
    state["exhausted"] = True
    save_json(path, state)


if __name__ == "__main__":
    default_path = str(Path(__file__).resolve().parent.parent / "state" / "quota_usage.json")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "record", "exhaust"])
    parser.add_argument("--path", default=default_path)
    parser.add_argument("--daily-cap", type=int, default=8)
    args = parser.parse_args()

    if args.action == "check":
        print("ok" if can_upload(args.path, args.daily_cap) else "capped")
    elif args.action == "record":
        record_upload(args.path)
    else:
        record_quota_exhausted(args.path)
