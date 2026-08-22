"""Assemble the final vertical short: cut b-roll to beats, burn captions, mix
narration + background music. Uses ffmpeg directly (installed by setup_env.sh) rather
than a Python video library -- fewer deps to reinstall on every fresh cloud checkout.

Caption timing has no word-level timestamps from the TTS step, so each beat's on-screen
duration is estimated proportionally to its text length against the narration's actual
audio duration. Good enough for short-form captions; not frame-perfect.
"""
import argparse
import json
import math
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config

WIDTH, HEIGHT = 1080, 1920

# One accent color per beat (not always gold) for **word**-marked emphasis, ASS inline
# &HBBGGRR& order -- rotates through EMPHASIS_COLORS below, picked per beat rather than
# fixed, so the highlighted keyword actually reads as "the one word that matters in
# this sentence" instead of a static style element the eye tunes out after a few
# repeats. Base caption style is already bold, so emphasis is color + a slight size
# bump rather than bold-on-bold, which wouldn't read as distinct.
# RGB -> BBGGRR: white FFFFFF, yellow FFFF00->00FFFF, green 33CC66->66CC33,
# red FF3B30->303BFF, magenta FF2D95->952DFF, cyan 30D5F2->F2D530.
EMPHASIS_COLORS = ["FFFFFF", "00FFFF", "66CC33", "303BFF", "952DFF", "F2D530"]
EMPHASIS_RESET = r"{\r}"


def _emphasis_override(color_hex):
    return rf"{{\c&H{color_hex}&\fscx115\fscy115}}"

# Every caption chunk pops in (scales up from 70% to 100%) over 120ms instead of just
# appearing statically -- with captions now switching every 1-2 words (WORDS_PER_CAPTION),
# that little burst of motion on each change is what actually keeps the eye locked to a
# constantly-refreshing caption instead of tuning it out, not just the bigger/centered
# static style. If a chunk's first word is also **emphasis**-marked, the emphasis
# override's own hard-set \fscx115 supersedes the pop transform for that word (ASS
# override tags on the same property don't blend) -- a rare, cosmetically minor
# interaction, not a bug, since emphasis marks are sparse (>=1-2 per beat, not per chunk).
POP_IN_DURATION_MS = 120
POP_IN_TAG = rf"{{\fscx70\fscy70\t(0,{POP_IN_DURATION_MS},\fscx100\fscy100)}}"


def _probe_duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(out.strip())


def _beat_durations(beats, total_duration):
    weights = [max(len(config.strip_emphasis_markup(b["text"])), 1) for b in beats]
    total_weight = sum(weights)
    return [total_duration * w / total_weight for w in weights]


def _format_ass_timestamp(seconds):
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text):
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


# TikTok-style dynamic captions: 1 word on screen at a time instead of the whole beat
# sentence at once -- the eye can't skim ahead of a caption that keeps changing, which
# is exactly this channel's own top lever (avg_view_pct/retention). Dropped from 2->1
# 2026-08-21 after direct measurement against a real published video: 2-word chunks
# averaged 0.76s on screen (47 chunks in 36s) against a reference's ~0.42s (~105
# chunks in 44.65s), and occasionally line-wrapped to two lines at this font size,
# which never happens with true one-word chunks.
WORDS_PER_CAPTION = 1


def _tokenize_beat(text):
    """Splits beat text into (word, is_emphasized) tokens, based on the same **word**
    markers (see script_schema.py) _beat_text_to_ass used to consume whole-beat --
    needed per-word now since captions render 1-2 words at a time instead of a beat's
    full sentence. Splits on whitespace first, then strips ** per token, rather than
    re.split()-ing the whole string on the ** pairs first -- the latter drops trailing
    punctuation stuck directly to an emphasized word's closing ** (e.g. "**1518**,")
    into its own orphaned punctuation-only token with no word attached."""
    tokens = []
    for raw_word in text.split():
        is_emphasized = "**" in raw_word
        tokens.append((raw_word.replace("**", ""), is_emphasized))
    return tokens


def _chunk_to_ass(chunk, emphasis_color):
    words = []
    for word, is_emphasized in chunk:
        escaped = _ass_escape(word)
        words.append(f"{_emphasis_override(emphasis_color)}{escaped}{EMPHASIS_RESET}" if is_emphasized else escaped)
    return POP_IN_TAG + " ".join(words)


ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
; Fontsize 90->130, Outline 6->8, Shadow 0->3, Alignment 2 (bottom-center) -> 5
; (middle-center), MarginV 300->0 -- moved dead-center per explicit request
; (2026-08-21) and sized up now that each caption is only 1-2 words (WORDS_PER_CAPTION)
; instead of a full sentence, so there's plenty of room without wrapping.
Style: Default,Arial,130,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,8,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _write_ass(beats, durations, out_path):
    lines = [ASS_HEADER]
    t = 0.0
    for beat_index, (beat, beat_duration) in enumerate(zip(beats, durations)):
        tokens = _tokenize_beat(beat["text"])
        if not tokens:
            t += beat_duration  # keep later beats' timing aligned even on empty text
            continue

        # One color per beat/sentence, not one fixed color for the whole video --
        # rotates by beat index so consecutive beats don't repeat.
        emphasis_color = EMPHASIS_COLORS[beat_index % len(EMPHASIS_COLORS)]

        chunks = [tokens[i:i + WORDS_PER_CAPTION] for i in range(0, len(tokens), WORDS_PER_CAPTION)]
        # No real per-word timestamps from the TTS step (see module docstring) -- each
        # chunk's on-screen share of the beat is estimated proportionally to its word
        # length, same approach _beat_durations already uses per-beat against the
        # narration's actual audio duration, just carried one level deeper.
        total_weight = sum(max(len(word), 1) for chunk in chunks for word, _ in chunk)
        for chunk in chunks:
            chunk_weight = sum(max(len(word), 1) for word, _ in chunk)
            chunk_duration = beat_duration * chunk_weight / total_weight
            start, end = t, t + chunk_duration
            ass_text = _chunk_to_ass(chunk, emphasis_color)
            lines.append(f"Dialogue: 0,{_format_ass_timestamp(start)},{_format_ass_timestamp(end)},Default,,0,0,0,,{ass_text}")
            t = end
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")


CLIP_DURATION_BUFFER = 0.35  # each segment overshoots slightly so frame-rounding
                              # across N clips can never leave the concatenated
                              # video shorter than the narration audio

# Transition type as punctuation, not decoration -- a whip-blur transition marks a
# structurally important cut (into/out of a joke, into the hedge, into the ending),
# while an ordinary claim/evidence beat gets a near-instant hard cut. Using the same
# transition everywhere (what this pipeline did before) makes every cut feel equally
# weighted; varying it signals which moments actually matter. Both still go through the
# same xfade chain in _build_video_track -- WHIP_TRANSITION_DURATION is also the
# padding basis for every clip (see clip_targets in assemble()), a safe upper bound
# since HARD_CUT_DURATION is always smaller.
WHIP_TRANSITION_DURATION = 0.16
HARD_CUT_DURATION = 0.06
TRANSITION_DURATION = WHIP_TRANSITION_DURATION  # padding basis, see comment above

# beat_role values (script_schema.py's BEAT_ROLES) whose transition IN is a whip --
# matches the structural pattern found in a reference video's own edit: whip lands on
# joke entries/exits and tonal shifts, hard cut carries ordinary fact-to-fact moves.
WHIP_ROLES = {"joke", "hedge", "ending"}


def _transition_duration(beats, i):
    """Duration for the transition INTO beat i (i.e. between beat i-1 and beat i) --
    whip if beat i is entering a structurally important role, or beat i-1 was a joke
    (so the joke gets punctuated on both entry and exit)."""
    if beats[i].get("beat_role") in WHIP_ROLES or beats[i - 1].get("beat_role") == "joke":
        return WHIP_TRANSITION_DURATION
    return HARD_CUT_DURATION


OUTPUT_FPS = 30


# Continuous push-in zoom, magnitude depends on which beat -- a documented,
# easily-automated visual pattern-interrupt/pacing device that doesn't need to know
# *where* the interesting thing in frame is, unlike a pointer/circle overlay would --
# it just scales toward center, so it can't end up pointing at nothing. Beat 0 gets a
# dramatic punch (distinct treatment on the hook); every other beat gets a lighter
# version -- static, unmoving stock footage/AI images read as low-effort and give the
# eye nothing to track, so keep constant motion through the whole video, not just the
# opening. Raised sharply 2026-08-21 (1.15->1.35 hook, 1.06->1.20 subtle) after
# feedback that the previous values read as static/boring on real AI-generated images
# -- a 6% zoom over a couple seconds is genuinely close to imperceptible at normal
# viewing speed. Bigger zoom_end also gives PAN_TARGETS below more actual room to
# drift toward (the crop window's available travel is proportional to how far zoom_end
# is above 1.0), so this also makes the Ken Burns pan more visible, not just the zoom.
HOOK_ZOOM_END = 1.35
SUBTLE_ZOOM_END = 1.20

# Applied to every clip in every video -- a consistent color grade + vignette is a
# visual signature: raw unfiltered stock footage looks like raw unfiltered stock
# footage no matter whose channel it's on, but a consistent, distinct grade is
# something a viewer can start to recognize as *this channel's* look across videos,
# unlike per-video variation which reads as generic every time. Mild boost to
# contrast/saturation plus a soft vignette pulling focus toward center -- deliberately
# conservative, not stylized enough to fight the footage or look artificial.
SIGNATURE_LOOK_FILTER = "eq=contrast=1.08:saturation=1.18:brightness=0.01,vignette=PI/4"

# Real Ken Burns pan targets (fractional focal point the crop window drifts toward as
# it zooms), applied to every clip -- every clip is now an AI-generated held image
# (see ai_broll.py) with no motion of its own, so pairing zoom with a genuine
# directional drift (not just zooming on the dead center) is what keeps it from
# reading as "a photo with a zoom filter."
PAN_TARGETS = [(0.15, 0.25), (0.85, 0.25), (0.15, 0.75), (0.85, 0.75), (0.5, 0.15), (0.5, 0.85)]

def _scale_clip(src, dst, target, zoom=None, pan_target=None):
    # fps filter forces a real, constant frame rate on the AI-generated held-image
    # clip's own encode -- concatenating segments with different/variable frame rates
    # causes a stutter at each cut point even though the audio/captions stay on
    # schedule.
    vf = (
        f"fps={OUTPUT_FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},{SIGNATURE_LOOK_FILTER}"
    )
    zoom_end = {"hook": HOOK_ZOOM_END, "subtle": SUBTLE_ZOOM_END}.get(zoom)
    if zoom_end:
        # Driven entirely off ffmpeg's built-in `t` (elapsed seconds in this filter's
        # own timeline), not zoompan's frame-to-frame self-referential "zoom"
        # accumulator -- switched 2026-08-22 after a real published video (channel-owner
        # report: "completely frozen") confirmed every clip was rendering at one
        # unmoving frame despite the zoompan z='if(eq(on,1),1,min(zoom+step,zoom_end))'
        # construction below (the standard documented workaround for zoompan's own
        # frame-1 reset bug). That construction still depends on the filter correctly
        # persisting "zoom" across frames, which apparently doesn't hold on this
        # pipeline's real ffmpeg 6.1.1 build for a genuine multi-frame video input (as
        # opposed to zoompan's more common use case of a single `-loop 1` image with a
        # large `d`). `crop`'s w/h/x/y expressions are recomputed fresh from `t` on
        # every single frame instead -- there is no accumulator to persist, so there's
        # nothing left to fail to advance.
        progress = f"min(t/{target},1)"
        fx, fy = pan_target if pan_target else (0.5, 0.5)
        # Drift the crop window's center from frame-center toward (fx,fy) as the zoom
        # progresses from 1.0 to zoom_end, instead of staying centered -- see
        # PAN_TARGETS' comment. cx/cy are the crop window's center as a 0-1 fraction of
        # the frame; out_w/out_h (ffmpeg's names for THIS crop's own computed width/
        # height) let x/y reference the already-shrinking crop window directly instead
        # of recomputing its size a second time. max/min clamp to the valid crop range
        # for the same reason the old zoompan version did: an unclamped pan target can
        # put the raw center position outside the frame at modest zoom levels.
        cx = f"(0.5+({progress})*({fx}-0.5))"
        cy = f"(0.5+({progress})*({fy}-0.5))"
        crop_w = f"iw/(1+({zoom_end}-1)*({progress}))"
        crop_h = f"ih/(1+({zoom_end}-1)*({progress}))"
        vf += (
            f",crop=w='{crop_w}':h='{crop_h}':"
            f"x='max(0,min(in_w-out_w,in_w*{cx}-out_w/2))':"
            f"y='max(0,min(in_h-out_h,in_h*{cy}-out_h/2))',"
            f"scale={WIDTH}:{HEIGHT}"
        )
    clip_len = _probe_duration(src)
    loop_count = max(math.ceil(target / clip_len), 1) if clip_len > 0 else 1
    subprocess.run([
        "ffmpeg", "-y", "-stream_loop", str(loop_count - 1), "-i", str(src),
        "-t", str(target), "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", str(dst),
    ], check=True)


def _cut_offsets(clip_targets, transition_durations):
    """Absolute-time offset (seconds) of every cut point, one entry per shot after the
    first. Shared between _build_video_track's xfade chain and _build_swoosh_track's
    sound placement (see assemble()) so the swoosh can never drift out of sync with the
    actual video cut it's supposed to punctuate -- both derive from this exact same math
    instead of two separate copies of it."""
    offsets = []
    cumulative = clip_targets[0]
    for i in range(1, len(clip_targets)):
        duration = transition_durations[i]
        offsets.append(max(cumulative - duration, 0.0))
        cumulative = cumulative + clip_targets[i] - duration
    return offsets


def _build_video_track(scaled_paths, clip_targets, transition_durations, out_path):
    """Crossfades consecutive shot clips into video_track.mp4 instead of hard-cutting
    between them -- chains ffmpeg's xfade filter across all N clips in one pass, rather
    than the old concat-demuxer approach (concat can't overlap two streams at once, only
    play them back to back, so a real crossfade needs every clip as a live filter-graph
    input instead). transition_durations[i] is the duration of the cut INTO shot i
    (index 0 is unused -- the first shot has nothing to transition from); the caller
    (assemble()) decides per-shot whether that's a real beat-boundary whip/hard-cut
    (see WHIP_ROLES/_transition_duration) or an ordinary within-beat sub-shot cut."""
    n = len(scaled_paths)
    inputs = []
    for p in scaled_paths:
        inputs += ["-i", str(p)]

    if n == 1:
        filter_complex, final_label = None, "0:v"
    else:
        offsets = _cut_offsets(clip_targets, transition_durations)
        parts = []
        prev_label = "0:v"
        for i in range(1, n):
            duration = transition_durations[i]
            transition = "hblur" if duration == WHIP_TRANSITION_DURATION else "fade"
            out_label = f"v{i}"
            parts.append(
                f"[{prev_label}][{i}:v]xfade=transition={transition}:"
                f"duration={duration}:offset={offsets[i - 1]:.3f}[{out_label}]"
            )
            prev_label = out_label
        filter_complex, final_label = ";".join(parts), prev_label

    cmd = ["ffmpeg", "-y", *inputs]
    if filter_complex:
        cmd += ["-filter_complex", filter_complex, "-map", f"[{final_label}]"]
    else:
        cmd += ["-map", final_label]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-r", str(OUTPUT_FPS), "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


# Category -> preferred music mood tags. assets/music/tags.json (user-maintained) maps
# filename -> list of mood tags; a track matching any preferred tag for the script's
# category is preferred over a fully random pick. Inert and harmless until tagged
# files actually exist -- falls straight back to random, same as before this existed.
CATEGORY_MOODS = {
    "history": ("mysterious", "dramatic", "epic"),
}


MUSIC_ENABLED = False  # disabled per user request (2026-08-18) -- re-enable by flipping this back


def _pick_music_track(music_dir, category):
    if not MUSIC_ENABLED:
        return None

    music_dir = Path(music_dir)
    tracks = list(music_dir.glob("*.mp3"))
    if not tracks:
        return None

    tags_path = music_dir / "tags.json"
    tags = {}
    if tags_path.exists():
        try:
            tags = json.loads(tags_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            tags = {}

    preferred = set(CATEGORY_MOODS.get(category, ()))
    if preferred and tags:
        matches = [t for t in tracks if preferred & set(tags.get(t.name, []))]
        if matches:
            return random.choice(matches)

    return random.choice(tracks)


# Caps any internal silence in the narration at MAX_PAUSE_SECONDS instead of leaving
# whatever gap Cloud TTS naturally inserts at each sentence boundary -- a real
# published video (2026-08-21) measured 13 pauses totaling 6.4s (18% of runtime),
# longest 0.96s, against a healthy reference's 2.78s/6%. Deliberately NOT done via
# SSML <break> tags: Chirp3-HD's SSML tag support is genuinely unclear/inconsistently
# documented as of this session (no way to verify locally, and a malformed/unsupported
# tag risks breaking synthesis entirely for every video, a far worse failure than the
# pause issue itself). silenceremove operates on the already-synthesized audio file
# with a well-documented, stable ffmpeg filter instead -- stop_periods=-1 targets every
# internal silence (not just leading/trailing), and stop_duration caps each one at
# MAX_PAUSE_SECONDS by dropping only the excess beyond that point, not the whole gap --
# still reads as a natural sentence pause, just not a stall.
MAX_PAUSE_SECONDS = 0.25
SILENCE_THRESHOLD_DB = "-40dB"  # narration's dead air measured -57 to -60+ dBFS --
                                # comfortably below any actual quiet speech, low risk
                                # of clipping a real quiet word as "silence"


def _trim_long_pauses(narration_path, work_dir):
    trimmed = work_dir / "narration_trimmed.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(narration_path),
        "-af", f"silenceremove=stop_periods=-1:stop_duration={MAX_PAUSE_SECONDS}:stop_threshold={SILENCE_THRESHOLD_DB}",
        "-ar", "44100", "-ac", "2", str(trimmed),
    ], check=True)
    return trimmed


# A quiet, non-rhythmic ambient bed under the narration -- a real published video
# measured as pure mono with dead silence between words (no independent low end at
# all), while a healthy reference video ran a continuous stereo bed ~16dB under the
# voice with no detectable beat (autocorrelation peak 0.06). Synthesized rather than a
# sourced track, same reasoning as the hook chirp/mascot chime already in this file --
# zero licensing/copyright question, and it sidesteps MUSIC_ENABLED's file-based system
# entirely (assets/music/ has no files in it, and that flag was about actual music
# tracks specifically, not this kind of textureless room-tone bed). Three detuned low
# sine tones with independent slow tremolo -- deliberately non-musical/non-rhythmic so
# it can't create the "is there a beat" impression the reference explicitly lacked.
def _build_ambient_bed(work_dir, duration_seconds):
    bed = work_dir / "ambient_bed.mp3"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=80:duration={duration_seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=121:duration={duration_seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=163:duration={duration_seconds}",
        "-filter_complex",
        # ffmpeg's tremolo filter rejects f<0.1 outright ("Numerical result out of
        # range") -- 0.1 is the floor, not just a suggestion, confirmed by a real
        # production crash on every run since this landed. 0.1/0.11 (the third tone's
        # already-valid value) is as close to the intended near-imperceptible slow
        # wobble as the filter actually allows.
        "[0:a]tremolo=f=0.1:d=0.3[a0];"
        "[1:a]tremolo=f=0.11:d=0.3[a1];"
        "[2:a]tremolo=f=0.1:d=0.25[a2];"
        "[a0][a1][a2]amix=inputs=3:duration=first:normalize=0,"
        "highpass=f=50,lowpass=f=400,volume=0.05[out]",
        "-map", "[out]", "-ar", "44100", "-ac", "2", str(bed),
    ], check=True)
    return bed


# Explicit per-cut punctuation, distinct from the ambient bed's continuous texture --
# channel-owner request: a swoosh on every AI-image change. A synthesized noise-burst
# version of this shipped first and sounded terrible on a real render (channel-owner
# feedback); this is a real sourced clip instead -- "Swoosh 014" by Universfield,
# royalty-free/no-attribution-required (Pixabay license), picked by the channel owner
# directly rather than another synthesis attempt. Placed at every real cut via
# _cut_offsets, same as the removed synthesized version, so it can't drift out of sync
# with the picture cut it marks.
SWOOSH_SFX_PATH = config.ASSETS_DIR / "sfx" / "swoosh_014.mp3"
# Mixed well under the narration ("a lot softer" -- explicit channel-owner feedback on
# the first attempt) rather than the old synthesized version's volume=2.5 boost.
SWOOSH_VOLUME = 0.12


def _build_swoosh_track(work_dir, cut_offsets, sfx_path=SWOOSH_SFX_PATH):
    if not cut_offsets:
        return None
    n = len(cut_offsets)
    split_labels = [f"s{i}" for i in range(n)]
    parts = [f"[0:a]asplit={n}[" + "][".join(split_labels) + "]"]
    for label, offset in zip(split_labels, cut_offsets):
        ms = max(int(round(offset * 1000)), 0)
        parts.append(f"[{label}]adelay={ms}|{ms}[{label}d]")
    mix_inputs = "".join(f"[{label}d]" for label in split_labels)
    # duration=longest, not first/shortest -- the first delayed copy is the shortest
    # stream (least delay padding); using it as the reference would truncate every
    # later swoosh right at the mix step.
    parts.append(f"{mix_inputs}amix=inputs={n}:duration=longest:normalize=0[out]")

    track = work_dir / "swoosh_track.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(sfx_path),
        "-filter_complex", ";".join(parts),
        "-map", "[out]", "-ar", "44100", "-ac", "2", str(track),
    ], check=True)
    return track


def assemble(script, narration_path, clips, music_dir, out_path, work_dir):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Trimmed BEFORE probing duration -- everything downstream (beat timing, caption
    # timing) should reflect the actual, post-trim audio length, not the original.
    narration_path = _trim_long_pauses(narration_path, work_dir)
    narration_duration = min(_probe_duration(narration_path), config.MAX_SHORT_SECONDS)
    durations = _beat_durations(script["beats"], narration_duration)

    # broll.py already generated one DISTINCT image per visual shot and tagged each
    # clip with which beat it belongs to (pipeline/shot_planning.py decided the shot
    # count there, from its own narration-duration estimate) -- group by beat_index and
    # lay out whatever images actually exist for beat i evenly across that beat's FINAL
    # duration (computed just above, from the actual trimmed narration). The two stages
    # deliberately don't need to agree on an exact shot count: assemble.py just adapts
    # to whatever broll.py produced, so a small drift between broll.py's duration
    # estimate and this one can never desync captions/timing from the video track.
    shots_by_beat = {}
    for clip in clips:
        shots_by_beat.setdefault(clip["beat_index"], []).append(clip)
    for beat_clips in shots_by_beat.values():
        beat_clips.sort(key=lambda c: c["shot_index"])

    # Each sub-shot clip is rendered TRANSITION_DURATION longer than it needs to be --
    # see TRANSITION_DURATION's comment -- so the crossfade below has real overlap
    # material to consume instead of eating into CLIP_DURATION_BUFFER's own
    # frame-rounding safety margin.
    scaled_paths = []
    clip_targets = []
    transition_durations = [0.0]  # index 0 unused -- the first shot has nothing to transition from
    for i, duration in enumerate(durations):
        role = script["beats"][i].get("beat_role")
        # 'ending' matches the hook's own zoom rate (not the subtle everyday rate) --
        # when the routine uses the sentence-loop technique (ROUTINE_INSTRUCTIONS.md),
        # matching motion across the loop point is part of what makes the cut back to
        # beat 0 read as continuous rather than a hard restart.
        zoom = "hook" if role in ("hook", "ending") else "subtle"

        beat_clips = shots_by_beat[i]
        sub_duration = duration / len(beat_clips)
        sub_target = sub_duration + CLIP_DURATION_BUFFER + TRANSITION_DURATION

        for s, clip in enumerate(beat_clips):
            scaled = work_dir / f"beat_{i:02d}_shot_{s:02d}_scaled.mp4"
            # Every clip is a distinct AI-generated image with no motion of its own
            # (see PAN_TARGETS' comment), so every sub-shot gets a real Ken Burns pan
            # on top of its own already-distinct content.
            pan_target = random.choice(PAN_TARGETS)
            _scale_clip(clip["path"], scaled, sub_target, zoom=zoom, pan_target=pan_target)
            scaled_paths.append(scaled)
            clip_targets.append(sub_target)

            if i == 0 and s == 0:
                continue  # the video's very first shot -- nothing to transition from
            if s == 0:
                # A real beat boundary -- carries the existing whip/hard-cut meaning.
                transition_durations.append(_transition_duration(script["beats"], i))
            else:
                # A within-beat sub-shot cut -- not a structural boundary, always a
                # fast hard cut so it reads as pace, not punctuation.
                transition_durations.append(HARD_CUT_DURATION)

    video_track = work_dir / "video_track.mp4"
    _build_video_track(scaled_paths, clip_targets, transition_durations, video_track)

    ass_path = work_dir / "captions.ass"
    _write_ass(script["beats"], durations, ass_path)

    music_path = _pick_music_track(music_dir, script.get("category"))

    mixed_audio = narration_path
    if music_path:
        mixed_audio = work_dir / "mixed_audio.mp3"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(narration_path), "-stream_loop", "-1", "-i", str(music_path),
            "-filter_complex",
            "[1:a]volume=0.12[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "[aout]", "-t", str(narration_duration), str(mixed_audio),
        ], check=True)

    # Ambient bed layers in regardless of whether a music track was found above --
    # it's a room-tone texture, not music (see _build_ambient_bed's comment), so it
    # doesn't compete with or substitute for MUSIC_ENABLED's file-based track system.
    ambient_bed = _build_ambient_bed(work_dir, narration_duration)
    bedded_audio = work_dir / "bedded_audio.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(mixed_audio), "-i", str(ambient_bed),
        "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]",
        "-map", "[aout]", "-t", str(narration_duration), str(bedded_audio),
    ], check=True)
    mixed_audio = bedded_audio

    # A swoosh on every real image-change cut (see _build_swoosh_track's comment) --
    # cut_offsets is the exact same math _build_video_track already used above for the
    # xfade chain, so the sound can't drift out of sync with the picture cut it marks.
    cut_offsets = _cut_offsets(clip_targets, transition_durations)
    swoosh_track = _build_swoosh_track(work_dir, cut_offsets)
    if swoosh_track:
        swooshed_audio = work_dir / "swooshed_audio.mp3"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(mixed_audio), "-i", str(swoosh_track),
            "-filter_complex",
            f"[1:a]volume={SWOOSH_VOLUME}[sw];[0:a][sw]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]",
            "-map", "[aout]", "-t", str(narration_duration), str(swooshed_audio),
        ], check=True)
        mixed_audio = swooshed_audio

    # No force_style override needed -- the .ass file's own [V4+ Styles] section
    # carries the base look, and per-word emphasis overrides live inline in the text.
    # No corner watermark as of 2026-08-21 -- removed per measured feedback on a real
    # published video ("costs attention and buys nothing" on a format this short);
    # reverses the earlier explicit "keep it in the corner" call, confirmed with the
    # channel owner before making the change. assets/branding/ mascot SVGs are unused
    # now but left in place rather than deleted, in case that decision gets revisited.
    escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_track), "-i", str(mixed_audio),
        "-filter_complex", f"[0:v]subtitles='{escaped_ass}'[outv]",
        "-map", "[outv]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        "-t", str(narration_duration), str(out_path),
    ], check=True)

    return str(out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--narration", required=True)
    parser.add_argument("--clips", required=True, help="JSON list of {path, source, beat_index, shot_index} shot entries (see broll.py), inline or a file path")
    parser.add_argument("--music-dir", default=str(config.ASSETS_DIR / "music"))
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    script_data = json.loads(Path(args.script).read_text(encoding="utf-8"))
    if args.clips.strip().startswith("["):
        clips = json.loads(args.clips)
    else:
        clips = json.loads(Path(args.clips).read_text(encoding="utf-8"))

    result = assemble(
        script_data, args.narration, clips, args.music_dir, args.out, args.work_dir,
    )
    print(result)
