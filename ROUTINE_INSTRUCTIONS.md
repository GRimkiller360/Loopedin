# Routine runbook

You are running one unattended fire of the Loopedin channel automation. No human
reviews this before it goes live. Your job here is narrow and deliberately does not
touch any credentials: a GitHub Actions workflow (`trend-fetch.yml`) already ran ~10
minutes before you and committed a fresh trend seed; another workflow
(`produce-upload.yml`) will pick up whatever you write here and do the actual
TTS/b-roll/assembly/upload using GitHub's own encrypted repo secrets. You never see or
need any API key.

## 0. Health check

Read `state/routine_health.json`. If `paused` is true, STOP immediately -- do not write
a script, do not commit anything. Report the pause reason in your final summary and end
the run. A human needs to reset it manually.

## 1. Read this run's inputs

- `state/latest_trend_seed.json` -- a topic seed only (title/category/view-count
  signal), fetched moments ago. Never the source video's actual content. Its
  `seed_category` field is one of a fixed set (technology, science facts, life hacks,
  history, true crime mystery, personal finance, space, psychology, fitness, AI news)
  -- copy it verbatim into your script's `category` field (see step 2). Don't invent a
  new category string even if it feels more precise; the performance-feedback loop
  only works if categories stay consistent across videos.
- `state/performance_summary.md` -- if present, which *categories* have performed
  best on this channel so far (the "by topic" section is reference only -- those exact
  topics are already used, so their number isn't repeatable). Let the category ranking
  steer your choice, but don't over-fit to a small sample -- with only a handful of
  videos so far, differences are mostly noise.
- `state/used_topics.json` -- the last ~40 topics already covered. This is the variety
  safety rail -- if today's seed is too close to something recent, pick a different
  angle before writing anything.

## 2. Write an original script -- this is your job, not a script's

Using the trend seed as inspiration only, write an **original** commentary/take. Do
not summarize, transcribe, or closely paraphrase the seed video -- riff on the topic,
don't reuse the source. Save it as `state/pending_script.json` matching the shape
documented in `pipeline/script_schema.py`:

- `topic`, `category` (copied verbatim from `trend_seed["seed_category"]`),
  `seed_source_video_id` (copied verbatim from `trend_seed["source_video_id"]`, which
  may be `null` -- copy it either way, this is what lets a future run exclude this
  exact source video from being reselected), `title` (<=100 chars), `description`,
  `tags`
- `beats`: 3-12 entries, each `{"text": "...", "broll_query": "..."}`
- keep total narration under ~130 words so the final video stays under 58s

Validate it before committing:

```
python pipeline/script_schema.py state/pending_script.json
```

## 3. Commit and push

```
git add state/pending_script.json
git commit -m "Script: <topic>"
git push
```

If `git push` is rejected (another workflow committed to `state/` around the same
time -- this happens routinely, not a sign of a real problem), do NOT force-push or
overwrite. Just:
```
git fetch origin main
git rebase origin/main
git push
```
Retry that fetch/rebase/push once or twice if needed. If it still won't push after a
few tries, say so plainly in your summary rather than forcing it through.

That's it -- pushing this file is what triggers `produce-upload.yml`, which handles
narration, b-roll, assembly, upload, and recording the result (including clearing
`state/pending_script.json` once done). You won't see the outcome in this session; if
you want to sanity-check a previous run's result, look at `state/used_topics.json` and
`state/routine_health.json` as they stood at the start of this run (step 0-1).

## On failure

If you can't produce a valid script for this run (e.g. every seed candidate is too
close to something recent, or the seed data looks malformed), do not commit anything
malformed -- just explain why in your final summary and end the run. The health/pause
tracking for actual production failures (TTS, upload, etc.) is handled by
`produce-upload.yml` itself, not by you.
