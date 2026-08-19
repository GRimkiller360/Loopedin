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
from pipeline import config

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
    "psychology": ("curious", "calm", "mysterious"),
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


def assemble(script, narration_path, beats_clips, music_dir, out_path, work_dir):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    narration_duration = min(_probe_duration(narration_path), config.MAX_SHORT_SECONDS)
    durations = _beat_durations(script["beats"], narration_duration)

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

    # No force_style override needed -- the .ass file's own [V4+ Styles] section
    # carries the base look, and per-word emphasis overrides live inline in the text.
    escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
    subtitles_filter = f"subtitles='{escaped_ass}'"
    # Progress bar: a thin strip at the very top edge, out of the way of captions
    # (which live near the bottom). Dark background track shows total distance; the
    # gold foreground bar (same color as caption emphasis, for a consistent look)
    # fills left-to-right over the real narration duration. This is a completion-
    # anxiety device, not a retention-through-interest one -- distinct from
    # everything else in this pipeline, which is about making people *want* to keep
    # watching rather than making the remaining distance visible.
    # eval=frame is required -- drawbox only evaluates x/y/w/h expressions once at
    # filter init by default, not per-frame, so a t-dependent width without this
    # renders as a single static bar for the whole video instead of actually filling
    # (confirmed in production: the bar showed but never progressed).
    progress_bar_filter = (
        f"drawbox=x=0:y=0:w=iw:h=10:color=black@0.35:t=fill,"
        f"drawbox=x=0:y=0:w='iw*min(t/{narration_duration},1)':h=10:color=0xFFD700@0.9:t=fill:eval=frame"
    )
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_track), "-i", str(mixed_audio),
        "-vf", f"{progress_bar_filter},{subtitles_filter}", "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
        "-t", str(narration_duration), str(out_path),
    ], check=True)

    return str(out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--narration", required=True)
    parser.add_argument("--clips", required=True, help='JSON {"beats": [[clip,...],...]}, inline or a file path')
    parser.add_argument("--music-dir", default=str(config.ASSETS_DIR / "music"))
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    script_data = json.loads(Path(args.script).read_text(encoding="utf-8"))
    if args.clips.strip().startswith("{"):
        clips_data = json.loads(args.clips)
    else:
        clips_data = json.loads(Path(args.clips).read_text(encoding="utf-8"))
    beats_clips = clips_data["beats"]

    result = assemble(script_data, args.narration, beats_clips, args.music_dir, args.out, args.work_dir)
    print(result)
