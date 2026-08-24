"""Shared shot-count math used by broll.py to decide how many distinct AI images to
generate per beat, from that beat's real measured duration (tts.py's synthesize_beats()
sidecar -- see broll.py's fetch_all()). Kept in one place so the shot-count formula
itself has a single definition rather than being duplicated anywhere else that needs it.
"""


# Splits each beat's screen time into several short visual shots instead of one static
# hold -- a real published video (Portugal 1966 World Cup, 2026-08-21) measured only 8
# shots across 36s (mean 4.49s, longest 6.93s) against a healthy reference's 31 shots
# (mean 1.44s): the reference changes image 3.4x faster than it changes topic, and the
# owner's own analysis identified that ratio -- not just motion within one held image --
# as the actual source of momentum between cuts. Verified these three constants against
# the real beat durations from that same video (~[4.48,5.67,6.90,2.55,4.10,6.30,4.57,
# 1.43]s): this math produces 25 total shots, matching "roughly 25 images for a
# 36-second video" almost exactly, rather than being picked from a bare estimate.
TARGET_SHOT_SECONDS = 1.5
MIN_SHOT_SECONDS = 0.9
MAX_SHOTS_PER_BEAT = 6

# Per-role override of TARGET_SHOT_SECONDS -- 2026-08-24, a real published video's
# shot durations measured as a near-metronome (mean 1.93s, std 0.17s, every single shot
# between 1.70-2.54s) against a healthy reference's real rhythm (mean 1.44s but std
# 0.50s: a fast hook zone at 0.88s mean for its first four shots, then a slower
# 1.86s-mean information zone once the video settles in). A flat TARGET_SHOT_SECONDS
# everywhere can't produce that shape no matter how it's tuned -- it needs to vary by
# what the beat IS, not just how long it runs. hook/joke/ending get the reference's
# fast-zone rate (punchy, matches how those roles already get whip-blur transitions
# and a stronger zoom in assemble.py); claim/evidence/hedge get close to the
# reference's own slow-zone rate (real information needs a beat longer than a punchline
# does to actually register). Falls back to the flat TARGET_SHOT_SECONDS for an
# unrecognized/missing role rather than erroring -- this is a pacing refinement, not a
# structural requirement the rest of the pipeline should ever hard-depend on.
ROLE_TARGET_SHOT_SECONDS = {
    "hook": 0.9,
    "joke": 1.0,
    "ending": 1.1,
    "claim": 1.8,
    "evidence": 1.8,
    "hedge": 1.8,
}


def shots_for_duration(duration, beat_role=None):
    target = ROLE_TARGET_SHOT_SECONDS.get(beat_role, TARGET_SHOT_SECONDS)
    count = max(1, round(duration / target))
    count = min(count, MAX_SHOTS_PER_BEAT)
    while count > 1 and duration / count < MIN_SHOT_SECONDS:
        count -= 1
    return count
