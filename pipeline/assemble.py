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
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,16,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,2,60,60,120,1

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


def _scale_clip(src, dst, duration):
    target = duration + CLIP_DURATION_BUFFER
    clip_len = _probe_duration(src)
    loop_count = max(math.ceil(target / clip_len), 1) if clip_len > 0 else 1
    # fps filter forces a real, constant frame rate -- Pexels source clips come in
    # at whatever fps the original was shot at, and concatenating segments with
    # different/variable frame rates causes a stutter at each cut point even though
    # the audio/captions stay on schedule.
    vf = (
        f"fps={OUTPUT_FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT}"
    )
    subprocess.run([
        "ffmpeg", "-y", "-stream_loop", str(loop_count - 1), "-i", str(src),
        "-t", str(target), "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", str(dst),
    ], check=True)


def assemble(script, narration_path, clip_paths, music_dir, out_path, work_dir):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    narration_duration = min(_probe_duration(narration_path), config.MAX_SHORT_SECONDS)
    durations = _beat_durations(script["beats"], narration_duration)

    scaled_paths = []
    for i, (clip, dur) in enumerate(zip(clip_paths, durations)):
        scaled = work_dir / f"beat_{i:02d}_scaled.mp4"
        _scale_clip(clip, scaled, dur)
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

    music_tracks = list(Path(music_dir).glob("*.mp3"))
    music_path = random.choice(music_tracks) if music_tracks else None

    mixed_audio = narration_path
    if music_path:
        mixed_audio = work_dir / "mixed_audio.mp3"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(narration_path), "-stream_loop", "-1", "-i", str(music_path),
            "-filter_complex",
            "[1:a]volume=0.12[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "[aout]", "-t", str(narration_duration), str(mixed_audio),
        ], check=True)

    # No force_style override needed -- the .ass file's own [V4+ Styles] section
    # carries the base look, and per-word emphasis overrides live inline in the text.
    escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
    subtitles_filter = f"subtitles='{escaped_ass}'"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_track), "-i", str(mixed_audio),
        "-vf", subtitles_filter, "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
        "-t", str(narration_duration), str(out_path),
    ], check=True)

    return str(out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--narration", required=True)
    parser.add_argument("--clips", required=True, help="JSON list of clip paths, inline or a file path")
    parser.add_argument("--music-dir", default=str(config.ASSETS_DIR / "music"))
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    script_data = json.loads(Path(args.script).read_text(encoding="utf-8"))
    if args.clips.strip().startswith("["):
        clip_paths = json.loads(args.clips)
    else:
        clip_paths = json.loads(Path(args.clips).read_text(encoding="utf-8"))

    result = assemble(script_data, args.narration, clip_paths, args.music_dir, args.out, args.work_dir)
    print(result)
