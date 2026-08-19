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
import re
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

# "people who like history" is an audience description, not a share trigger -- it names
# a topic-affinity category, not an actual person/relationship, and gives no message to
# send. Catches that whole family of generic phrasing regardless of which topic word
# fills in the blank.
GENERIC_SHARE_TRIGGER_RE = re.compile(
    r"\b(people|anyone|folks|those|fans|viewers|users)\s+(who|that)\s+"
    r"(like|love|enjoy|are into|are interested in|care about)\b",
    re.IGNORECASE,
)

# share_trigger must name an actual relationship, not just pass the generic-pattern
# check by accident -- require at least one of these. Named constant, not an inline
# literal, so it's one place to extend as real scripts surface relationship words this
# list is missing.
SHARE_TRIGGER_RELATIONSHIP_KEYWORDS = (
    "friend", "dad", "mum", "mom", "partner", "coworker", "colleague", "brother",
    "sister", "boss", "the person who", "anyone who told",
)

# A resolved-sounding opener on the final beat undoes the "don't resolve at the end"
# rule even when the actual content is fine -- these are the tells of a tidy summary
# rather than an open ending. Checked as a leading pattern (word boundary on "so" --
# not a bare substring match, which would also flag "somehow"/"sole"/etc).
BANNED_CLOSING_PATTERNS_RE = re.compile(
    r"^(so\b|and that's why\b|next time\b)", re.IGNORECASE,
)

SERIES_LABEL_RE = re.compile(r"^.+ #\d+$")

# Dropped before checking whether contradicted_belief's real content actually shows up
# in beat 0 -- otherwise two sentences sharing only function words (the/a/is/that/...)
# would look like they overlap when they share no actual content.
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "this",
    "that", "these", "those", "it", "its", "to", "of", "in", "on", "at", "for",
    "and", "or", "but", "not", "no", "so", "as", "by", "with", "from", "than",
    "then", "they", "their", "you", "your", "most", "people", "actually", "really",
    "just", "who", "what", "which", "do", "does", "did", "have", "has", "had",
}
MIN_BELIEF_BEAT0_OVERLAP = 0.25

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


def _word_set(text):
    # Strip ** too -- beat text carries **emphasis** caption markup (see
    # script_schema.py) that overlap checks against non-beat text (hook_candidates,
    # payoff_mechanism) would otherwise silently mismatch on (e.g. "**same**" != "same").
    return {w.strip(".,!?:;\"'*").lower() for w in text.split() if w.strip(".,!?:;\"'*")}


def _title_overlap(a, b):
    wa, wb = _word_set(a), _word_set(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _stem(word):
    # Crude suffix-stripping, not a real stemmer (no NLP library in this pipeline) --
    # just enough that "spoils"/"spoil" or "goes"/"go" aren't treated as unrelated
    # tokens by the overlap check below, which naive exact-string matching would do.
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _content_words(text):
    return {_stem(w) for w in _word_set(text) if w not in STOPWORDS and len(w) > 2}


def _directional_content_overlap(source_text, target_text):
    # Fraction of source_text's meaningful words that appear in target_text -- not
    # symmetric Jaccard like _title_overlap, since contradicted_belief and beat 0 are
    # never going to be similar *lengths*; what matters is whether beat 0 actually
    # contains the belief's key words, not whether the two texts are similar overall.
    source_words = _content_words(source_text)
    if not source_words:
        return 0.0
    return len(source_words & _content_words(target_text)) / len(source_words)


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

    # payoff_mechanism must actually show up in the narration, not just satisfy
    # script_schema.py's word-count check as an unused field. Checked against beats[1:]
    # (not beat 0, which is the hook, not the explanation) with the same low bar as the
    # hook-winner check -- light polish is fine, zero resemblance means the explanation
    # written in payoff_mechanism never actually made it into the video.
    mechanism = script.get("payoff_mechanism", "")
    if mechanism and len(beats) > 1:
        best_overlap = max(
            (_title_overlap(mechanism, b.get("text", "")) for b in beats[1:]),
            default=0.0,
        )
        if best_overlap < MIN_HOOK_WINNER_OVERLAP:
            errors.append(
                f"payoff_mechanism doesn't resemble any beat after the hook (best overlap="
                f"{best_overlap:.2f}) -- the explanation written in payoff_mechanism has to "
                "actually be narrated, not just exist as metadata"
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

    # share_trigger must name a specific relationship, not just describe who'd find the
    # topic interesting -- see ROUTINE_INSTRUCTIONS.md step 2.2 for why this matters
    # more than retention right now.
    trigger = (script.get("share_trigger") or "").strip()
    if trigger:
        if GENERIC_SHARE_TRIGGER_RE.search(trigger):
            errors.append(
                f"share_trigger reads as a generic audience description ({trigger!r}) -- "
                "name a specific relationship (e.g. 'the friend who...'), not just who'd "
                "find the topic interesting"
            )
        elif not any(kw in trigger.lower() for kw in SHARE_TRIGGER_RELATIONSHIP_KEYWORDS):
            errors.append(
                f"share_trigger doesn't name a specific relationship ({trigger!r}) -- "
                f"must contain one of: {', '.join(SHARE_TRIGGER_RELATIONSHIP_KEYWORDS)}"
            )

    # contradicted_belief has to actually be audible in beat 0, not just exist as
    # metadata -- same failure mode payoff_mechanism already guards against above.
    belief = (script.get("contradicted_belief") or "").strip()
    if belief and beats:
        overlap = _directional_content_overlap(belief, beats[0].get("text", ""))
        if overlap < MIN_BELIEF_BEAT0_OVERLAP:
            errors.append(
                f"contradicted_belief ({belief!r}) isn't audible in beat 0 (overlap="
                f"{overlap:.2f}) -- the belief being disproved has to actually be stated "
                "in the first ~3 seconds, not saved for later"
            )

    # A resolved-sounding final beat undoes the open-ending rule even when the actual
    # content is fine -- see ROUTINE_INSTRUCTIONS.md's closing-beat guidance (item 3).
    if beats:
        closer = beats[-1].get("text", "").strip()
        if BANNED_CLOSING_PATTERNS_RE.match(closer):
            errors.append(
                f"final beat opens with a banned resolved-summary pattern ({closer!r}) -- "
                "end on the claim restated harder, an open question, or a challenge, "
                "never a tidy wrap-up"
            )

    series_label = script.get("series_label") or ""
    if not SERIES_LABEL_RE.match(series_label):
        errors.append(
            f"series_label {series_label!r} doesn't match '<series_name> #<n>' -- read "
            "and increment state/series_log.json, don't invent a label"
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
