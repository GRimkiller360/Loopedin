"""Deterministic experiment-arm assignment -- computed from used_topics.json's current
length, not left to agent judgment, so assignment is reliably unbiased and can't drift
from what the actual count says run to run. Two independent axes:

- holdout: ~1 in 10 videos ignores all performance-steered guidance (category
  weighting, hook/style/length ranking, CTA-mix targets) entirely and picks on
  genuine judgment alone -- a permanent baseline so the self-improvement loop can
  tell whether its tuning is actually beating an unsteered baseline, not just
  exploiting whatever pattern it already found and converging on a local maximum.

- experiment_arm: "control" or "variant", alternating per video -- lets the
  self-improvement routine A/B one specific rule change against the current
  baseline concurrently instead of sequentially. Globally bumping RULESET_VERSION
  for every future video confounds any later comparison with whatever else changed
  in the meantime; two arms running side by side in the same window don't have that
  problem. "variant" is only meaningful when ROUTINE_INSTRUCTIONS.md currently
  defines an active variant-arm block -- with no active experiment, both arms
  behave identically.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.state_utils import load_json

HOLDOUT_EVERY_N = 10


def current_arm(used_topics_path):
    used = load_json(used_topics_path, {"topics": []})
    n = len(used["topics"])
    return {
        "holdout": (n % HOLDOUT_EVERY_N) == 0,
        "experiment_arm": "variant" if (n % 2) == 1 else "control",
        "video_index": n,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--used-topics", required=True)
    args = parser.parse_args()
    print(json.dumps(current_arm(args.used_topics), indent=2))
