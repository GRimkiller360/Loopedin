"""Content-quality checks for a pending script, run right after schema validation and
before any production cost (TTS/b-roll/assembly/upload) is spent on it.

Unlike script_schema.py (structural shape, self-contained), these checks are heuristic
and need history (state/used_topics.json) to catch things like accidental near-duplicate
titles or lazy filler openers slipping past the routine's own instructions. A failure
here is treated the same as any other production failure by produce-upload.yml --
counts toward auto-pause, leaves state/pending_script.json in place for a human to
inspect (see ROUTINE_INSTRUCTIONS.md step 0.5).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.state_utils import load_json

MIN_WORDS, MAX_WORDS = 25, 160
MAX_BROLL_QUERY_WORDS = 8
RECENT_TITLES_TO_CHECK = 15
TITLE_OVERLAP_THRESHOLD = 0.7
MIN_HOOK_WINNER_OVERLAP = 0.15
HOOK_CANDIDATE_OVERLAP_THRESHOLD = 0.75

BANNED_OPENERS = (
    "so today", "in this video", "welcome back", "today we're talking about",
    "today we are talking about", "let's talk about", "in today's video",
    "hey guys", "what's up everyone",
    # Evidence-backed, not a guess: this channel's two weakest-retention hooks
    # (29.7% and 53.6% avg view) both opened with this exact pattern -- a familiar
    # observation the viewer already agrees with, not a claim that creates tension.
    # None of the strong hooks (up to 97.8%) use it. See ROUTINE_INSTRUCTIONS.md
    # step 2.1 for the full "pattern-observation vs specific claim" reasoning.
    "every year", "every few months", "every time",
)

# share_trigger must name an actual relationship, not a category of viewer -- these
# phrases are the tell that it's describing an audience segment instead ("people who
# like history") rather than a specific person a viewer would actually think of and
# send the video to. See ROUTINE_INSTRUCTIONS.md's share-trigger step.
GENERIC_SHARE_TRIGGER_PHRASES = (
    "people who like", "people who are into", "people who enjoy", "fans of",
    "anyone interested in", "anyone who likes", "those who love", "those who like",
    "people interested in", "history buffs", "science lovers", "space enthusiasts",
    "people into", "audience who",
)


def _word_set(text):
    # Strip ** too -- beat text carries **emphasis** caption markup (see
    # script_schema.py) that overlap checks against non-beat text (hook_candidates,
    # share_trigger, contradicted_belief) would otherwise silently mismatch on (e.g.
    # "**same**" != "same").
    return {w.strip(".,!?:;\"'*").lower() for w in text.split() if w.strip(".,!?:;\"'*")}


def _title_overlap(a, b):
    wa, wb = _word_set(a), _word_set(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def check(script, used_topics_path):
    errors = []

    beats = script.get("beats") or []
    total_words = sum(len(b.get("text", "").split()) for b in beats)
    if not (MIN_WORDS <= total_words <= MAX_WORDS):
        errors.append(f"narration word count {total_words} outside expected {MIN_WORDS}-{MAX_WORDS} range")

    if beats:
        opener = beats[0].get("text", "").strip().lower()
        for banned in BANNED_OPENERS:
            if opener.startswith(banned):
                errors.append(f"beat 0 opens with a banned filler phrase: {banned!r}")
                break

    # Pixabay (the b-roll provider) does keyword-OR matching with no scene understanding
    # -- a long cinematic broll_query dilutes the match and returns unrelated footage
    # matched on one stray word (verified in production: a full-sentence mask query
    # returned an ocean wave and a CPU socket). Catch it here, before wasting a
    # narration/b-roll/assembly run on a query that was never going to match well.
    for i, beat in enumerate(beats):
        query = beat.get("broll_query", "")
        word_count = len(query.split())
        if word_count > MAX_BROLL_QUERY_WORDS:
            errors.append(
                f"beat {i}'s broll_query is {word_count} words (max {MAX_BROLL_QUERY_WORDS}) -- "
                "must be a short literal keyword phrase, not a cinematic sentence"
            )

    # The winning hook_candidate must actually be the one used -- catches "planning
    # theater" where candidates get drafted per the schema but beat 0 is written as
    # something unrelated anyway. Light-touch polish between candidate and final beat 0
    # is fine (hence a low bar, not an exact match), but zero resemblance means the
    # planning step was skipped in substance even though the file satisfies the schema.
    winner = next(
        (c for c in (script.get("hook_candidates") or []) if c.get("hook_type") == script.get("hook_type")),
        None,
    )
    if winner and beats:
        overlap = _title_overlap(winner.get("text", ""), beats[0].get("text", ""))
        if overlap < MIN_HOOK_WINNER_OVERLAP:
            errors.append(
                f"beat 0 doesn't resemble the winning hook_candidate (overlap={overlap:.2f}) -- "
                "the drafted hook that matched hook_type must actually be the one used, not ignored"
            )

    # share_trigger's real growth value depends on naming an actual person/relationship,
    # not a demographic -- script_schema.py already enforces the >=12 word floor, this
    # catches the genericness a word count alone can't.
    trigger = (script.get("share_trigger") or "").lower()
    for phrase in GENERIC_SHARE_TRIGGER_PHRASES:
        if phrase in trigger:
            errors.append(
                f"share_trigger reads as a generic audience description ({phrase!r}) -- "
                "name an actual relationship (a specific person/group), not a category of viewer"
            )
            break

    # contradicted_belief must actually be audible in beat 0, not just exist as
    # metadata -- same low-bar overlap approach as the hook-winner check above, just
    # anchored to the opening beat instead since that's where it has to land.
    belief = script.get("contradicted_belief", "")
    if belief and beats:
        overlap = _title_overlap(belief, beats[0].get("text", ""))
        if overlap < MIN_HOOK_WINNER_OVERLAP:
            errors.append(
                f"contradicted_belief doesn't resemble beat 0 (overlap={overlap:.2f}) -- it "
                "must be audible in the opening beat, not just exist as metadata"
            )

    # closing_comment must actually reference this specific video, not be an
    # interchangeable line that could sit under any upload -- checked against both
    # topic and title (whichever it resembles), same low-bar overlap approach as the
    # other specificity checks above. This is what replaced upload.py's old small
    # fixed template pool, which was producing exact-duplicate comments across videos.
    closing_comment = script.get("closing_comment", "")
    if closing_comment:
        best_overlap = max(
            _title_overlap(closing_comment, script.get("topic", "")),
            _title_overlap(closing_comment, script.get("title", "")),
        )
        if best_overlap < MIN_HOOK_WINNER_OVERLAP:
            errors.append(
                f"closing_comment doesn't resemble this video's topic/title (best overlap="
                f"{best_overlap:.2f}) -- it reads as generic enough to sit under any video, "
                "not written specifically for this one"
            )

    # Candidates need to be genuinely different options, not the same idea reworded --
    # schema.py only catches exact-duplicate text; this catches near-duplicates.
    candidates = script.get("hook_candidates") or []
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            overlap = _title_overlap(candidates[i].get("text", ""), candidates[j].get("text", ""))
            if overlap >= HOOK_CANDIDATE_OVERLAP_THRESHOLD:
                errors.append(
                    f"hook_candidates[{i}] and [{j}] are too similar (overlap={overlap:.2f}) -- "
                    "these need to be genuinely distinct angles, not the same hook reworded"
                )

    used = load_json(used_topics_path, {"topics": []})
    recent_titles = [t["title"] for t in used["topics"][-RECENT_TITLES_TO_CHECK:] if t.get("title")]
    title = script.get("title", "")
    for recent in recent_titles:
        overlap = _title_overlap(title, recent)
        if overlap >= TITLE_OVERLAP_THRESHOLD:
            errors.append(f"title too similar (overlap={overlap:.2f}) to a recent title: {recent!r}")
            break

    return errors


if __name__ == "__main__":
    script_path, used_topics_path = sys.argv[1], sys.argv[2]
    data = json.loads(open(script_path, encoding="utf-8").read())
    errors = check(data, used_topics_path)
    if errors:
        print("QUALITY GATE FAILED:\n" + "\n".join(errors))
        sys.exit(1)
    print("ok")
