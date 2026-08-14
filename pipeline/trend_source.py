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
    with urllib.request.urlopen(f"{url}?{query}") as resp:
        return json.loads(resp.read())


def _iso_duration_to_seconds(duration):
    match = DURATION_RE.match(duration)
    h, m, s = (int(x) if x else 0 for x in match.groups())
    return h * 3600 + m * 60 + s


def find_trend_seed(used_topics_path, api_key):
    used = load_json(used_topics_path, {"topics": []})
    recent_topics = {t["topic"].lower() for t in used["topics"][-config.VARIETY_LOOKBACK:]}

    published_after = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    categories = SEED_CATEGORIES[:]
    random.shuffle(categories)

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
            if duration_s > 60 or title.lower() in recent_topics:
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
