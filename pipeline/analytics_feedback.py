"""Pull performance stats for previously uploaded shorts and summarize what's working,
so the agent can weight future topic/style choices toward it. Run once/day, timed
right before the first routine fire of the day (see analytics-feedback.yml) --
YouTube Analytics data itself only settles on a ~24-48h cycle, so querying more often
than daily would just re-fetch unchanged numbers. Near-real-time early-velocity data
comes from a separate, faster-updating source -- see live_stats.py."""
import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
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
        metrics="views,likes,comments,averageViewPercentage,subscribersGained",
        dimensions="video",
        filters=f"video=={','.join(video_ids)}",
    ).execute()

    stats = {}
    for row in response.get("rows", []):
        video_id, views, likes, comments, avg_view_pct, subs_gained = row
        stats[video_id] = {
            "views": views,
            "likes": likes,
            "comments": comments,
            "avg_view_pct": avg_view_pct,
            "subscribers_gained": subs_gained,
        }
    return stats


def pull_traffic_sources(days_back=28):
    """Channel-level, not per-video -- how viewers are actually finding this channel's
    videos overall. Source labels are the raw insightTrafficSourceType enum values the
    API returns (e.g. SHORTS, YT_SEARCH, SUBSCRIBER, PLAYLIST, EXT_URL) rather than a
    hand-written translation table, since getting that mapping wrong would silently
    mislabel the data -- worse than showing the API's own names verbatim."""
    analytics = _analytics_client()
    start = (date.today() - timedelta(days=days_back)).isoformat()
    end = date.today().isoformat()

    response = analytics.reports().query(
        ids="channel==MINE",
        startDate=start,
        endDate=end,
        metrics="views",
        dimensions="insightTrafficSourceType",
        sort="-views",
    ).execute()

    rows = response.get("rows", [])
    total = sum(r[1] for r in rows) or 1
    return [{"source": r[0], "views": r[1], "share_pct": r[1] / total * 100} for r in rows]


def update_performance_log(performance_log_path, used_topics_path):
    performance = load_json(performance_log_path, {"videos": []})
    used = load_json(used_topics_path, {"topics": []})

    meta_by_video = {
        t["video_id"]: {
            "topic": t["topic"],
            "category": t.get("category"),
            "hook_type": t.get("hook_type"),
            "uploaded_at": t.get("uploaded_at"),
            "publish_hour_utc": t.get("publish_hour_utc"),
            "duration_seconds": t.get("duration_seconds"),
            "seed_view_count": t.get("seed_view_count"),
        }
        for t in used["topics"] if t.get("video_id")
    }
    stats = pull_stats(list(meta_by_video.keys()))

    for video_id, s in stats.items():
        existing = next((v for v in performance["videos"] if v["video_id"] == video_id), None)
        record = {"video_id": video_id, **meta_by_video.get(video_id, {}), **s}
        if existing:
            existing.update(record)
        else:
            performance["videos"].append(record)

    save_json(performance_log_path, performance)
    return performance


def _length_bucket(seconds):
    if seconds <= 20:
        return "short (<=20s)"
    if seconds <= 40:
        return "medium (20-40s)"
    return "long (40-58s)"


def _seed_momentum_bucket(view_count):
    if not view_count:
        return "no source video"
    if view_count < 100_000:
        return "low (<100k)"
    if view_count < 1_000_000:
        return "medium (100k-1M)"
    return "high (>=1M)"


def _rate_per_1k(count, views):
    if not views:
        return 0.0
    return count / views * 1000


def summarize(performance):
    # Category, hook_type, length, publish hour, and seed momentum are the
    # generalizable signals -- unlike an exact topic, all of them repeat across videos,
    # so they're what's actually safe to steer future choices by. Per-topic detail is
    # kept too, but only as reference. Each bucket tracks three metrics: avg_view_pct
    # (retention), sub_rate_per_1k_views (does this convert viewers to subscribers --
    # directly relevant to the 1,000-subscriber monetization gate, not just the view
    # count gate), and engagement_rate_per_1k_views (likes+comments -- an explicit
    # algorithm signal, distinct from passive watch time).
    dimensions = {
        "by_category": defaultdict(list),
        "by_hook_type": defaultdict(list),
        "by_length": defaultdict(list),
        "by_publish_hour": defaultdict(list),
        "by_seed_momentum": defaultdict(list),
        "by_topic": defaultdict(list),
    }
    for v in performance["videos"]:
        views = v.get("views") or 0
        entry = {
            "avg_view_pct": v.get("avg_view_pct") or 0,
            "sub_rate": _rate_per_1k(v.get("subscribers_gained") or 0, views),
            "eng_rate": _rate_per_1k((v.get("likes") or 0) + (v.get("comments") or 0), views),
        }
        if v.get("category"):
            dimensions["by_category"][v["category"]].append(entry)
        if v.get("hook_type"):
            dimensions["by_hook_type"][v["hook_type"]].append(entry)
        if v.get("duration_seconds") is not None:
            dimensions["by_length"][_length_bucket(v["duration_seconds"])].append(entry)
        if v.get("publish_hour_utc") is not None:
            dimensions["by_publish_hour"][f"{v['publish_hour_utc']:02d}:00 UTC"].append(entry)
        if v.get("seed_view_count") is not None:
            dimensions["by_seed_momentum"][_seed_momentum_bucket(v["seed_view_count"])].append(entry)
        if v.get("topic"):
            dimensions["by_topic"][v["topic"]].append(entry)

    def _avg(key, entries):
        return sum(e[key] for e in entries) / len(entries)

    def _rank(bucket, limit):
        ranked = sorted(bucket.items(), key=lambda kv: _avg("avg_view_pct", kv[1]), reverse=True)
        return [
            {
                "name": name,
                "avg_view_pct": _avg("avg_view_pct", entries),
                "sub_rate_per_1k_views": _avg("sub_rate", entries),
                "engagement_rate_per_1k_views": _avg("eng_rate", entries),
                "sample_size": len(entries),
            }
            for name, entries in ranked[:limit]
        ]

    return {name: _rank(bucket, 10) for name, bucket in dimensions.items()}


def recent_uploads(used_topics_path, live_stats_path, hours=48):
    """Early-velocity leaderboard sourced from live_stats.json (near-real-time
    videos.list counts), NOT the once/day Analytics performance log -- that log has a
    24-48h reporting lag, so for exactly the window this function covers it would show
    nothing. Raw view count (not avg_view_pct, which isn't available yet here) is the
    right sort key: this is a reach/velocity signal, not a retention signal."""
    used = load_json(used_topics_path, {"topics": []})
    live = load_json(live_stats_path, {})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    recent = []
    for t in used["topics"]:
        video_id, uploaded_at = t.get("video_id"), t.get("uploaded_at")
        if not video_id or not uploaded_at or video_id not in live:
            continue
        try:
            when = datetime.fromisoformat(uploaded_at)
        except ValueError:
            continue
        if when < cutoff:
            continue
        recent.append({
            "topic": t["topic"],
            "category": t.get("category"),
            "hook_type": t.get("hook_type"),
            **live[video_id],
        })
    return sorted(recent, key=lambda v: v.get("views", 0), reverse=True)


def render_summary_markdown(summary, recent, traffic_sources):
    lines = ["# Performance summary (auto-generated, read this before writing a script)", ""]

    if recent:
        lines.append("## Recent uploads (last 48h) -- early velocity signal, near-real-time (not the retention ranking below)")
        lines.append("")
        for r in recent:
            hook = r.get("hook_type", "n/a")
            lines.append(f"- {r['topic']} ({r.get('category', 'n/a')}/{hook}): {r.get('views', 0)} views, {r.get('likes', 0)} likes, {r.get('comments', 0)} comments")
        lines.append("")

    if not summary["by_category"]:
        lines.append("No performance data yet -- not enough uploads/history to summarize.")
        return "\n".join(lines) + "\n"

    def _section(title, key, note=""):
        rows = summary[key]
        if not rows:
            return
        lines.append(f"## {title}{note}")
        lines.append("")
        for r in rows:
            lines.append(
                f"- {r['name']}: {r['avg_view_pct']:.1f}% avg view, "
                f"{r['sub_rate_per_1k_views']:.2f} subs/1k views, "
                f"{r['engagement_rate_per_1k_views']:.2f} likes+comments/1k views (n={r['sample_size']})"
            )
        lines.append("")

    _section("Top categories (steer topic choice by this)", "by_category")
    _section("Top hook styles (steer how you open/close by this)", "by_hook_type")
    _section("Top video lengths (steer target narration length by this)", "by_length")
    _section("Top publish hours (UTC) -- reference only, needs more spread of upload times before it means anything", "by_publish_hour")
    _section("Top seed momentum tiers -- does a higher-view-count trend seed actually predict this channel's own performance?", "by_seed_momentum")

    lines.append("## Top individual topics (reference only -- these exact topics are already used)")
    lines.append("")
    for r in summary["by_topic"]:
        lines.append(f"- {r['name']}: {r['avg_view_pct']:.1f}% avg view")
    lines.append("")

    if traffic_sources:
        lines.append("## Where views are coming from (last 28 days, channel-wide, reference only -- not a per-video lever)")
        lines.append("")
        for s in traffic_sources:
            lines.append(f"- {s['source']}: {s['share_pct']:.1f}% of views ({s['views']})")
        lines.append("")

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--performance-log", default=str(config.STATE_DIR / "performance_log.json"))
    parser.add_argument("--used-topics", default=str(config.STATE_DIR / "used_topics.json"))
    parser.add_argument("--live-stats", default=str(config.STATE_DIR / "live_stats.json"))
    parser.add_argument("--summary-out", default=None, help="if set, also render+write performance_summary.md here")
    args = parser.parse_args()

    perf = update_performance_log(args.performance_log, args.used_topics)
    summary = summarize(perf)
    recent = recent_uploads(args.used_topics, args.live_stats)
    traffic = pull_traffic_sources()

    output = dict(summary)
    output["recent_uploads"] = recent
    output["traffic_sources"] = traffic
    print(json.dumps(output, indent=2))

    if args.summary_out:
        Path(args.summary_out).write_text(render_summary_markdown(summary, recent, traffic), encoding="utf-8")
