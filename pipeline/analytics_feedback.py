"""Pull performance stats for previously uploaded shorts and summarize what's working,
so the agent can weight future topic/style choices toward it. Run this once/day (not
on every fire) to conserve Analytics API quota."""
import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config
from pipeline.state_utils import load_json, save_json


def _analytics_client():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=config.require("YOUTUBE_REFRESH_TOKEN"),
        client_id=config.require("YOUTUBE_CLIENT_ID"),
        client_secret=config.require("YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtubeAnalytics", "v2", credentials=creds)


def pull_stats(video_ids, days_back=28):
    if not video_ids:
        return {}
    analytics = _analytics_client()
    start = (date.today() - timedelta(days=days_back)).isoformat()
    end = date.today().isoformat()

    response = analytics.reports().query(
        ids="channel==MINE",
        startDate=start,
        endDate=end,
        metrics="views,likes,averageViewPercentage",
        dimensions="video",
        filters=f"video=={','.join(video_ids)}",
    ).execute()

    stats = {}
    for row in response.get("rows", []):
        video_id, views, likes, avg_view_pct = row
        stats[video_id] = {"views": views, "likes": likes, "avg_view_pct": avg_view_pct}
    return stats


def update_performance_log(performance_log_path, used_topics_path):
    performance = load_json(performance_log_path, {"videos": []})
    used = load_json(used_topics_path, {"topics": []})

    topic_by_video = {t["video_id"]: t["topic"] for t in used["topics"] if t.get("video_id")}
    stats = pull_stats(list(topic_by_video.keys()))

    for video_id, s in stats.items():
        existing = next((v for v in performance["videos"] if v["video_id"] == video_id), None)
        record = {"video_id": video_id, "topic": topic_by_video.get(video_id), **s}
        if existing:
            existing.update(record)
        else:
            performance["videos"].append(record)

    save_json(performance_log_path, performance)
    return performance


def summarize(performance):
    by_topic = defaultdict(list)
    for v in performance["videos"]:
        if v.get("topic"):
            by_topic[v["topic"]].append(v.get("avg_view_pct") or 0)

    ranked = sorted(by_topic.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True)
    return [{"topic": topic, "avg_view_pct": sum(vals) / len(vals)} for topic, vals in ranked[:10]]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--performance-log", default=str(config.STATE_DIR / "performance_log.json"))
    parser.add_argument("--used-topics", default=str(config.STATE_DIR / "used_topics.json"))
    args = parser.parse_args()

    perf = update_performance_log(args.performance_log, args.used_topics)
    print(json.dumps(summarize(perf), indent=2))
