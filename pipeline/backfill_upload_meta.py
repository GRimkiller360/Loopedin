"""One-off (but safe to re-run) backfill for used_topics.json entries uploaded before
record_used_topic() started auto-recording uploaded_at/title/duration_seconds. Without
uploaded_at those videos are permanently invisible to live_stats.py's tracking and sort
to the bottom of the dashboard's per-video table -- this recovers what's actually
recoverable from YouTube's own API (publish time, title, duration). category/hook_type/
seed_view_count aren't recoverable this way since they were never YouTube-side data to
begin with; those stay missing for pre-refactor videos.

Idempotent: only fills fields that are currently missing, safe to run again later if
more legacy entries ever need it.
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config
from pipeline.state_utils import load_json, save_json

VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
ISO8601_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")


def _get(url, params):
    query = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(f"{url}?{query}") as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} request failed ({e.code}): {e.read().decode()}") from e


def _parse_duration_seconds(iso_duration):
    m = ISO8601_DURATION_RE.fullmatch(iso_duration)
    if not m:
        return None
    hours, minutes, seconds = (float(g) if g else 0.0 for g in m.groups())
    return hours * 3600 + minutes * 60 + seconds


def fetch_upload_meta(video_ids, api_key):
    meta = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        resp = _get(VIDEOS_URL, {"key": api_key, "part": "snippet,contentDetails", "id": ",".join(batch)})
        for item in resp.get("items", []):
            snippet, content = item["snippet"], item["contentDetails"]
            published_at = snippet["publishedAt"]  # RFC3339, e.g. 2026-08-10T14:03:00Z
            meta[item["id"]] = {
                "uploaded_at": published_at.replace("Z", "+00:00"),
                "publish_hour_utc": int(published_at[11:13]),
                "title": snippet.get("title"),
                "duration_seconds": _parse_duration_seconds(content["duration"]),
            }
    return meta


def backfill(used_topics_path, api_key):
    used = load_json(used_topics_path, {"topics": []})
    missing_ids = [
        t["video_id"] for t in used["topics"]
        if t.get("video_id") and not t.get("uploaded_at")
    ]
    if not missing_ids:
        return used, 0

    meta = fetch_upload_meta(missing_ids, api_key)
    filled = 0
    for t in used["topics"]:
        video_id = t.get("video_id")
        if video_id in meta:
            for key, value in meta[video_id].items():
                if not t.get(key) and value is not None:
                    t[key] = value
            filled += 1

    save_json(used_topics_path, used)
    return used, filled


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--used-topics", default=str(config.STATE_DIR / "used_topics.json"))
    args = parser.parse_args()

    _, filled = backfill(args.used_topics, config.require("YOUTUBE_API_KEY"))
    print(f"Backfilled {filled} video(s).")
