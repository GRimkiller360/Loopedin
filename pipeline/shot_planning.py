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


def shots_for_duration(duration):
    count = max(1, round(duration / TARGET_SHOT_SECONDS))
    count = min(count, MAX_SHOTS_PER_BEAT)
    while count > 1 and duration / count < MIN_SHOT_SECONDS:
        count -= 1
    return count
