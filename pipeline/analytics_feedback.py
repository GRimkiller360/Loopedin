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
        metrics="views,likes,comments,shares,averageViewPercentage,subscribersGained",
        dimensions="video",
        filters=f"video=={','.join(video_ids)}",
    ).execute()

    stats = {}
    for row in response.get("rows", []):
        video_id, views, likes, comments, shares, avg_view_pct, subs_gained = row
        stats[video_id] = {
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "avg_view_pct": avg_view_pct,
            "subscribers_gained": subs_gained,
        }
    return stats


def pull_retention_curve(video_id, days_back=90):
    """Per-video audience-retention curve: elapsedVideoTimeRatio (0-1, fraction of
    video length) vs audienceWatchRatio (fraction of viewers still watching at that
    point). Unlike pull_stats' batched metrics, this dimension is only queryable
    scoped to a single video filter -- one API call per video, not batchable."""
    analytics = _analytics_client()
    start = (date.today() - timedelta(days=days_back)).isoformat()
    end = date.today().isoformat()

    response = analytics.reports().query(
        ids="channel==MINE",
        startDate=start,
        endDate=end,
        metrics="audienceWatchRatio",
        dimensions="elapsedVideoTimeRatio",
        filters=f"video=={video_id}",
    ).execute()
    return sorted((row[0], row[1]) for row in response.get("rows", []))


def beat_dropoff(retention_curve, beat_timings):
    """For each beat, how much audienceWatchRatio falls from its start to its end --
    the actionable per-beat signal a single averaged avg_view_pct can't give: 'beat 2
    loses 18 points when it's phrased as a definition' is something a writing rule can
    act on directly."""
    if not retention_curve or not beat_timings:
        return []

    def _ratio_at(frac):
        at_or_before = [r for r in retention_curve if r[0] <= frac]
        return at_or_before[-1][1] if at_or_before else retention_curve[0][1]

    drops = []
    for i, bt in enumerate(beat_timings):
        start_ratio = _ratio_at(bt["start_frac"])
        end_ratio = _ratio_at(bt["end_frac"])
        drops.append({
            "beat_index": i,
            "text": bt.get("text"),
            "start_pct": round(start_ratio * 100, 1),
            "end_pct": round(end_ratio * 100, 1),
            "drop_pct_points": round((start_ratio - end_ratio) * 100, 1),
        })
    return drops


def pull_beat_dropoff(video_id, beat_timings):
    return beat_dropoff(pull_retention_curve(video_id), beat_timings)


def worst_beat_dropoffs(performance, limit=10):
    """Flattened, ranked per-beat drop-offs across every video with curve data --
    which specific beat *content* loses viewers, not just which video. Only counts
    real drops (a negative value means retention rose through that beat -- a rewatch
    loop or a strong beat, not something to flag)."""
    rows = []
    for v in performance["videos"]:
        for d in v.get("beat_dropoff") or []:
            if d.get("drop_pct_points", 0) > 0:
                rows.append({
                    "topic": v.get("topic"),
                    "beat_index": d["beat_index"],
                    "text": d.get("text"),
                    "drop_pct_points": d["drop_pct_points"],
                    "start_pct": d.get("start_pct"),
                    "end_pct": d.get("end_pct"),
                })
    rows.sort(key=lambda r: r["drop_pct_points"], reverse=True)
    return rows[:limit]


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
            "title": t.get("title"),
            "category": t.get("category"),
            "hook_type": t.get("hook_type"),
            "uploaded_at": t.get("uploaded_at"),
            "publish_hour_utc": t.get("publish_hour_utc"),
            "duration_seconds": t.get("duration_seconds"),
            "seed_view_count": t.get("seed_view_count"),
            "ruleset_version": t.get("ruleset_version"),
            "beat_timings": t.get("beat_timings"),
            "first_clip_tags": t.get("first_clip_tags"),
            "holdout": t.get("holdout"),
            "experiment_arm": t.get("experiment_arm"),
        }
        for t in used["topics"] if t.get("video_id")
    }
    stats = pull_stats(list(meta_by_video.keys()))
    existing_by_id = {v["video_id"]: v for v in performance["videos"]}

    for video_id, s in stats.items():
        existing = existing_by_id.get(video_id)
        record = {"video_id": video_id, **meta_by_video.get(video_id, {}), **s}
        if existing:
            existing.update(record)
            record = existing
        else:
            performance["videos"].append(record)
            existing_by_id[video_id] = record

        # Retention curve settles on the same ~24-48h lag as avg_view_pct, and doesn't
        # meaningfully change once settled -- only worth the extra per-video API call
        # once real data exists, and only once per video (cached via "beat_dropoff" in
        # record), not re-fetched every daily run forever.
        beat_timings = record.get("beat_timings")
        if record.get("avg_view_pct") is not None and beat_timings and "beat_dropoff" not in record:
            try:
                record["beat_dropoff"] = pull_beat_dropoff(video_id, beat_timings)
            except Exception as e:
                # Don't let one video's extra retention-curve call break the whole
                # daily run -- the core metrics above already succeeded and are worth
                # keeping even if this enrichment call fails.
                record["beat_dropoff_error"] = str(e)

    save_json(performance_log_path, performance)
    return performance


def _length_bucket(seconds):
    if seconds <= 20:
        return "short (<=20s)"
    if seconds <= 40:
        return "medium (20-40s)"
    return "long (40-58s)"


# Rough keyword buckets for Pixabay's free-text tags on beat 0's first clip -- the
# only visual-opening signal available without real computer vision (nothing in this
# pipeline analyzes actual pixels; this is a proxy based on what the clip's uploader
# tagged it as, not ground truth). Priority order matters: a clip tagged both "woman"
# and "screen" counts as faces/people first, since a visible person is the stronger
# visual signal. Read this data with that caveat, not as a precise classification.
FACE_KEYWORDS = {"man", "woman", "person", "people", "face", "portrait", "boy", "girl", "child", "kid", "hand", "hands"}
TEXT_SCREEN_KEYWORDS = {"text", "words", "typography", "sign", "screen", "computer", "chart", "graph", "map", "book"}
MOTION_KEYWORDS = {"motion", "action", "running", "flying", "explosion", "fast", "speed", "moving", "wave", "fire", "storm"}


def _first_clip_type_bucket(tags):
    if not tags:
        return None
    words = {w.strip().lower() for w in tags.split(",")}
    if words & FACE_KEYWORDS:
        return "faces/people"
    if words & TEXT_SCREEN_KEYWORDS:
        return "text/screen"
    if words & MOTION_KEYWORDS:
        return "motion/action"
    return "other/scenic"


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


# Composite per-video score, 0-100, for the dashboard's "how is this video doing"
# column. Weighted toward retention since that's the primary growth signal (see
# ROUTINE_INSTRUCTIONS.md's priority order), with subscriber conversion and
# engagement rate normalized against a ceiling and blended in. The ceilings
# (SUB_RATE_CEILING / ENG_RATE_CEILING) are initial guesses for "what a strong
# per-1k-views rate looks like," not measured benchmarks -- there isn't enough
# channel history yet to derive real ones. Revisit once there's a real
# distribution of videos to calibrate against; until then this is a deliberately
# transparent heuristic, not a black box.
SCORE_WEIGHTS = {"avg_view_pct": 0.6, "sub_rate": 0.25, "eng_rate": 0.15}
SUB_RATE_CEILING = 20.0   # subs per 1k views -> treated as a "perfect" 100 on this sub-score
ENG_RATE_CEILING = 200.0  # likes+comments per 1k views -> treated as a "perfect" 100 on this sub-score


def _normalize_to_100(value, ceiling):
    if not ceiling:
        return 0.0
    return max(0.0, min(100.0, value / ceiling * 100))


def score_video(v):
    """None if there's no Analytics data yet for this video (still within the
    24-48h reporting lag) -- a video shouldn't show a misleadingly low score just
    because it's too new to have real numbers."""
    if v.get("avg_view_pct") is None:
        return None
    views = v.get("views") or 0
    sub_rate = _rate_per_1k(v.get("subscribers_gained") or 0, views)
    eng_rate = _rate_per_1k((v.get("likes") or 0) + (v.get("comments") or 0), views)
    score = (
        SCORE_WEIGHTS["avg_view_pct"] * (v.get("avg_view_pct") or 0)
        + SCORE_WEIGHTS["sub_rate"] * _normalize_to_100(sub_rate, SUB_RATE_CEILING)
        + SCORE_WEIGHTS["eng_rate"] * _normalize_to_100(eng_rate, ENG_RATE_CEILING)
    )
    return round(min(100.0, score), 1)


def build_video_list(used_topics_path, performance, live_stats_path):
    """Full per-video table for the dashboard -- every uploaded video (from
    used_topics.json, the authoritative record of what's actually been published),
    not just ones old enough for Analytics to have settled. Layers in whichever
    stats are available: settled Analytics numbers where present, falling back to
    live_stats.json's near-real-time counts for videos too new for Analytics yet.
    score_pct stays None until real Analytics data exists -- a video shouldn't show
    a misleadingly low score just because it's a few hours old."""
    used = load_json(used_topics_path, {"topics": []})
    live = load_json(live_stats_path, {})
    perf_by_id = {v["video_id"]: v for v in performance["videos"]}

    videos = []
    for t in used["topics"]:
        video_id = t.get("video_id")
        if not video_id:
            continue
        analytics = perf_by_id.get(video_id)
        fallback = live.get(video_id, {})
        videos.append({
            "video_id": video_id,
            "url": f"https://www.youtube.com/shorts/{video_id}",
            "title": t.get("title"),
            "topic": t.get("topic"),
            "category": t.get("category"),
            "hook_type": t.get("hook_type"),
            "duration_seconds": t.get("duration_seconds"),
            "uploaded_at": t.get("uploaded_at"),
            "views": (analytics or {}).get("views", fallback.get("views")),
            "likes": (analytics or {}).get("likes", fallback.get("likes")),
            "comments": (analytics or {}).get("comments", fallback.get("comments")),
            "avg_view_pct": (analytics or {}).get("avg_view_pct"),
            "subscribers_gained": (analytics or {}).get("subscribers_gained"),
            "score_pct": score_video(analytics) if analytics else None,
            "stats_pending": analytics is None,
        })
    return sorted(videos, key=lambda v: v.get("uploaded_at") or "", reverse=True)


def channel_totals(video_list):
    scored = [v for v in video_list if v.get("avg_view_pct") is not None]
    return {
        "total_videos": len(video_list),
        "total_views": sum(v.get("views") or 0 for v in video_list),
        "total_subscribers_gained": sum(v.get("subscribers_gained") or 0 for v in video_list),
        "avg_view_pct": (sum(v["avg_view_pct"] for v in scored) / len(scored)) if scored else None,
        "avg_score_pct": (sum(v["score_pct"] for v in scored) / len(scored)) if scored else None,
    }


def summarize(performance):
    # Category, hook_type, length, publish hour, and seed momentum are the
    # generalizable signals -- unlike an exact topic, all of them repeat across videos,
    # so they're what's actually safe to steer future choices by. Per-topic detail is
    # kept too, but only as reference. Each bucket tracks four metrics: avg_view_pct
    # (retention), sub_rate_per_1k_views (does this convert viewers to subscribers --
    # directly relevant to the 1,000-subscriber monetization gate, not just the view
    # count gate), engagement_rate_per_1k_views (likes+comments -- an explicit
    # algorithm signal, distinct from passive watch time), and share_rate_per_1k_views
    # (does this actually leave the channel's existing audience -- distinct from all
    # three of the above, which only measure what people who already saw it did with
    # it, not whether it reached anyone new. Retention has consistently cleared well
    # while subscriber growth has lagged; shares are the more direct lever for that gap
    # than more retention tuning, since a video someone forwards reaches viewers the
    # algorithm/existing audience never would have surfaced it to on their own).
    dimensions = {
        "by_category": defaultdict(list),
        "by_hook_type": defaultdict(list),
        "by_length": defaultdict(list),
        "by_publish_hour": defaultdict(list),
        "by_seed_momentum": defaultdict(list),
        "by_topic": defaultdict(list),
        "by_ruleset_version": defaultdict(list),
        "by_first_clip_type": defaultdict(list),
        "by_holdout": defaultdict(list),
        "by_experiment_arm": defaultdict(list),
    }
    for v in performance["videos"]:
        views = v.get("views") or 0
        entry = {
            "avg_view_pct": v.get("avg_view_pct") or 0,
            "sub_rate": _rate_per_1k(v.get("subscribers_gained") or 0, views),
            "eng_rate": _rate_per_1k((v.get("likes") or 0) + (v.get("comments") or 0), views),
            "share_rate": _rate_per_1k(v.get("shares") or 0, views),
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
        if v.get("ruleset_version"):
            dimensions["by_ruleset_version"][v["ruleset_version"]].append(entry)
        first_clip_type = _first_clip_type_bucket(v.get("first_clip_tags"))
        if first_clip_type:
            dimensions["by_first_clip_type"][first_clip_type].append(entry)
        if v.get("holdout") is not None:
            dimensions["by_holdout"]["holdout (unsteered)" if v["holdout"] else "steered"].append(entry)
        if v.get("experiment_arm"):
            dimensions["by_experiment_arm"][v["experiment_arm"]].append(entry)

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
                "share_rate_per_1k_views": _avg("share_rate", entries),
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


def _iso_week(dt_str):
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def weekly_variance(video_list, weeks_back=8):
    """Median and max views per calendar week -- a ruleset that produces one 3,000-view
    video and nine 200s beats one that produces ten 500s, because only the outlier
    changes the channel's distribution/reach, not the median. Optimizing on averages
    alone can't see this and pushes toward reliable mediocrity (a flat, narrow
    distribution) instead of toward whatever produces real outliers. Track both so a
    ruleset decision doesn't get made on the wrong number."""
    by_week = defaultdict(list)
    for v in video_list:
        views, uploaded_at = v.get("views"), v.get("uploaded_at")
        if views is None or not uploaded_at:
            continue
        try:
            by_week[_iso_week(uploaded_at)].append(views)
        except ValueError:
            continue

    weeks = []
    for week, views_list in sorted(by_week.items()):
        views_list.sort()
        n = len(views_list)
        median = views_list[n // 2] if n % 2 == 1 else (views_list[n // 2 - 1] + views_list[n // 2]) / 2
        weeks.append({"week": week, "median_views": median, "max_views": max(views_list), "video_count": n})
    return weeks[-weeks_back:]


def render_summary_markdown(summary, recent, traffic_sources, weekly=None, beat_drops=None):
    lines = [
        "# Performance summary (auto-generated, read this before writing a script)",
        "",
        "**shares/1k views is the metric to prioritize right now.** Retention "
        "(avg_view_pct) is already clearing well across the board -- shares and "
        "subscriber growth are the actual gating metrics. A high-retention video that "
        "nobody forwards still doesn't grow the channel; shares are the more direct "
        "lever, since a forwarded video reaches people the algorithm/existing audience "
        "never would have. Every script now requires a `share_trigger` (see "
        "`pipeline/script_schema.py`) -- once there's enough data (n>=8 per bucket), "
        "weight topic/hook/style choices by share_rate_per_1k_views below, not just "
        "avg_view_pct.",
        "",
    ]

    if recent:
        lines.append("## Recent uploads (last 48h) -- early velocity signal, near-real-time (not the retention ranking below)")
        lines.append("")
        for r in recent:
            hook = r.get("hook_type", "n/a")
            velocity = f", {r['views_at_2h']} views@2h" if r.get("views_at_2h") is not None else ""
            lines.append(f"- {r['topic']} ({r.get('category', 'n/a')}/{hook}): {r.get('views', 0)} views, {r.get('likes', 0)} likes, {r.get('comments', 0)} comments{velocity}")
        lines.append("")

    if not summary["by_category"]:
        lines.append("No performance data yet -- not enough uploads/history to summarize.")
        return "\n".join(lines) + "\n"

    def _section(title, key, note="", preamble=""):
        rows = summary[key]
        if not rows:
            return
        lines.append(f"## {title}{note}")
        lines.append("")
        if preamble:
            lines.append(preamble)
            lines.append("")
        for r in rows:
            lines.append(
                f"- {r['name']}: {r['avg_view_pct']:.1f}% avg view, "
                f"{r['sub_rate_per_1k_views']:.2f} subs/1k views, "
                f"{r['engagement_rate_per_1k_views']:.2f} likes+comments/1k views, "
                f"{r['share_rate_per_1k_views']:.2f} shares/1k views (n={r['sample_size']})"
            )
        lines.append("")

    _section(
        "Performance by ruleset version -- READ THIS FIRST if more than one is listed",
        "by_ruleset_version",
        preamble=(
            "A newer version means a real pipeline/guidance change (captions, b-roll "
            "provider, hook/payoff rules, voice, motion, etc.), not just more time "
            "passing. Older versions can look artificially weak for reasons that have "
            "nothing to do with topic/category choice -- e.g. every video before "
            "2026-08-18-retention-overhaul-v3 had a caption-visibility bug that made "
            "captions unreadable regardless of topic. Weight the *current* version's "
            "own numbers most heavily once it has a real sample size (n>=5 or so); "
            "until then, older-version data still carries topic/category signal, but "
            "read its absolute retention numbers as a floor the current pipeline "
            "should beat, not a ceiling to match."
        ),
    )
    _section("Top categories (steer topic choice by this)", "by_category")
    _section("Top hook styles (steer how you open/close by this)", "by_hook_type")
    _section("Top video lengths (steer target narration length by this)", "by_length")
    _section("Top publish hours (UTC) -- reference only, needs more spread of upload times before it means anything", "by_publish_hour")
    _section("Top seed momentum tiers -- does a higher-view-count trend seed actually predict this channel's own performance?", "by_seed_momentum")
    _section(
        "Opening clip type (beat 0's first visual, proxied from Pixabay's own tags -- "
        "not real computer vision, read as a rough signal only)",
        "by_first_clip_type",
    )
    _section(
        "Steered vs. holdout -- is tuning actually beating an unsteered baseline?",
        "by_holdout",
    )
    _section(
        "Active experiment arm (control vs. variant) -- only meaningful while a VARIANT "
        "ARM block is active in ROUTINE_INSTRUCTIONS.md",
        "by_experiment_arm",
    )

    lines.append("## Top individual topics (reference only -- these exact topics are already used)")
    lines.append("")
    for r in summary["by_topic"]:
        lines.append(f"- {r['name']}: {r['avg_view_pct']:.1f}% avg view")
    lines.append("")

    if beat_drops:
        lines.append(
            "## Where viewers actually leave, beat by beat (per-beat retention drop, "
            "most actionable signal here)"
        )
        lines.append("")
        for d in beat_drops:
            lines.append(
                f"- -{d['drop_pct_points']:.1f} points at beat {d['beat_index']} "
                f"(\"{d['text']}\") in \"{d['topic']}\" ({d['start_pct']:.0f}% -> "
                f"{d['end_pct']:.0f}%)"
            )
        lines.append("")

    if weekly:
        lines.append(
            "## Weekly reach: median vs. max views -- a wide gap means the ceiling is "
            "real, not the median"
        )
        lines.append("")
        for w in weekly:
            lines.append(
                f"- {w['week']}: median {w['median_views']:.0f}, max {w['max_views']} "
                f"(n={w['video_count']} videos)"
            )
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
    parser.add_argument("--dashboard-out", default=None,
                         help="if set, write the full channel+per-video payload here for the "
                              "car-loan-dashboard youtube-status ingest step to POST")
    args = parser.parse_args()

    perf = update_performance_log(args.performance_log, args.used_topics)
    summary = summarize(perf)
    recent = recent_uploads(args.used_topics, args.live_stats)
    traffic = pull_traffic_sources()
    videos = build_video_list(args.used_topics, perf, args.live_stats)

    output = dict(summary)
    output["recent_uploads"] = recent
    output["traffic_sources"] = traffic
    print(json.dumps(output, indent=2))

    weekly = weekly_variance(videos)
    beat_drops = worst_beat_dropoffs(perf)

    if args.summary_out:
        Path(args.summary_out).write_text(
            render_summary_markdown(summary, recent, traffic, weekly, beat_drops), encoding="utf-8"
        )

    if args.dashboard_out:
        dashboard_payload = dict(summary)
        dashboard_payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        dashboard_payload["channel_totals"] = channel_totals(videos)
        dashboard_payload["recent_uploads"] = recent
        dashboard_payload["traffic_sources"] = traffic
        dashboard_payload["weekly_variance"] = weekly
        dashboard_payload["videos"] = videos
        Path(args.dashboard_out).write_text(json.dumps(dashboard_payload, indent=2), encoding="utf-8")
