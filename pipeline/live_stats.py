"""Near-real-time view/like/comment counts via videos.list, as a companion to
analytics_feedback.py's daily YouTube Analytics pull.

Why this exists: YouTube Analytics data (avg_view_pct, subscribersGained, etc.) only
settles on a ~24-48h reporting cycle -- fine for the once/day retention ranking, but
useless as an *early* velocity signal, since a video's first two days show nothing.
videos.list statistics (viewCount/likeCount/commentCount) update far faster and use the
same public YOUTUBE_API_KEY trend_source.py already has, no OAuth needed. Run this on
the same ~10x/day cadence as trend-fetch.yml so "is this one taking off" is visible
within hours, not days.
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config
from pipeline.state_utils import load_json, save_json

VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def _get(url, params):
    query = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(f"{url}?{query}") as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} request failed ({e.code}): {e.read().decode()}") from e


VELOCITY_SNAPSHOT_HOURS = 2.0


def fetch_live_stats(video_ids, api_key):
    if not video_ids:
        return {}
    stats = {}
    # videos.list takes up to 50 ids per call
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        resp = _get(VIDEOS_URL, {"key": api_key, "part": "statistics", "id": ",".join(batch)})
        for item in resp.get("items", []):
            s = item["statistics"]
            stats[item["id"]] = {
                "views": int(s.get("viewCount", 0)),
                "likes": int(s.get("likeCount", 0)),
                "comments": int(s.get("commentCount", 0)),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
    return stats


def update_live_stats(live_stats_path, used_topics_path, api_key):
    # Every uploaded video, unconditionally -- not just ones uploaded "recently".
    # videos.list (part=statistics only) is cheap, and this is the only source that
    # keeps working once YouTube Analytics has settled data (Analytics stops being a
    # useful fallback trigger at that point, but there's no reason to stop refreshing
    # live counts for older videos just because they've aged past some window --
    # build_video_list's dashboard table needs *some* number for every video, not just
    # the newest ones).
    used = load_json(used_topics_path, {"topics": []})
    uploaded_at_by_id = {t["video_id"]: t.get("uploaded_at") for t in used["topics"] if t.get("video_id")}
    video_ids = list(uploaded_at_by_id.keys())

    fresh = fetch_live_stats(video_ids, api_key)
    existing = load_json(live_stats_path, {})
    now = datetime.now(timezone.utc)

    for vid, s in fresh.items():
        prior = existing.get(vid, {})
        if "views_at_2h" in prior:
            # Already captured -- carry it forward permanently. A fixed early-velocity
            # snapshot is only useful if it never moves again; overwriting it with a
            # later, larger count on the next hourly refresh would defeat the point.
            s["views_at_2h"] = prior["views_at_2h"]
            s["views_at_2h_elapsed_hours"] = prior.get("views_at_2h_elapsed_hours")
            continue
        uploaded_at = uploaded_at_by_id.get(vid)
        if not uploaded_at:
            continue
        try:
            uploaded_dt = datetime.fromisoformat(uploaded_at)
        except ValueError:
            continue
        if uploaded_dt.tzinfo is None:
            uploaded_dt = uploaded_dt.replace(tzinfo=timezone.utc)
        elapsed_hours = (now - uploaded_dt).total_seconds() / 3600
        if elapsed_hours >= VELOCITY_SNAPSHOT_HOURS:
            # First refresh past the 2h mark -- lock this count in as the
            # early-velocity signal. Actual elapsed time varies with the refresh
            # cadence (could be 2.0-3.0h+ depending on timing), so record the real
            # value rather than pretending this is exactly 2h every time.
            s["views_at_2h"] = s["views"]
            s["views_at_2h_elapsed_hours"] = round(elapsed_hours, 2)

    existing.update(fresh)
    # drop anything no longer in used_topics.json at all (e.g. deleted uploads)
    existing = {vid: s for vid, s in existing.items() if vid in video_ids}
    save_json(live_stats_path, existing)
    return existing


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-stats", default=str(config.STATE_DIR / "live_stats.json"))
    parser.add_argument("--used-topics", default=str(config.STATE_DIR / "used_topics.json"))
    args = parser.parse_args()

    result = update_live_stats(args.live_stats, args.used_topics, config.require("YOUTUBE_API_KEY"))
    print(json.dumps(result, indent=2))
