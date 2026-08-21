"""Schema for the per-run script.

The commentary script itself is written by the agent (Claude) directly -- using the
trend seed as inspiration only, never as source material to summarize or transcribe.
This module only defines and validates the expected shape so the deterministic
downstream stages (tts.py, broll.py, assemble.py) can rely on it.

Expected script.json shape:
{
  "topic": "short human-readable topic label, used for variety tracking",
  "category": "the broader bucket this topic falls under (copy trend_seed['seed_category']
               verbatim -- currently always 'history', see CATEGORIES below) -- unlike
               topic, this repeats across videos, so it's what the performance-feedback
               loop can actually learn from ('history videos do well' is a generalizable
               signal; one specific topic's performance is not)",
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
  "share_trigger": "one sentence (>=12 words) completing 'a viewer sends this to ___ because
                    they want to ___' with a real, specific relationship (e.g. 'the friend
                    who still swears cracking your knuckles causes arthritis'), not a generic
                    audience description (e.g. 'people who like history'). This is the actual
                    growth bottleneck this field targets -- shares reach people the existing
                    audience and algorithm never would have surfaced the video to on their
                    own. Checked structurally here (word count) and for genericness by
                    quality_gate.py. See ROUTINE_INSTRUCTIONS.md's share-trigger step.",
  "contradicted_belief": "one sentence (>=8 words) stating what the viewer currently believes
                          that this video disproves. Must actually be audible in beat 0 --
                          quality_gate.py checks it resembles the opening beat, not just sits
                          here as unused metadata. See ROUTINE_INSTRUCTIONS.md's share-trigger
                          step.",
  "closing_comment": "one sentence (>=8 words), written fresh for THIS video's specific
                     topic/claim -- posted by pipeline/upload.py as a top-level comment
                     right after upload (not narrated, not pinned -- the Data API has no
                     pin endpoint). Must actually reference something specific from this
                     video (quality_gate.py checks it overlaps the topic/title, not a
                     interchangeable line that could sit under any video). A natural
                     subscribe/follow or comment-inviting nudge is welcome but not
                     required -- specificity is what's enforced, not a fixed formula. See
                     ROUTINE_INSTRUCTIONS.md's closing-comment step.",
  "sources": "optional list of {'url': '...', 'note': 'what this source actually confirms,
              <=20 words'} entries -- at least one real, independently-verifiable source for
              this video's central claim, found via WebSearch/WebFetch. pipeline/upload.py
              appends these to the video description automatically. Do not fabricate a
              citation if search tools aren't available this run -- omit the field entirely
              rather than invent a plausible-sounding fake source.",
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
    {"text": "one short narration sentence/clause (mostly 6-19 words -- see
             ROUTINE_INSTRUCTIONS.md's format) -- wrap AT MOST 1-2 words per beat in
             **double asterisks** to mark them for burned-in caption emphasis (color
             highlight + size bump, one color per sentence -- see EMPHASIS_COLORS
             below). Stripped automatically before TTS synthesis and beat-duration
             weighting (pipeline/config.py strip_emphasis_markup) so it never affects
             narration audio or timing -- this is caption-only styling. Use it on the
             one specific surprising word or number in a beat, not decoratively.",
     "broll_query": "stock-footage/AI-image search phrase for this beat",
     "beat_role": "one of BEAT_ROLES below -- what this beat's job is, not just its
                  text. Drives production decisions in pipeline/assemble.py (which
                  transitions get a whip-blur vs. a hard cut, which beats get a pan vs.
                  a zoom) that only make sense with real structural intent behind them,
                  not guessed from beat position alone. See ROUTINE_INSTRUCTIONS.md
                  step 2 for what each role means and when to use it."},
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
#
# 2026-08-21-claim-evidence-joke-v7: replaced the single-deep-payoff format with a
# claim/evidence/joke structure (hook -> 3x[claim, evidence, joke] -> dark claim ->
# hedge -> no-CTA ending) -- see ROUTINE_INSTRUCTIONS.md step 2 and
# state/ruleset_changelog.json for the full reasoning. payoff_mechanism is gone (no
# single deep mechanism in this format); beats now carry a required beat_role so
# assemble.py's transition/motion choices reflect real structural intent.
#
# 2026-08-21-closing-comment-v8: added closing_comment -- the posted-after-upload
# comment (pipeline/upload.py) is now written fresh per video instead of picked from a
# small fixed template pool, which was producing exact-duplicate comments across
# videos once category stopped varying (history-only). See
# state/ruleset_changelog.json.
RULESET_VERSION = "2026-08-21-closing-comment-v8"

REQUIRED_TOP_LEVEL = {"topic", "category", "title", "description", "tags", "beats", "seed_source_video_id", "hook_type", "hook_candidates", "share_trigger", "contradicted_belief", "closing_comment"}
REQUIRED_BEAT_KEYS = {"text", "broll_query", "beat_role"}
REQUIRED_HOOK_CANDIDATE_KEYS = {"hook_type", "text"}
MIN_BEATS, MAX_BEATS = 3, 16
MIN_HOOK_CANDIDATES = 3
MIN_SHARE_TRIGGER_WORDS = 12
MIN_CONTRADICTED_BELIEF_WORDS = 8
MIN_CLOSING_COMMENT_WORDS = 8
MAX_TITLE_LEN = 100

# What job each beat does -- see ROUTINE_INSTRUCTIONS.md step 2 for the full structure.
# assemble.py uses this (not beat position) to decide which transitions get a whip-blur
# vs. a hard cut, and which beats get a lateral pan vs. a zoom.
BEAT_ROLES = {"hook", "claim", "evidence", "joke", "hedge", "ending"}

# Fixed vocabularies -- must stay consistent across videos or the performance-feedback
# loop (pipeline/analytics_feedback.py) can't compare like with like. Keep in sync with
# pipeline/trend_source.py's SEED_CATEGORIES and the list documented in
# ROUTINE_INSTRUCTIONS.md.
#
# Narrowed to history alone 2026-08-21 per explicit channel-owner instruction (no
# diagnosed defect cited -- see state/ruleset_changelog.json). Was science
# facts/space/history from 2026-08-20 (that narrowing's own reasoning, now superseded:
# subscriber conversion was flat across a wider 10-category spread, and these three
# shared a proven content mechanic + lower factual-liability risk than the categories
# dropped then). History alone was also the standout performer of the three once
# avg_view_pct started breaking share_rate ties in the analytics ranking (2026-08-21) --
# ~223% avg view vs. ~58-60% for space/science facts, see state/ruleset_changelog.json's
# analytics_feedback.py entry.
CATEGORIES = {
    "history",
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

    trigger = (script.get("share_trigger") or "").strip()
    trigger_words = len(trigger.split())
    if trigger_words < MIN_SHARE_TRIGGER_WORDS:
        errors.append(
            f"share_trigger: need >={MIN_SHARE_TRIGGER_WORDS} words completing 'a viewer sends "
            f"this to ___ because they want to ___' with a real relationship, got {trigger_words}"
        )

    belief = (script.get("contradicted_belief") or "").strip()
    belief_words = len(belief.split())
    if belief_words < MIN_CONTRADICTED_BELIEF_WORDS:
        errors.append(
            f"contradicted_belief: need >={MIN_CONTRADICTED_BELIEF_WORDS} words stating what "
            f"the viewer currently believes that this video disproves, got {belief_words}"
        )

    closing_comment = (script.get("closing_comment") or "").strip()
    closing_comment_words = len(closing_comment.split())
    if closing_comment_words < MIN_CLOSING_COMMENT_WORDS:
        errors.append(
            f"closing_comment: need >={MIN_CLOSING_COMMENT_WORDS} words, written fresh for "
            f"this video's specific topic, got {closing_comment_words}"
        )

    sources = script.get("sources")
    if sources is not None:
        if not isinstance(sources, list):
            errors.append("sources must be a list of {'url', 'note'} entries if present")
        else:
            for i, s in enumerate(sources):
                if not isinstance(s, dict) or not s.get("url"):
                    errors.append(f"sources[{i}] missing a non-empty 'url' key")

    beats = script.get("beats") or []
    if not (MIN_BEATS <= len(beats) <= MAX_BEATS):
        errors.append(f"expected {MIN_BEATS}-{MAX_BEATS} beats, got {len(beats)}")
    for i, beat in enumerate(beats):
        missing_beat = REQUIRED_BEAT_KEYS - beat.keys()
        if missing_beat:
            errors.append(f"beat {i} missing keys: {sorted(missing_beat)}")
            continue
        role = beat["beat_role"]
        if role not in BEAT_ROLES:
            errors.append(f"beat {i}.beat_role {role!r} not in fixed set {sorted(BEAT_ROLES)}")
        if role == "hook" and i != 0:
            errors.append(f"beat {i} has beat_role 'hook' -- only beat 0 can be the hook")

    if beats and beats[0].get("beat_role") != "hook":
        errors.append("beat 0 must have beat_role 'hook'")

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
