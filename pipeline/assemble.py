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


def _probe_duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(out.strip())


def _beat_durations(beats, total_duration):
    weights = [max(len(b["text"]), 1) for b in beats]
    total_weight = sum(weights)
    return [total_duration * w / total_weight for w in weights]


def _format_srt_timestamp(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(beats, durations, out_path):
    lines = []
    t = 0.0
    for i, (beat, dur) in enumerate(zip(beats, durations), start=1):
        start, end = t, t + dur
        lines += [str(i), f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}", beat["text"], ""]
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

    srt_path = work_dir / "captions.srt"
    _write_srt(script["beats"], durations, srt_path)

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

    escaped_srt = str(srt_path).replace("\\", "/").replace(":", "\\:")
    subtitles_filter = (
        f"subtitles='{escaped_srt}':force_style="
        "'FontName=Arial,FontSize=16,Bold=1,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,"
        "BorderStyle=1,Outline=2,Alignment=2,MarginV=120'"
    )
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
