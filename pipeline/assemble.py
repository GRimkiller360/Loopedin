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
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import broll, config

WIDTH, HEIGHT = 1080, 1920

# Gold/highlight color for **word**-marked emphasis, ASS inline &HBBGGRR& order
# (RGB 255,215,0 -> BBGGRR 00D7FF). Base caption style is already bold, so emphasis
# is color + a slight size bump rather than bold-on-bold, which wouldn't read as
# distinct.
EMPHASIS_OVERRIDE = r"{\c&H00D7FF&\fscx115\fscy115}"
EMPHASIS_RESET = r"{\r}"


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


def _beat_text_to_ass(text):
    """Converts **word** emphasis markers (see script_schema.py) into ASS inline
    override tags; everything else is escaped plain text."""
    parts = re.split(r"\*\*(.+?)\*\*", text)
    out = []
    for i, part in enumerate(parts):
        escaped = _ass_escape(part)
        out.append(f"{EMPHASIS_OVERRIDE}{escaped}{EMPHASIS_RESET}" if i % 2 == 1 else escaped)
    return "".join(out)


ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,90,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,6,0,2,60,60,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _write_ass(beats, durations, out_path):
    lines = [ASS_HEADER]
    t = 0.0
    for beat, dur in zip(beats, durations):
        start, end = t, t + dur
        ass_text = _beat_text_to_ass(beat["text"])
        lines.append(f"Dialogue: 0,{_format_ass_timestamp(start)},{_format_ass_timestamp(end)},Default,,0,0,0,,{ass_text}")
        t = end
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")


CLIP_DURATION_BUFFER = 0.35  # each segment overshoots slightly so frame-rounding
                              # across N clips can never leave the concatenated
                              # video shorter than the narration audio


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


def _scale_clip(src, dst, duration, zoom=None):
    target = duration + CLIP_DURATION_BUFFER
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
        vf += (
            f",zoompan=z='min(zoom+{zoom_step},{zoom_end})':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={WIDTH}x{HEIGHT}:fps={OUTPUT_FPS}"
        )
    subprocess.run([
        "ffmpeg", "-y", "-stream_loop", str(loop_count - 1), "-i", str(src),
        "-t", str(target), "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", str(dst),
    ], check=True)


# Category -> preferred music mood tags. assets/music/tags.json (user-maintained) maps
# filename -> list of mood tags; a track matching any preferred tag for the script's
# category is preferred over a fully random pick. Inert and harmless until tagged
# files actually exist -- falls straight back to random, same as before this existed.
CATEGORY_MOODS = {
    "science facts": ("curious", "upbeat"),
    "space": ("awe", "calm", "epic"),
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


# --- Typographic render path (RENDER_STYLE=typographic) -----------------------------
#
# Full-bleed flat-color frames with large centered text instead of stock footage --
# built from pre-rendered PNG frames (Pillow) composited via ffmpeg's overlay, not
# ffmpeg text/expression filters. This isn't a style choice: this build's drawbox and
# crop filters both lack an `eval` option (see the progress-bar history above), and
# drawtext has its own escaping/font-availability headaches on a headless runner --
# Pillow gives real font control and sidesteps that whole class of failure entirely.

# Keyed off category so the channel reads as visually consistent, not random --
# deliberately flat colors, no gradients. Anything not in this dict (a category
# dropped from CATEGORIES, or None) falls back to DEFAULT_BG_COLOR rather than
# crashing -- a render should never fail just because of an unmapped category.
CATEGORY_PALETTE = {
    "science facts": (14, 61, 46),    # deep green
    "space": (13, 27, 58),            # deep navy
    "history": (58, 26, 20),          # deep maroon/sepia
}
DEFAULT_BG_COLOR = (26, 26, 26)
ACCENT_COLOR = (255, 215, 0)          # gold -- matches the existing progress bar color
TEXT_COLOR = (255, 255, 255)
SERIES_LABEL_COLOR = (255, 255, 255)

BASE_FONT_SIZE = 84
EMPHASIS_FONT_SIZE = 118
SERIES_LABEL_FONT_SIZE = 28
TEXT_MAX_WIDTH_FRAC = 0.84
LINE_SPACING = 18
WORD_SPACING = 22

# Checked in order -- DejaVu Sans Bold is what the production Ubuntu runner actually
# has (confirmed against setup_env.sh's apt install, which pulls it in as an ffmpeg/
# fontconfig dependency); the others are local-dev fallbacks so this can be tested
# off the production box too. PIL's built-in bitmap font is the last resort -- ugly,
# but never crashes a render just because no TTF was found.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _load_font(size):
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _parse_emphasis_runs(text):
    """**word** markers (see script_schema.py) -> [(word, is_emphasis), ...], split on
    whitespace within each run so word-wrapping can work word-by-word regardless of
    which side of a ** boundary a line break falls on."""
    runs = []
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        is_emphasis = i % 2 == 1
        for word in part.split():
            runs.append((word, is_emphasis))
    return runs


def _wrap_lines(runs, draw, max_width):
    lines, current, current_width = [], [], 0
    for word, is_emphasis in runs:
        font = _load_font(EMPHASIS_FONT_SIZE if is_emphasis else BASE_FONT_SIZE)
        bbox = draw.textbbox((0, 0), word, font=font)
        word_width = bbox[2] - bbox[0]
        added_width = word_width + (WORD_SPACING if current else 0)
        if current and current_width + added_width > max_width:
            lines.append(current)
            current, current_width = [], 0
            added_width = word_width
        current.append((word, is_emphasis, font, word_width))
        current_width += added_width
    if current:
        lines.append(current)
    return lines


def _draw_wrapped_text(draw, text, width, height, series_label=None):
    runs = _parse_emphasis_runs(text)
    max_width = int(width * TEXT_MAX_WIDTH_FRAC)
    lines = _wrap_lines(runs, draw, max_width)

    line_heights = [max(f.size for _, _, f, _ in line) + LINE_SPACING for line in lines]
    total_height = sum(line_heights) - LINE_SPACING if line_heights else 0

    y = (height - total_height) // 2
    for line, line_height in zip(lines, line_heights):
        line_width = sum(w for *_, w in line) + WORD_SPACING * max(len(line) - 1, 0)
        x = (width - line_width) // 2
        for word, is_emphasis, font, word_width in line:
            color = ACCENT_COLOR if is_emphasis else TEXT_COLOR
            draw.text((x, y), word, font=font, fill=color)
            x += word_width + WORD_SPACING
        y += line_height

    if series_label:
        label_font = _load_font(SERIES_LABEL_FONT_SIZE)
        bbox = draw.textbbox((0, 0), series_label, font=label_font)
        label_w, label_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        margin = 48
        draw.text(
            (width - label_w - margin, height - label_h - margin),
            series_label, font=label_font, fill=SERIES_LABEL_COLOR,
        )


def _render_text_frame(text, bg_color, out_path, series_label=None, width=WIDTH, height=HEIGHT):
    """Renders one full-bleed frame: flat background, large centered word-wrapped
    text with **emphasis** words in the accent color/size, series_label small in a
    bottom corner. Used for both beat frames and the cold-open frame."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    _draw_wrapped_text(draw, text, width, height, series_label=series_label)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


def _render_text_overlay_png(text, out_path, series_label=None, width=WIDTH, height=HEIGHT):
    """Same text layout as _render_text_frame, but on a transparent RGBA canvas --
    for compositing over an archival still (see fetch_archival_still) rather than a
    flat color background."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _draw_wrapped_text(draw, text, width, height, series_label=series_label)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


ARCHIVAL_DIM_OPACITY = 0.45  # still shown at this opacity, blended toward the
                              # category color behind it -- "composited under the
                              # text layer at reduced opacity," not full-frame footage
ARCHIVAL_ZOOM_END = 1.12     # slow push-in across the *whole* video (Ken Burns), a
                              # gentler ramp than the per-beat hook zoom on stock clips


def _build_archival_background(image_path, narration_duration, bg_color, work_dir):
    """Continuous slow push-in over the single archival still for the full narration
    duration -- one image, one continuous camera move, the way a real Ken Burns
    documentary segment works, rather than cutting between different images per beat
    (which would need one archival fetch per beat instead of one per video). Reuses
    zoompan exactly as proven in _scale_clip's hook-zoom -- has its own per-frame
    state on this ffmpeg build, unlike drawbox/crop's eval option."""
    work_dir = Path(work_dir)
    from PIL import Image

    dimmed = work_dir / "archival_dimmed.png"
    img = Image.open(image_path).convert("RGB")
    overlay = Image.new("RGB", img.size, bg_color)
    Image.blend(img, overlay, 1 - ARCHIVAL_DIM_OPACITY).save(dimmed)

    total_frames = max(int(round(narration_duration * OUTPUT_FPS)), 1)
    zoom_step = (ARCHIVAL_ZOOM_END - 1.0) / total_frames
    vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
        f"zoompan=z='min(zoom+{zoom_step},{ARCHIVAL_ZOOM_END})':d=1:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={WIDTH}x{HEIGHT}:fps={OUTPUT_FPS}"
    )
    background = work_dir / "archival_background.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(dimmed), "-t", str(narration_duration),
        "-vf", vf, "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", str(background),
    ], check=True)
    return background


def _composite_beat_over_archival(background_path, start, dur, text_png_path, out_path):
    # Trim this beat's slice of the continuously-zooming background, then overlay its
    # transparent text PNG -- fixed-position overlay, no timeline/enable expression,
    # same "pre-render the exact segment, don't rely on ffmpeg evaluating a
    # time-varying condition" discipline as the rest of this file.
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(start), "-t", str(dur), "-i", str(background_path),
        "-loop", "1", "-t", str(dur), "-i", str(text_png_path),
        "-filter_complex", "[0:v][1:v]overlay=x=0:y=0",
        "-r", str(OUTPUT_FPS), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
        str(out_path),
    ], check=True)


def _frame_to_clip(frame_path, duration, out_path):
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(frame_path), "-t", str(duration),
        "-vf", f"scale={WIDTH}:{HEIGHT}", "-r", str(OUTPUT_FPS), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", str(out_path),
    ], check=True)


# --- Shared helpers (used by both the stock and typographic render paths) -----------

def _compute_beat_timings(beats, durations, narration_duration):
    beat_timings = []
    cursor = 0.0
    for beat, dur in zip(beats, durations):
        beat_timings.append({
            "start_frac": round(cursor / narration_duration, 4),
            "end_frac": round(min(cursor + dur, narration_duration) / narration_duration, 4),
            "text": beat["text"],
        })
        cursor += dur
    return beat_timings


def _mix_narration_with_music(narration_path, music_path, narration_duration, work_dir):
    if not music_path:
        return narration_path
    mixed_audio = Path(work_dir) / "mixed_audio.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(narration_path), "-stream_loop", "-1", "-i", str(music_path),
        "-filter_complex",
        "[1:a]volume=0.12[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "[aout]", "-t", str(narration_duration), str(mixed_audio),
    ], check=True)
    return mixed_audio


def _build_progress_bar_source(narration_duration, work_dir):
    # Take 5 (plus a granularity tweak) -- see the extensive history in the stock
    # assemble() path below for why this generates zero ffmpeg expressions at all.
    # Shared by both render paths so the bar behaves identically either way.
    work_dir = Path(work_dir)
    TARGET_STEP_SECONDS = 0.15
    bar_steps = max(10, min(round(narration_duration / TARGET_STEP_SECONDS), 250))
    step_dur = narration_duration / bar_steps
    bar_step_paths = []
    for i in range(bar_steps):
        step_width = max(round(WIDTH * (i + 1) / bar_steps), 1)
        step_path = work_dir / f"progress_bar_step_{i:02d}.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=black@0.35:size={WIDTH}x10:duration={step_dur}:rate={OUTPUT_FPS}",
            "-vf", f"drawbox=x=0:y=0:w={step_width}:h=10:color=0xFFD700@0.9:thickness=fill",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(step_path),
        ], check=True)
        bar_step_paths.append(step_path)

    bar_concat_list = work_dir / "progress_bar_concat.txt"
    bar_concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in bar_step_paths))
    bar_source = work_dir / "progress_bar_source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(bar_concat_list),
        "-c:v", "libx264", "-preset", "veryfast", "-r", str(OUTPUT_FPS), "-pix_fmt", "yuv420p",
        str(bar_source),
    ], check=True)
    return bar_source


COLD_OPEN_SECONDS = 1.0


def _add_cold_open(main_video_path, contradicted_belief, bg_color, out_path, work_dir):
    """Prepends a 1.0s silent, motionless frame stating the contradicted belief --
    every other video in this feed opens with motion; a full second of stillness is
    itself the pattern-break. Applied to both render paths so the stock-vs-typographic
    A/B (see ROUTINE_INSTRUCTIONS.md/repo var RENDER_STYLE) isn't confounded by this
    being a second, separate variable."""
    work_dir = Path(work_dir)
    frame_path = work_dir / "cold_open_frame.png"
    _render_text_frame(contradicted_belief, bg_color, frame_path)

    cold_open_video = work_dir / "cold_open.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(frame_path),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(COLD_OPEN_SECONDS),
        "-vf", f"scale={WIDTH}:{HEIGHT}", "-r", str(OUTPUT_FPS), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-shortest",
        str(cold_open_video),
    ], check=True)

    # The concat FILTER, not the concat demuxer -- the demuxer assumes its inputs are
    # already stream-compatible (same sample rate/frame size/timestamps) and can
    # produce audible crackling when that's not quite true, which it isn't here:
    # cold_open_video's audio comes from anullsrc at a fixed 44100Hz, while
    # main_video_path's narration audio (Google TTS) and the hook-sound mix it went
    # through upstream aren't guaranteed to land on the same rate. The filter properly
    # decodes both inputs and re-encodes a single coherent output instead of just
    # splicing container-level packets together.
    subprocess.run([
        "ffmpeg", "-y", "-i", str(cold_open_video), "-i", str(main_video_path),
        "-filter_complex", "[0:v:0][0:a:0][1:v:0][1:a:0]concat=n=2:v=1:a=1[vout][aout]",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-pix_fmt", "yuv420p",
        str(out_path),
    ], check=True)
    return str(out_path)


def assemble_typographic(script, narration_path, music_dir, out_path, work_dir):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    narration_duration = min(_probe_duration(narration_path), config.MAX_SHORT_SECONDS)
    durations = _beat_durations(script["beats"], narration_duration)
    beat_timings = _compute_beat_timings(script["beats"], durations, narration_duration)

    bg_color = CATEGORY_PALETTE.get(script.get("category"), DEFAULT_BG_COLOR)
    series_label = script.get("series_label")
    category = script.get("category")

    # Real archival photographs are the actual visual differentiator this content has
    # over generic stock -- try for history/space, one still per video (not per beat,
    # so a continuous Ken Burns push-in works and only one API round-trip is needed).
    # Any failure anywhere in this chain (no compliant result, download error, dead
    # API) must fall back to the flat-color path silently, never break the render.
    archival_provenance = None
    archival_background = None
    if category in ("history", "space"):
        still_path = work_dir / "archival_still.jpg"
        try:
            archival_provenance = broll.fetch_archival_still(script.get("topic", ""), category, still_path)
        except Exception:
            archival_provenance = None
        if archival_provenance:
            try:
                archival_background = _build_archival_background(still_path, narration_duration, bg_color, work_dir)
            except Exception:
                archival_background = None
                archival_provenance = None

    beat_clip_paths = []
    cursor = 0.0
    for i, (beat, dur) in enumerate(zip(script["beats"], durations)):
        clip_path = work_dir / f"beat_{i:02d}.mp4"
        if archival_background:
            text_png = work_dir / f"beat_{i:02d}_text.png"
            _render_text_overlay_png(beat["text"], text_png, series_label=series_label)
            _composite_beat_over_archival(archival_background, cursor, dur, text_png, clip_path)
        else:
            frame_path = work_dir / f"beat_{i:02d}_frame.png"
            _render_text_frame(beat["text"], bg_color, frame_path, series_label=series_label)
            _frame_to_clip(frame_path, dur, clip_path)
        beat_clip_paths.append(clip_path)
        cursor += dur

    concat_list = work_dir / "concat.txt"
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in beat_clip_paths))
    video_track = work_dir / "video_track.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c:v", "libx264", "-preset", "veryfast", "-r", str(OUTPUT_FPS), "-pix_fmt", "yuv420p",
        str(video_track),
    ], check=True)

    music_path = _pick_music_track(music_dir, script.get("category"))
    mixed_audio = _mix_narration_with_music(narration_path, music_path, narration_duration, work_dir)
    mixed_audio = _add_hook_sound(mixed_audio, narration_duration, work_dir)

    bar_source = _build_progress_bar_source(narration_duration, work_dir)

    main_composite = work_dir / "main_composite.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_track), "-i", str(mixed_audio), "-i", str(bar_source),
        "-filter_complex", "[0:v][2:v]overlay=x=0:y=0:shortest=0[vout]",
        "-map", "[vout]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
        "-t", str(narration_duration), str(main_composite),
    ], check=True)

    _add_cold_open(main_composite, script.get("contradicted_belief", ""), bg_color, out_path, work_dir)

    return str(out_path), beat_timings, archival_provenance


def assemble(script, narration_path, beats_clips, music_dir, out_path, work_dir):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    narration_duration = min(_probe_duration(narration_path), config.MAX_SHORT_SECONDS)
    durations = _beat_durations(script["beats"], narration_duration)
    # Persisted so analytics_feedback.py can later map a YouTube Analytics
    # retention-curve drop-off point (also a 0-1 fraction of video length) back to
    # which specific beat it lands in.
    beat_timings = _compute_beat_timings(script["beats"], durations, narration_duration)

    # Each beat may now have multiple sub-clips (see broll.py's MAX_WORDS_PER_CLIP) --
    # split that beat's duration evenly across however many it actually got, so a real
    # cut happens partway through a long beat instead of one clip holding the whole
    # time. zoom treatment stays per-beat (hook vs subtle), applied to every sub-clip
    # within that beat -- _scale_clip's zoom ramps relative to whatever duration it's
    # given, so a shorter sub-duration still ramps correctly across just that sub-clip.
    scaled_paths = []
    for i, (sub_clips, dur) in enumerate(zip(beats_clips, durations)):
        sub_dur = dur / len(sub_clips)
        zoom = "hook" if i == 0 else "subtle"
        for j, clip in enumerate(sub_clips):
            scaled = work_dir / f"beat_{i:02d}_{j:02d}_scaled.mp4"
            _scale_clip(clip, scaled, sub_dur, zoom=zoom)
            scaled_paths.append(scaled)

    concat_list = work_dir / "concat.txt"
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in scaled_paths))
    video_track = work_dir / "video_track.mp4"
    # Re-encode rather than stream-copy: these segments were encoded independently
    # (separate ffmpeg invocations), and copy-concatenating them can produce subtly
    # misaligned keyframes/timestamps -- symptom seen: video freezes on the last
    # frame while audio keeps playing, because the copied track ends up shorter
    # than its nominal duration.
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c:v", "libx264", "-preset", "veryfast", "-r", str(OUTPUT_FPS), "-pix_fmt", "yuv420p",
        str(video_track),
    ], check=True)

    ass_path = work_dir / "captions.ass"
    _write_ass(script["beats"], durations, ass_path)

    music_path = _pick_music_track(music_dir, script.get("category"))
    mixed_audio = _mix_narration_with_music(narration_path, music_path, narration_duration, work_dir)
    mixed_audio = _add_hook_sound(mixed_audio, narration_duration, work_dir)

    # No force_style override needed -- the .ass file's own [V4+ Styles] section
    # carries the base look, and per-word emphasis overrides live inline in the text.
    escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
    subtitles_filter = f"subtitles='{escaped_ass}'"

    # Progress bar, take 5 (and a size tweak after confirming take 5 actually
    # animates in production, just too coarsely). Four prior attempts all relied on
    # ffmpeg evaluating a time-based expression (drawbox's w, then crop's w) -- neither
    # filter even has an `eval` option on this ffmpeg build ("Option not found" both
    # times), and without it the first attempt's expression rendered as a solid
    # full-width bar. See _build_progress_bar_source (shared with the typographic
    # path) for the zero-expression fix.
    bar_source = _build_progress_bar_source(narration_duration, work_dir)

    # bar_source already has its own black@0.35 background baked into every step, so
    # this is just a plain fixed-position overlay -- no drawbox needed here at all.
    main_composite = work_dir / "main_composite.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_track), "-i", str(mixed_audio), "-i", str(bar_source),
        "-filter_complex",
        f"[0:v][2:v]overlay=x=0:y=0:shortest=0[withbar];"
        f"[withbar]{subtitles_filter}[vout]",
        "-map", "[vout]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
        "-t", str(narration_duration), str(main_composite),
    ], check=True)

    bg_color = CATEGORY_PALETTE.get(script.get("category"), DEFAULT_BG_COLOR)
    _add_cold_open(main_composite, script.get("contradicted_belief", ""), bg_color, out_path, work_dir)

    return str(out_path), beat_timings, None  # archival stills only apply to the typographic path


if __name__ == "__main__":
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--narration", required=True)
    parser.add_argument("--clips", default=None,
                         help='JSON {"beats": [[clip,...],...]}, inline or a file path -- '
                              "required for --render-style stock, ignored for typographic")
    parser.add_argument("--music-dir", default=str(config.ASSETS_DIR / "music"))
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--render-style", default=None, choices=["stock", "typographic"],
                         help="defaults to the RENDER_STYLE env var, then 'stock'")
    args = parser.parse_args()

    render_style = args.render_style or os.environ.get("RENDER_STYLE", "stock")
    script_data = json.loads(Path(args.script).read_text(encoding="utf-8"))

    if render_style == "typographic":
        out_path, beat_timings, archival_provenance = assemble_typographic(
            script_data, args.narration, args.music_dir, args.out, args.work_dir
        )
    else:
        if not args.clips:
            raise SystemExit("--clips is required for --render-style stock")
        if args.clips.strip().startswith("{"):
            clips_data = json.loads(args.clips)
        else:
            clips_data = json.loads(Path(args.clips).read_text(encoding="utf-8"))
        beats_clips = clips_data["beats"]
        out_path, beat_timings, archival_provenance = assemble(
            script_data, args.narration, beats_clips, args.music_dir, args.out, args.work_dir
        )

    timings_path = Path(args.work_dir) / "beat_timings.json"
    timings_path.write_text(json.dumps(beat_timings, indent=2), encoding="utf-8")
    provenance_path = Path(args.work_dir) / "archival_provenance.json"
    provenance_path.write_text(json.dumps(archival_provenance, indent=2), encoding="utf-8")
    print(json.dumps({"out_path": out_path, "beat_timings_path": str(timings_path)}))
