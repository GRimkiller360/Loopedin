"""Fetch a trending-Shorts topic seed via YouTube Data API search.list.

There is no dedicated "trending Shorts" endpoint on the YouTube Data API, so this
approximates it: recent (last 48h), short-duration (<=60s), high-viewCount videos
across a rotating set of seed categories. This returns a *topic seed* only -- title,
category, view count -- never the source video's transcript/audio/footage. The agent
uses the seed as inspiration for an original script; it must not summarize or
transcribe the source video.
"""
import argparse
import json
import random
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config
from pipeline.state_utils import load_json

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

SEED_CATEGORIES = [
    "technology", "science facts", "life hacks", "history", "true crime mystery",
    "personal finance", "space", "psychology", "fitness", "AI news",
]

DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _get(url, params):
    query = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(f"{url}?{query}") as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} request failed ({e.code}): {e.read().decode()}") from e


def _iso_duration_to_seconds(duration):
    match = DURATION_RE.match(duration)
    h, m, s = (int(x) if x else 0 for x in match.groups())
    return h * 3600 + m * 60 + s


def find_trend_seed(used_topics_path, api_key):
    used = load_json(used_topics_path, {"topics": []})
    recent = used["topics"][-config.VARIETY_LOOKBACK:]
    recent_topics = {t["topic"].lower() for t in recent}
    # The authoritative dedup check: recent_topics compares AI-authored topic labels
    # against candidate video titles, which are worded completely differently and
    # essentially never match -- that alone let the same top-viewCount video get
    # reselected run after run. Excluding by exact source video ID actually works.
    recent_source_ids = {t["seed_source_video_id"] for t in recent if t.get("seed_source_video_id")}

    published_after = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    categories = SEED_CATEGORIES[:]
    random.shuffle(categories)
    # search.list costs 100 quota units/call -- cap worst-case categories tried per
    # run so a bad-luck run (everything recently used) can't burn the whole day's
    # budget on trend-spotting alone.
    categories = categories[:5]

    for query in categories:
        search_resp = _get(SEARCH_URL, {
            "key": api_key,
            "part": "snippet",
            "type": "video",
            "videoDuration": "short",
            "order": "viewCount",
            "publishedAfter": published_after,
            "maxResults": 10,
            "q": query,
        })
        video_ids = [item["id"]["videoId"] for item in search_resp.get("items", [])]
        if not video_ids:
            continue

        details_resp = _get(VIDEOS_URL, {
            "key": api_key,
            "part": "snippet,contentDetails,statistics",
            "id": ",".join(video_ids),
        })

        for video in details_resp.get("items", []):
            duration_s = _iso_duration_to_seconds(video["contentDetails"]["duration"])
            title = video["snippet"]["title"]
            if duration_s > 60 or video["id"] in recent_source_ids or title.lower() in recent_topics:
                continue
            return {
                "seed_category": query,
                "source_title": title,
                "source_description": video["snippet"].get("description", "")[:500],
                "source_video_id": video["id"],
                "view_count": int(video["statistics"].get("viewCount", 0)),
            }

    # nothing fresh found across any category -- fall back to a bare category seed
    return {
        "seed_category": random.choice(SEED_CATEGORIES),
        "source_title": None,
        "source_description": None,
        "source_video_id": None,
        "view_count": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--used-topics", default=str(config.STATE_DIR / "used_topics.json"))
    args = parser.parse_args()

    seed = find_trend_seed(args.used_topics, config.require("YOUTUBE_API_KEY"))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(seed, indent=2))
    print(json.dumps(seed, indent=2))
