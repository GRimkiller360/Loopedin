"""Pull performance stats for previously uploaded shorts and summarize what's working,
so the agent can weight future topic/style choices toward it. Run once/day, timed
right before the first routine fire of the day (see analytics-feedback.yml) --
YouTube Analytics data itself only settles on a ~24-48h cycle, so querying more often
than daily would just re-fetch unchanged numbers. Near-real-time early-velocity data
comes from a separate, faster-updating source -- see live_stats.py."""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config
from pipeline.script_schema import CATEGORIES
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


# Composite per-video score, 0-100, for the dashboard's "how is this video doing"
# column. share_rate is weighted alongside retention now -- shares are the channel's
# actual growth bottleneck (the one lever that reaches people the existing audience
# and algorithm never would have surfaced the video to on their own), not just a
# secondary engagement stat, so it's no longer folded into eng_rate's weight. The
# ceilings (SUB_RATE_CEILING / ENG_RATE_CEILING / SHARE_RATE_CEILING) are initial
# guesses for "what a strong per-1k-views rate looks like," not measured benchmarks --
# there isn't enough channel history yet to derive real ones. Revisit once there's a
# real distribution of videos to calibrate against; until then this is a deliberately
# transparent heuristic, not a black box.
SCORE_WEIGHTS = {"avg_view_pct": 0.4, "share_rate": 0.25, "sub_rate": 0.2, "eng_rate": 0.15}
SUB_RATE_CEILING = 20.0    # subs per 1k views -> treated as a "perfect" 100 on this sub-score
ENG_RATE_CEILING = 200.0   # likes+comments per 1k views -> treated as a "perfect" 100 on this sub-score
SHARE_RATE_CEILING = 5.0   # shares per 1k views -> treated as a "perfect" 100 on this sub-score


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
    share_rate = _rate_per_1k(v.get("shares") or 0, views)
    score = (
        SCORE_WEIGHTS["avg_view_pct"] * (v.get("avg_view_pct") or 0)
        + SCORE_WEIGHTS["share_rate"] * _normalize_to_100(share_rate, SHARE_RATE_CEILING)
        + SCORE_WEIGHTS["sub_rate"] * _normalize_to_100(sub_rate, SUB_RATE_CEILING)
        + SCORE_WEIGHTS["eng_rate"] * _normalize_to_100(eng_rate, ENG_RATE_CEILING)
    )
    return round(min(100.0, score), 1)


# Simple, mechanically-computable title-level signals -- not semantic understanding,
# just regex-detectable proxies for the kind of pattern a human already found once by
# hand (the human-stakes-angle finding in ROUTINE_INSTRUCTIONS.md: a named-person
# angle out-reached an abstract-mechanism one by ~1.7x, discovered by someone looking
# at the data once). topic_signals() below automates *looking* for more patterns like
# that on every run instead of relying on a person noticing again. Deliberately
# simple/regex over clever/NLP -- a wrong mechanical signal is at least auditable from
# its own detector function; a black-box one isn't. Extend this dict with more
# detectors as ideas come up; each one is checked independently.
TOPIC_SIGNALS = {
    "named person/place (possessive)": lambda title: bool(re.search(r"[A-Za-z][\w'-]*'s\b", title or "")),
    "contains a specific number": lambda title: bool(re.search(r"\d", title or "")),
    "phrased as a question": lambda title: (title or "").rstrip().endswith("?"),
    "title over 8 words": lambda title: len((title or "").split()) > 8,
}
# Each side of a signal split (has it / doesn't) needs at least this many scored
# videos before a gap is reported at all -- below this, noise dominates and any
# "pattern" is just which few videos happened to land there.
TOPIC_SIGNAL_MIN_GROUP_SIZE = 4
# Minimum gap in score_video() points (0-100 scale) between the two sides before it's
# worth a human's attention -- small gaps at small sample sizes are indistinguishable
# from chance.
TOPIC_SIGNAL_MIN_GAP = 12.0


def topic_signals(performance):
    """Compares scored videos with vs. without each TOPIC_SIGNALS detector, using the
    same composite score_video() the dashboard already ranks by, and returns any gap
    large enough (both thresholds above) to plausibly matter -- ranked by gap size,
    largest first. Returns [] if nothing clears the bar yet; that's a correct, expected
    result while sample sizes are still small, not a sign anything is broken."""
    scored = []
    for v in performance["videos"]:
        score = score_video(v)
        if score is not None:
            scored.append((v.get("title") or v.get("topic") or "", score))

    results = []
    for name, detector in TOPIC_SIGNALS.items():
        has = [score for title, score in scored if detector(title)]
        hasnt = [score for title, score in scored if not detector(title)]
        if len(has) < TOPIC_SIGNAL_MIN_GROUP_SIZE or len(hasnt) < TOPIC_SIGNAL_MIN_GROUP_SIZE:
            continue
        avg_has = sum(has) / len(has)
        avg_hasnt = sum(hasnt) / len(hasnt)
        gap = avg_has - avg_hasnt
        if abs(gap) < TOPIC_SIGNAL_MIN_GAP:
            continue
        results.append({
            "signal": name,
            "avg_score_with": round(avg_has, 1),
            "avg_score_without": round(avg_hasnt, 1),
            "gap": round(gap, 1),
            "n_with": len(has),
            "n_without": len(hasnt),
        })
    results.sort(key=lambda r: abs(r["gap"]), reverse=True)
    return results


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
            "shares": (analytics or {}).get("shares"),
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
        "total_shares": sum(v.get("shares") or 0 for v in video_list),
        "avg_view_pct": (sum(v["avg_view_pct"] for v in scored) / len(scored)) if scored else None,
        "avg_score_pct": (sum(v["score_pct"] for v in scored) / len(scored)) if scored else None,
    }


def summarize(performance):
    # Category, hook_type, length, publish hour, and seed momentum are the
    # generalizable signals -- unlike an exact topic, all of them repeat across videos,
    # so they're what's actually safe to steer future choices by. Per-topic detail is
    # kept too, but only as reference. Each bucket tracks four metrics, share_rate_per_1k
    # first: shares/1k views (does this actually leave the existing audience and reach
    # someone new -- the channel's real growth bottleneck), avg_view_pct (retention),
    # sub_rate_per_1k_views (does this convert viewers to subscribers -- directly
    # relevant to the 1,000-subscriber monetization gate, not just the view count gate),
    # and engagement_rate_per_1k_views (likes+comments -- an explicit algorithm signal,
    # distinct from passive watch time). Rankings below are sorted by share_rate, not
    # avg_view_pct, since a well-retained video that nobody forwards still doesn't grow
    # the channel -- see ROUTINE_INSTRUCTIONS.md's priority order.
    dimensions = {
        "by_category": defaultdict(list),
        "by_hook_type": defaultdict(list),
        "by_length": defaultdict(list),
        "by_publish_hour": defaultdict(list),
        "by_seed_momentum": defaultdict(list),
        "by_topic": defaultdict(list),
        "by_ruleset_version": defaultdict(list),
    }
    for v in performance["videos"]:
        views = v.get("views") or 0
        entry = {
            "avg_view_pct": v.get("avg_view_pct") or 0,
            "share_rate": _rate_per_1k(v.get("shares") or 0, views),
            "sub_rate": _rate_per_1k(v.get("subscribers_gained") or 0, views),
            "eng_rate": _rate_per_1k((v.get("likes") or 0) + (v.get("comments") or 0), views),
        }
        # Only categories still in CATEGORIES (pipeline/script_schema.py) are actually
        # choosable by a future script -- a category the channel used to make (e.g.
        # "space", dropped 2026-08-21 when narrowed to history-only) can have real,
        # still-live videos with real Analytics data, but showing it in a section
        # titled "steer topic choice by this" is misleading when it was never a choice
        # to begin with. Excluded from ranking here, not deleted from
        # performance_log.json -- the underlying video data stays as historical record.
        if v.get("category") in CATEGORIES:
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

    def _avg(key, entries):
        return sum(e[key] for e in entries) / len(entries)

    def _rank(bucket, limit):
        # Secondary key matters in practice: share_rate is 0.000 for every currently-
        # active category right now (shares are still too rare at this channel's size
        # to have landed on science facts/space/history yet), so a share_rate-only sort
        # left them in arbitrary dict-insertion order -- history's actual ~4x retention
        # lead over space/science facts was invisible in this ranking even though the
        # raw numbers already showed it. avg_view_pct as tiebreak surfaces that signal
        # instead of hiding it behind a three-way tie. Added 2026-08-21.
        ranked = sorted(
            bucket.items(),
            key=lambda kv: (_avg("share_rate", kv[1]), _avg("avg_view_pct", kv[1])),
            reverse=True,
        )
        return [
            {
                "name": name,
                "share_rate_per_1k_views": _avg("share_rate", entries),
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


def render_summary_markdown(summary, recent, traffic_sources, topic_signal_results=None):
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
                f"- {r['name']}: {r['share_rate_per_1k_views']:.3f} shares/1k views, "
                f"{r['avg_view_pct']:.1f}% avg view, "
                f"{r['sub_rate_per_1k_views']:.2f} subs/1k views, "
                f"{r['engagement_rate_per_1k_views']:.2f} likes+comments/1k views (n={r['sample_size']})"
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

    lines.append("## Topic-title patterns (auto-detected, steer topic choice by this)")
    lines.append("")
    lines.append(
        "Mechanical title-level signals only, not semantic understanding -- each line "
        "compares scored videos that have a signal against ones that don't, using the "
        "same composite score `score_video()` already computes for the dashboard. Only "
        "shown when both sides have a real sample "
        f"(n>={TOPIC_SIGNAL_MIN_GROUP_SIZE} each) and a real gap "
        f"(>={TOPIC_SIGNAL_MIN_GAP:.0f} score points) -- read as a lean worth trying "
        "when picking between otherwise-similar angles, not a rule that overrides "
        "genuine topic quality."
    )
    lines.append("")
    if topic_signal_results:
        for r in topic_signal_results:
            lines.append(
                f"- {r['signal']}: {r['avg_score_with']:.1f} avg score WITH "
                f"(n={r['n_with']}) vs. {r['avg_score_without']:.1f} WITHOUT "
                f"(n={r['n_without']}) -- gap {r['gap']:+.1f}"
            )
    else:
        lines.append(
            "No signal has cleared both the sample-size and gap thresholds yet -- "
            "expected while the channel still has few scored videos, not a sign "
            "anything is broken. Check back as more videos publish."
        )
    lines.append("")

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

    if args.summary_out:
        signals = topic_signals(perf)
        Path(args.summary_out).write_text(render_summary_markdown(summary, recent, traffic, signals), encoding="utf-8")

    if args.dashboard_out:
        dashboard_payload = dict(summary)
        dashboard_payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        dashboard_payload["channel_totals"] = channel_totals(videos)
        dashboard_payload["recent_uploads"] = recent
        dashboard_payload["traffic_sources"] = traffic
        dashboard_payload["videos"] = videos
        Path(args.dashboard_out).write_text(json.dumps(dashboard_payload, indent=2), encoding="utf-8")
