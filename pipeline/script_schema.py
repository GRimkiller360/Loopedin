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
  "hook_type": "one of HOOK_TYPES below, describing how beat[0] opens the video. Like
                category, this repeats across videos on purpose so the
                performance-feedback loop can learn which opening style actually holds
                attention, separately from which topic category performs well.",
  "hook_candidates": "list of >=3 {'hook_type', 'text'} drafts actually considered before
                      beat[0] was locked in -- forces genuine hook planning instead of
                      writing beat[0] as an afterthought while drafting the rest of the
                      script. Must span at least 2 distinct hook_types, and one entry's
                      hook_type must match the script's top-level hook_type (the winner).
                      See ROUTINE_INSTRUCTIONS.md step 2.1.",
  "payoff_mechanism": "one plain-language sentence (>=20 words) stating the actual causal
                       reason behind the video's claim -- not a metaphor, not a restatement
                       of the hook. Written before the beats, same reasoning as
                       hook_candidates: forces genuine explanation to exist before it gets
                       compressed into short narration, instead of the compression itself
                       silently replacing the explanation with an assertion. Its content
                       must actually show up in the narration (checked by quality_gate.py),
                       not just sit here decoratively. See ROUTINE_INSTRUCTIONS.md step 2.2.",
  "seed_source_video_id": "copy trend_seed['source_video_id'] verbatim (may be null if the
                           seed had no source video) -- lets trend_source.py exclude this
                           exact video from being resurfaced as a seed on a future run,
                           since comparing topic labels against video titles doesn't work
                           (different vocabularies, they never match)",
  "seed_view_count": "optional -- copy the chosen candidate's trend_seed['view_count']
                      verbatim (0/absent if there was no source video). Tracked so the
                      performance-feedback loop can eventually test whether picking a
                      higher-momentum seed actually correlates with this channel's own
                      video performance, or whether it doesn't matter.",
  "beats": [
    {"text": "one narration sentence/clause -- wrap AT MOST 1-2 words per beat in "
             "**double asterisks** to mark them for burned-in caption emphasis "
             "(bold color highlight + size bump). Stripped automatically before TTS "
             "synthesis and beat-duration weighting (pipeline/config.py "
             "strip_emphasis_markup) so it never affects narration audio or timing --"
             "this is caption-only styling. Use it on the specific surprising word or "
             "number in a beat, not decoratively; marking most/every beat defeats the "
             "purpose since nothing stands out if everything does. See "
             "ROUTINE_INSTRUCTIONS.md step 2 for when to use it.",
     "broll_query": "stock-footage search phrase for this beat"},
    ...
  ]
}
"""
import json
import sys

# Bump this string whenever ROUTINE_INSTRUCTIONS.md's hook/pacing/writing guidance
# changes meaningfully. Auto-stamped onto every used_topics.json entry by
# produce-upload.yml (not something the agent sets itself) so a future performance
# comparison can actually tell whether a guidance change moved retention, instead of
# every video's history being lumped into one undifferentiated average forever.
RULESET_VERSION = "2026-08-19-payoff-mechanism-v4"

REQUIRED_TOP_LEVEL = {"topic", "category", "title", "description", "tags", "beats", "seed_source_video_id", "hook_type", "hook_candidates", "payoff_mechanism"}
REQUIRED_BEAT_KEYS = {"text", "broll_query"}
REQUIRED_HOOK_CANDIDATE_KEYS = {"hook_type", "text"}
MIN_BEATS, MAX_BEATS = 3, 12
MIN_HOOK_CANDIDATES = 3
MIN_PAYOFF_MECHANISM_WORDS = 20
MAX_TITLE_LEN = 100

# Fixed vocabularies -- must stay consistent across videos or the performance-feedback
# loop (pipeline/analytics_feedback.py) can't compare like with like. Keep in sync with
# pipeline/trend_source.py's SEED_CATEGORIES and the list documented in
# ROUTINE_INSTRUCTIONS.md.
#
# Narrowed from the original 10 to a single coherent niche -- subscriber conversion
# was flat 0.00/1k views across every category with the full spread, and a channel
# that jumps between tractors, sharks, and Albanian law gives neither viewers nor the
# algorithm a reason to expect what's next. These four share the same content
# mechanic that's already proven to drive retention here (a specific, checkable,
# counter-intuitive claim), and carry lower factual-liability risk for a fully
# automated, unreviewed pipeline than the categories dropped (personal finance reads
# as financial advice with zero human review; true crime mystery involves real
# victims/cases with no fact-check step; life hacks/technology/fitness are heavily
# saturated by existing large channels).
CATEGORIES = {
    "science facts", "psychology", "space", "history",
}
HOOK_TYPES = {"question", "shocking_fact", "myth_bust", "list", "story", "challenge"}


def validate(script):
    errors = []
    missing = REQUIRED_TOP_LEVEL - script.keys()
    if missing:
        errors.append(f"missing top-level keys: {sorted(missing)}")

    if script.get("category") is not None and script["category"] not in CATEGORIES:
        errors.append(f"category {script['category']!r} not in fixed set {sorted(CATEGORIES)}")

    if script.get("hook_type") is not None and script["hook_type"] not in HOOK_TYPES:
        errors.append(f"hook_type {script['hook_type']!r} not in fixed set {sorted(HOOK_TYPES)}")

    candidates = script.get("hook_candidates") or []
    if len(candidates) < MIN_HOOK_CANDIDATES:
        errors.append(f"hook_candidates: need at least {MIN_HOOK_CANDIDATES} drafted options, got {len(candidates)}")
    seen_texts, seen_hook_types = set(), set()
    for i, c in enumerate(candidates):
        missing_keys = REQUIRED_HOOK_CANDIDATE_KEYS - c.keys()
        if missing_keys:
            errors.append(f"hook_candidates[{i}] missing keys: {sorted(missing_keys)}")
            continue
        if c["hook_type"] not in HOOK_TYPES:
            errors.append(f"hook_candidates[{i}].hook_type {c['hook_type']!r} not in fixed set {sorted(HOOK_TYPES)}")
        norm_text = c["text"].strip().lower()
        if norm_text in seen_texts:
            errors.append(f"hook_candidates[{i}] is a near-duplicate of another candidate -- these must be genuinely distinct options, not the same line twice")
        seen_texts.add(norm_text)
        seen_hook_types.add(c["hook_type"])
    if len(seen_hook_types) < 2 and len(candidates) >= MIN_HOOK_CANDIDATES:
        errors.append("hook_candidates must span at least 2 distinct hook_types -- planning only variations on one style isn't genuine hook planning")
    if script.get("hook_type") is not None and candidates and script["hook_type"] not in seen_hook_types:
        errors.append("hook_type must match one of the drafted hook_candidates -- the winning hook has to actually be one of the options considered")

    mechanism = (script.get("payoff_mechanism") or "").strip()
    mechanism_words = len(mechanism.split())
    if mechanism_words < MIN_PAYOFF_MECHANISM_WORDS:
        errors.append(
            f"payoff_mechanism: need >={MIN_PAYOFF_MECHANISM_WORDS} words of actual causal "
            f"explanation, got {mechanism_words} -- a short phrase is almost always a metaphor "
            "or restatement standing in for a real mechanism, not the mechanism itself"
        )

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
