"""Local-only preview: a "ranked top-5" animal shorts format, no narration, just a
synthesized soundtrack. NOT part of the automated production pipeline -- no
GitHub Actions workflow calls this, and it never uploads anywhere. It writes a
local mp4 for the channel owner to watch and judge before deciding whether this
format is worth building into the real pipeline.

Sourced entirely from Pixabay's licensed stock video library (same provider/license
pipeline/broll.py already uses for normal b-roll) -- not real "viral moments" footage.
The "hook/shock" ranking below is a keyword-against-tag heuristic, not a measure of
genuine virality; generic stock footage has no real moment to rank, so treat the
result as "5 higher-energy stock clips," not an actual highlight reel.

Run locally (needs your own PIXABAY_API_KEY -- get a free one at pixabay.com/api/docs
if you don't already have it; this script never has access to the one stored as a
GitHub Actions secret):

    set PIXABAY_API_KEY=your_key_here          (PowerShell: $env:PIXABAY_API_KEY = "...")
    python pipeline/preview_animal_ranking.py --out work/animal_ranking_preview.mp4
"""
import argparse
import json
import random
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config
from pipeline.broll import SEARCH_URL, USER_AGENT, _best_video_file

WIDTH, HEIGHT = 1080, 1920
OUTPUT_FPS = 30
CLIP_SECONDS = 5.0

# A handful of broad queries so the candidate pool isn't dominated by whichever single
# search term Pixabay happens to have the most footage for.
QUERIES = ["wild animal", "animal chase", "animal jump", "funny animal", "animal fight", "animal rescue"]
PER_QUERY = 15

# Keyword-against-tag heuristic only -- Pixabay hits carry a comma-separated "tags"
# string, not any real popularity/virality signal. This just biases the pick toward
# clips *tagged* as higher-energy, it can't manufacture a genuine moment that isn't
# in generic stock footage.
HOOK_SHOCK_KEYWORDS = {
    "attack", "chase", "jump", "leap", "fight", "bite", "charge", "hunt", "escape",
    "fall", "fast", "wild", "dramatic", "rescue", "surprise", "clash", "dangerous",
    "predator", "prey", "stunt", "daring", "battle", "run", "sprint", "roar", "strike",
}


def _search(query, api_key, per_page):
    params = urllib.parse.urlencode({
        "key": api_key, "q": query, "video_type": "film", "safesearch": "true", "per_page": per_page,
    })
    req = urllib.request.Request(f"{SEARCH_URL}?{params}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()).get("hits", [])
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Pixabay request failed ({e.code}): {e.read().decode()}") from e


def _score(hit):
    tags = {t.strip().lower() for t in hit.get("tags", "").split(",") if t.strip()}
    return len(tags & HOOK_SHOCK_KEYWORDS)


def gather_top5(api_key):
    seen_ids, candidates = set(), []
    for q in QUERIES:
        for hit in _search(q, api_key, PER_QUERY):
            if hit["id"] in seen_ids:
                continue
            seen_ids.add(hit["id"])
            candidates.append(hit)
    if len(candidates) < 5:
        raise RuntimeError(f"only found {len(candidates)} distinct candidates, need 5")
    ranked = sorted(candidates, key=_score, reverse=True)[:5]
    return list(reversed(ranked))  # weakest-of-the-top-5 first -> strongest last, countdown order


def _download(hit, out_path):
    file_info = _best_video_file(hit)
    req = urllib.request.Request(file_info["url"], headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as resp, open(out_path, "wb") as f:
        f.write(resp.read())


def _probe_duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(out.strip())


def _rank_clip(src, dst, rank, clip_seconds, font_file):
    target = clip_seconds + 0.35
    clip_len = _probe_duration(src)
    loop_count = max(int((target // clip_len) + 1), 1) if clip_len > 0 else 1
    drawtext = (
        f"drawtext=text='#{rank}':fontfile='{font_file}':fontsize=200:fontcolor=white:"
        f"x=(w-text_w)/2:y=120:box=1:boxcolor=black@0.45:boxborderw=24"
    )
    vf = (
        f"fps={OUTPUT_FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},eq=contrast=1.08:saturation=1.18,{drawtext}"
    )
    subprocess.run([
        "ffmpeg", "-y", "-stream_loop", str(loop_count - 1), "-i", str(src),
        "-t", str(target), "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", str(dst),
    ], check=True)


# Procedurally synthesized, not sourced from anywhere -- same reasoning as
# assemble.py's _add_hook_sound: zero licensing question. A simple ascending
# four-note chime loop, upbeat/percussive-feeling rather than melodic, meant as a
# placeholder "fun soundtrack" for this preview, not a finished score.
def _synth_soundtrack(total_seconds, work_dir):
    notes = [523.25, 659.25, 783.99, 1046.50]  # C5 E5 G5 C6 -- a plain major chime
    note_dur = 0.18
    tones = work_dir / "tones.mp3"
    inputs, filters = [], []
    for i, freq in enumerate(notes):
        inputs += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={note_dur}"]
        filters.append(f"[{i}:a]")
    subprocess.run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", f"{''.join(filters)}concat=n={len(notes)}:v=0:a=1,volume=0.5[out]",
        "-map", "[out]", str(tones),
    ], check=True)

    loop_count = max(int(total_seconds // (note_dur * len(notes))) + 2, 1)
    soundtrack = work_dir / "soundtrack.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-stream_loop", str(loop_count - 1), "-i", str(tones),
        "-t", str(total_seconds), str(soundtrack),
    ], check=True)
    return soundtrack


def build_preview(api_key, out_path, work_dir, font_file):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    top5 = gather_top5(api_key)
    scaled_paths = []
    for i, hit in enumerate(top5):
        rank = 5 - i  # top5[0] is weakest (rank 5), top5[-1] is strongest (rank 1)
        raw = work_dir / f"raw_{i}.mp4"
        _download(hit, raw)
        scaled = work_dir / f"ranked_{i}.mp4"
        _rank_clip(raw, scaled, rank, CLIP_SECONDS, font_file)
        scaled_paths.append(scaled)

    concat_list = work_dir / "concat.txt"
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in scaled_paths))
    video_track = work_dir / "video_track.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c:v", "libx264", "-preset", "veryfast", "-r", str(OUTPUT_FPS), "-pix_fmt", "yuv420p",
        str(video_track),
    ], check=True)

    total_seconds = len(scaled_paths) * CLIP_SECONDS
    soundtrack = _synth_soundtrack(total_seconds, work_dir)

    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_track), "-i", str(soundtrack),
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
        "-t", str(total_seconds), str(out_path),
    ], check=True)

    return str(out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="work/animal_ranking_preview.mp4")
    parser.add_argument("--work-dir", default="work/animal_ranking_preview")
    parser.add_argument("--font", default=r"C:\Windows\Fonts\arialbd.ttf",
                         help="TrueType font file for the rank-number overlay")
    args = parser.parse_args()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    result = build_preview(config.require("PIXABAY_API_KEY"), args.out, args.work_dir, args.font)
    print(f"wrote {result} -- local preview only, not uploaded anywhere")
