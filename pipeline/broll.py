"""Fetch stock b-roll clips from Pexels for each script beat.

Pexels footage is licensed for this kind of reuse -- this is the deliberate
alternative to clipping any real creator's copyrighted video/audio.
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config

SEARCH_URL = "https://api.pexels.com/videos/search"
# Cloudflare (fronting pexels.com) fingerprint-blocks urllib's default
# "Python-urllib/3.x" User-Agent (Cloudflare error 1010) -- any real-looking UA works.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _best_vertical_file(video):
    verticals = [f for f in video["video_files"] if f["height"] > f["width"]]
    candidates = verticals or video["video_files"]
    return min(candidates, key=lambda f: abs(f.get("width", 0) - 1080))


def fetch_clip_for_query(query, out_path, used_video_ids, api_key):
    params = urllib.parse.urlencode({"query": query, "orientation": "portrait", "per_page": 5})
    req = urllib.request.Request(
        f"{SEARCH_URL}?{params}",
        headers={"Authorization": api_key, "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Pexels request failed ({e.code}): {e.read().decode()}") from e

    for video in result.get("videos", []):
        if video["id"] in used_video_ids:
            continue
        file_info = _best_vertical_file(video)
        dl_req = urllib.request.Request(file_info["link"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(dl_req) as dl_resp, open(out_path, "wb") as f:
            f.write(dl_resp.read())
        used_video_ids.add(video["id"])
        return True
    return False


def fetch_all(script, work_dir, api_key):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    used_video_ids = set()
    clip_paths = []

    for i, beat in enumerate(script["beats"]):
        out_path = work_dir / f"beat_{i:02d}.mp4"
        found = fetch_clip_for_query(beat["broll_query"], out_path, used_video_ids, api_key)
        if not found:
            found = fetch_clip_for_query(script["topic"], out_path, used_video_ids, api_key)
        if not found:
            raise RuntimeError(f"no b-roll found for beat {i} (query={beat['broll_query']!r})")
        clip_paths.append(str(out_path))

    return clip_paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--work-dir", required=True)
    args = parser.parse_args()

    script = json.loads(Path(args.script).read_text(encoding="utf-8"))
    paths = fetch_all(script, args.work_dir, config.require("PEXELS_API_KEY"))
    print(json.dumps(paths, indent=2))
