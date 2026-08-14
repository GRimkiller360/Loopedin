"""Schema for the per-run script.

The commentary script itself is written by the agent (Claude) directly -- using the
trend seed as inspiration only, never as source material to summarize or transcribe.
This module only defines and validates the expected shape so the deterministic
downstream stages (tts.py, broll.py, assemble.py) can rely on it.

Expected script.json shape:
{
  "topic": "short human-readable topic label, used for variety tracking",
  "category": "the broader bucket this topic falls under (copy trend_seed['seed_category']
               verbatim, e.g. 'science facts', 'life hacks') -- unlike topic, this repeats
               across videos, so it's what the performance-feedback loop can actually learn
               from ('science facts videos do well' is a generalizable signal; one specific
               topic's performance is not)",
  "title": "YouTube title, <=100 chars, hook + relevant keywords",
  "description": "YouTube description; mention this is AI-narrated commentary",
  "tags": ["...", "..."],
  "seed_source_video_id": "copy trend_seed['source_video_id'] verbatim (may be null if the
                           seed had no source video) -- lets trend_source.py exclude this
                           exact video from being resurfaced as a seed on a future run,
                           since comparing topic labels against video titles doesn't work
                           (different vocabularies, they never match)",
  "beats": [
    {"text": "one narration sentence/clause", "broll_query": "stock-footage search phrase for this beat"},
    ...
  ]
}
"""
import json
import sys

REQUIRED_TOP_LEVEL = {"topic", "category", "title", "description", "tags", "beats", "seed_source_video_id"}
REQUIRED_BEAT_KEYS = {"text", "broll_query"}
MIN_BEATS, MAX_BEATS = 3, 12
MAX_TITLE_LEN = 100


def validate(script):
    errors = []
    missing = REQUIRED_TOP_LEVEL - script.keys()
    if missing:
        errors.append(f"missing top-level keys: {sorted(missing)}")

    beats = script.get("beats") or []
    if not (MIN_BEATS <= len(beats) <= MAX_BEATS):
        errors.append(f"expected {MIN_BEATS}-{MAX_BEATS} beats, got {len(beats)}")
    for i, beat in enumerate(beats):
        missing_beat = REQUIRED_BEAT_KEYS - beat.keys()
        if missing_beat:
            errors.append(f"beat {i} missing keys: {sorted(missing_beat)}")

    if len(script.get("title", "")) > MAX_TITLE_LEN:
        errors.append(f"title exceeds {MAX_TITLE_LEN} chars")

    return errors


if __name__ == "__main__":
    path = sys.argv[1]
    data = json.loads(open(path, encoding="utf-8").read())
    errors = validate(data)
    if errors:
        print("INVALID:\n" + "\n".join(errors))
        sys.exit(1)
    print("valid")
