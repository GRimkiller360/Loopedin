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


# TikTok-style dynamic captions: 1-2 words on screen at a time instead of the whole
# beat sentence at once -- the eye can't skim ahead of a caption that keeps changing,
# which is exactly this channel's own top lever (avg_view_pct/retention). 2 rather than
# 1 -- literal one-word-at-a-time reads choppy for short words at normal speaking pace;
# 2 is the common sweet spot in TikTok/CapCut-style caption tools. Drop to 1 for an
# even punchier feel.
WORDS_PER_CAPTION = 2


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
# dramatic punch (distinct treatment on the hook); every other beat gets a much
# subtler version -- static, unmoving stock footage reads as low-effort and gives the
# eye nothing to track, so keep constant gentle motion through the whole video, not
# just the opening.
HOOK_ZOOM_END = 1.15
SUBTLE_ZOOM_END = 1.06

# Applied to every clip in every video -- a consistent color grade + vignette is a
# visual signature: raw unfiltered stock footage looks like raw unfiltered stock
# footage no matter whose channel it's on, but a consistent, distinct grade is
# something a viewer can start to recognize as *this channel's* look across videos,
# unlike per-video variation which reads as generic every time. Mild boost to
# contrast/saturation plus a soft vignette pulling focus toward center -- deliberately
# conservative, not stylized enough to fight the footage or look artificial.
SIGNATURE_LOOK_FILTER = "eq=contrast=1.08:saturation=1.18:brightness=0.01,vignette=PI/4"

# Real Ken Burns pan targets (fractional focal point the crop window drifts toward as
# it zooms) -- used for AI-generated image clips specifically (see ai_broll.py), not
# Pixabay footage. A static AI image has no motion of its own, so pairing zoom with a
# genuine directional drift (not just zooming on the dead center) is what keeps it from
# reading as "a photo with a zoom filter" -- real stock clips already have their own
# camera/subject motion, so they keep the plain center-zoom below unchanged.
PAN_TARGETS = [(0.15, 0.25), (0.85, 0.25), (0.15, 0.75), (0.85, 0.75), (0.5, 0.15), (0.5, 0.85)]


def _scale_clip(src, dst, target, zoom=None, pan_target=None):
    clip_len = _probe_duration(src)
    loop_count = max(math.ceil(target / clip_len), 1) if clip_len > 0 else 1
    # fps filter forces a real, constant frame rate -- Pexels source clips come in
    # at whatever fps the original was shot at, and concatenating segments with
    # different/variable frame rates causes a stutter at each cut point even though
    # the audio/captions stay on schedule.
    vf = (
        f"fps={OUTPUT_FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},{SIGNATURE_LOOK_FILTER}"
    )
    zoom_end = {"hook": HOOK_ZOOM_END, "subtle": SUBTLE_ZOOM_END}.get(zoom)
    if zoom_end:
        total_frames = max(int(round(target * OUTPUT_FPS)), 1)
        zoom_step = (zoom_end - 1.0) / total_frames
        if pan_target:
            fx, fy = pan_target
            # Drift the crop window's center from frame-center toward (fx,fy) as zoom
            # progresses from 1.0 to zoom_end, instead of staying centered -- e.g. at
            # x: cx = 0.5 + progress*(fx-0.5), then x = iw*cx - (iw/zoom)/2 (the crop
            # window's half-width at the current zoom level).
            # Clamped to the valid crop range [0, iw-iw/zoom] -- at this pipeline's
            # modest zoom_end values (1.06-1.15), the crop window only has a few
            # percent of the frame to actually move within, so an uncomputed pan
            # target like 0.85 would put x_expr's raw value far outside the frame
            # (verified: at zoom_end=1.15, fx=0.85 needs x_frac=0.415 but the valid
            # range tops out at 0.130). Clamping means the pan drifts as far toward
            # the target as the zoom level actually allows, rather than producing an
            # invalid crop -- still a real, visible directional drift, just bounded.
            x_expr = f"max(0,min(iw-iw/zoom,(iw*(0.5+((zoom-1)/({zoom_end}-1))*({fx}-0.5)))-(iw/zoom/2)))"
            y_expr = f"max(0,min(ih-ih/zoom,(ih*(0.5+((zoom-1)/({zoom_end}-1))*({fy}-0.5)))-(ih/zoom/2)))"
        else:
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        vf += (
            f",zoompan=z='min(zoom+{zoom_step},{zoom_end})':d=1:"
            f"x='{x_expr}':y='{y_expr}':s={WIDTH}x{HEIGHT}:fps={OUTPUT_FPS}"
        )
    subprocess.run([
        "ffmpeg", "-y", "-stream_loop", str(loop_count - 1), "-i", str(src),
        "-t", str(target), "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", str(dst),
    ], check=True)


def _build_video_track(scaled_paths, clip_targets, beats, out_path):
    """Crossfades consecutive beat clips into video_track.mp4 instead of hard-cutting
    between them -- chains ffmpeg's xfade filter across all N clips in one pass, rather
    than the old concat-demuxer approach (concat can't overlap two streams at once, only
    play them back to back, so a real crossfade needs every clip as a live filter-graph
    input instead). Each transition is either a short whip-blur or a near-instant hard
    cut depending on beat_role -- see WHIP_ROLES/_transition_duration."""
    n = len(scaled_paths)
    inputs = []
    for p in scaled_paths:
        inputs += ["-i", str(p)]

    if n == 1:
        filter_complex, final_label = None, "0:v"
    else:
        parts = []
        cumulative = clip_targets[0]
        prev_label = "0:v"
        for i in range(1, n):
            duration = _transition_duration(beats, i)
            transition = "hblur" if duration == WHIP_TRANSITION_DURATION else "fade"
            offset = max(cumulative - duration, 0.0)
            out_label = f"v{i}"
            parts.append(
                f"[{prev_label}][{i}:v]xfade=transition={transition}:"
                f"duration={duration}:offset={offset:.3f}[{out_label}]"
            )
            cumulative = cumulative + clip_targets[i] - duration
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


# Channel mascot (assets/branding/) -- added 2026-08-21 per explicit channel-owner
# instruction, watermark-only now (the intro bumper was removed the same day after a
# real test run -- see state/ruleset_changelog.json). One expression is picked per
# video (not fixed) so the channel doesn't show the exact same static image on every
# upload -- same "structural variety matters" reasoning ROUTINE_INSTRUCTIONS.md
# already applies to hooks/CTAs.
MASCOT_VARIANTS = ("mascot_surprised.svg", "mascot_winking.svg", "mascot_thinking.svg")
WATERMARK_MASCOT_PX = 150
WATERMARK_MARGIN = 40


def _render_mascot_png(svg_path, out_png, size_px):
    subprocess.run([
        "rsvg-convert", "-w", str(size_px), "-h", str(size_px),
        str(svg_path), "-o", str(out_png),
    ], check=True)


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


# Short synthesized (not sourced -- no copyright question) two-tone attention cue,
# mixed under the very start of beat 0 as an audio pattern-interrupt to go with the
# visual zoom-punch -- alongside a sudden motion/zoom change, a brief distinct sound
# at hook time is a documented technique for cutting through autoplay-muted scrolling.
def _add_hook_sound(audio_path, narration_duration, work_dir):
    hook_sound = work_dir / "hook_sound.mp3"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=700:duration=0.06",
        "-f", "lavfi", "-i", "sine=frequency=1400:duration=0.09",
        "-filter_complex",
        "[0:a][1:a]concat=n=2:v=0:a=1,afade=t=in:st=0:d=0.01,afade=t=out:st=0.12:d=0.03,volume=0.4[out]",
        "-map", "[out]", str(hook_sound),
    ], check=True)

    hooked_audio = work_dir / "hooked_audio.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(audio_path), "-i", str(hook_sound),
        # normalize=0 -- amix's default auto-normalization would quietly halve the
        # narration's volume for the *entire* clip just because of a 150ms sound
        # effect layered at the start; the hook tone is already pre-scaled quiet
        # enough (volume=0.4 above) not to need it.
        "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]",
        "-map", "[aout]", "-t", str(narration_duration), str(hooked_audio),
    ], check=True)
    return hooked_audio


def assemble(script, narration_path, clips, music_dir, out_path, work_dir, mascot_dir=None):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    mascot_dir = Path(mascot_dir) if mascot_dir else config.ASSETS_DIR / "branding"

    narration_duration = min(_probe_duration(narration_path), config.MAX_SHORT_SECONDS)
    durations = _beat_durations(script["beats"], narration_duration)

    # Each clip is rendered TRANSITION_DURATION longer than it needs to be for its own
    # beat -- see TRANSITION_DURATION's comment -- so the crossfade below has real
    # overlap material to consume instead of eating into CLIP_DURATION_BUFFER's own
    # frame-rounding safety margin.
    clip_targets = [d + CLIP_DURATION_BUFFER + TRANSITION_DURATION for d in durations]
    scaled_paths = []
    for i, (clip, target) in enumerate(zip(clips, clip_targets)):
        scaled = work_dir / f"beat_{i:02d}_scaled.mp4"
        role = script["beats"][i].get("beat_role")
        # 'ending' matches the hook's own zoom rate (not the subtle everyday rate) --
        # when the routine uses the sentence-loop technique (ROUTINE_INSTRUCTIONS.md),
        # matching motion across the loop point is part of what makes the cut back to
        # beat 0 read as continuous rather than a hard restart.
        zoom = "hook" if role in ("hook", "ending") else "subtle"
        # Real Ken Burns pan on AI-generated image clips (no motion of their own -- see
        # PAN_TARGETS' comment) and on joke/hedge beats specifically, so the motion
        # itself varies across the video instead of every beat zooming dead-center the
        # same way -- a joke or a tonal shift reads as a distinct beat partly because
        # the camera moves differently through it, not just because of the words.
        wants_pan = clip.get("source") == "ai_image" or role in ("joke", "hedge")
        pan_target = random.choice(PAN_TARGETS) if wants_pan else None
        _scale_clip(clip["path"], scaled, target, zoom=zoom, pan_target=pan_target)
        scaled_paths.append(scaled)

    video_track = work_dir / "video_track.mp4"
    _build_video_track(scaled_paths, clip_targets, script["beats"], video_track)

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

    mixed_audio = _add_hook_sound(mixed_audio, narration_duration, work_dir)

    mascot_variant = random.choice(MASCOT_VARIANTS)
    mascot_svg = mascot_dir / mascot_variant
    watermark_png = work_dir / "mascot_watermark.png"
    _render_mascot_png(mascot_svg, watermark_png, WATERMARK_MASCOT_PX)

    # No force_style override needed -- the .ass file's own [V4+ Styles] section
    # carries the base look, and per-word emphasis overrides live inline in the text.
    # Watermark is composited in the same filter graph as the caption burn-in (rather
    # than a separate pass) so there's only one video re-encode for this step.
    escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_track), "-i", str(mixed_audio), "-loop", "1", "-i", str(watermark_png),
        "-filter_complex",
        f"[0:v]subtitles='{escaped_ass}'[capped];"
        f"[capped][2:v]overlay=W-w-{WATERMARK_MARGIN}:H-h-{WATERMARK_MARGIN}:shortest=1[outv]",
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
    parser.add_argument("--clips", required=True, help="JSON list of {path, source} clip entries, inline or a file path")
    parser.add_argument("--music-dir", default=str(config.ASSETS_DIR / "music"))
    parser.add_argument("--mascot-dir", default=str(config.ASSETS_DIR / "branding"))
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
        mascot_dir=args.mascot_dir,
    )
    print(result)
