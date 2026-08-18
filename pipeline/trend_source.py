"""Fetch several trending-Shorts topic seed candidates via YouTube Data API search.list.

There is no dedicated "trending Shorts" endpoint on the YouTube Data API, so this
approximates it: recent (last 48h), short-duration (<=60s), high-viewCount videos
across a rotating set of seed categories. Returns *topic seeds* only -- title,
category, view count -- never the source video's transcript/audio/footage. The agent
uses a seed as inspiration for an original script; it must not summarize or transcribe
the source video.

Returns multiple candidates (not just one) so the agent has somewhere to go if its
first choice turns out to be too close to a recent video -- at no extra quota cost,
since they're already deduped/collected from the same category calls a single-seed
fetch would have made anyway.
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

# Kept in sync with pipeline/script_schema.py's CATEGORIES -- see that file's comment
# for why this was narrowed from the original 10 to one coherent niche.
SEED_CATEGORIES = [
    "science facts", "psychology", "space", "history",
]

# search.list costs 100 quota units/call -- cap categories tried per run so a bad-luck
# run (everything recently used) can't burn the whole day's budget on trend-spotting.
MAX_CATEGORIES_PER_RUN = 5
MAX_CANDIDATES = 3

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


# Baseline weight for a category with no performance data yet -- roughly mid-range on
# the avg_view_pct scale, so unexplored categories stay competitive with proven ones
# instead of getting starved out before they've had a fair sample.
UNEXPLORED_CATEGORY_WEIGHT = 50.0
MIN_CATEGORY_WEIGHT = 5.0  # floor so one unlucky low-sample video doesn't zero a category out


def _category_weights(performance_log_path):
    perf = load_json(performance_log_path, {"videos": []})
    by_category = {}
    for v in perf.get("videos", []):
        cat, pct = v.get("category"), v.get("avg_view_pct")
        if cat and pct is not None:
            by_category.setdefault(cat, []).append(pct)

    weights = {}
    for cat in SEED_CATEGORIES:
        samples = by_category.get(cat)
        if samples:
            weights[cat] = max(sum(samples) / len(samples), MIN_CATEGORY_WEIGHT)
        else:
            weights[cat] = UNEXPLORED_CATEGORY_WEIGHT
    return weights


def _weighted_shuffle(items, weights):
    # Exponential-key trick: sampling without replacement where each item's chance of
    # sorting first is proportional to its weight. Still gives every category a real
    # (if smaller) chance -- this steers the odds toward what's performed well, it
    # doesn't hard-lock the routine into only ever trying past winners.
    keyed = [(random.random() ** (1.0 / weights[item]), item) for item in items]
    keyed.sort(reverse=True)
    return [item for _, item in keyed]


def find_trend_seeds(used_topics_path, api_key, max_candidates=MAX_CANDIDATES, performance_log_path=None):
    used = load_json(used_topics_path, {"topics": []})
    recent = used["topics"][-config.VARIETY_LOOKBACK:]
    recent_topics = {t["topic"].lower() for t in recent}
    # The authoritative dedup check: recent_topics compares AI-authored topic labels
    # against candidate video titles, which are worded completely differently and
    # essentially never match -- that alone let the same top-viewCount video get
    # reselected run after run. Excluding by exact source video ID actually works.
    recent_source_ids = {t["seed_source_video_id"] for t in recent if t.get("seed_source_video_id")}

    published_after = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if performance_log_path:
        weights = _category_weights(performance_log_path)
        categories = _weighted_shuffle(SEED_CATEGORIES, weights)[:MAX_CATEGORIES_PER_RUN]
    else:
        categories = SEED_CATEGORIES[:]
        random.shuffle(categories)
        categories = categories[:MAX_CATEGORIES_PER_RUN]

    candidates = []
    seen_ids_this_run = set()

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
            vid = video["id"]
            if (
                duration_s > 60
                or vid in recent_source_ids
                or vid in seen_ids_this_run
                or title.lower() in recent_topics
            ):
                continue
            candidates.append({
                "seed_category": query,
                "source_title": title,
                "source_description": video["snippet"].get("description", "")[:500],
                "source_video_id": vid,
                "view_count": int(video["statistics"].get("viewCount", 0)),
            })
            seen_ids_this_run.add(vid)
            if len(candidates) >= max_candidates:
                break
        if len(candidates) >= max_candidates:
            break

    if not candidates:
        # nothing fresh found in any tried category -- fall back to a couple of bare
        # category seeds (no specific video) so the agent still has more than one
        # option, just without a viral-video anchor to riff on.
        fallback_categories = random.sample(SEED_CATEGORIES, min(2, len(SEED_CATEGORIES)))
        candidates = [{
            "seed_category": cat, "source_title": None, "source_description": None,
            "source_video_id": None, "view_count": 0,
        } for cat in fallback_categories]

    return candidates


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--used-topics", default=str(config.STATE_DIR / "used_topics.json"))
    parser.add_argument("--performance-log", default=str(config.STATE_DIR / "performance_log.json"))
    args = parser.parse_args()

    candidates = find_trend_seeds(
        args.used_topics, config.require("YOUTUBE_API_KEY"), performance_log_path=args.performance_log
    )
    output = {"candidates": candidates}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(output, indent=2))
    print(json.dumps(output, indent=2))
