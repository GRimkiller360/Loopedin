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

## 0.5. In-flight check -- do this before touching anything else

Check whether `state/pending_script.json` already exists. **If it does, STOP -- do not
write or overwrite it, do not commit anything.** Its existence means a previous script
hasn't been cleared yet, which means one of two things: `produce-upload.yml` is still
actively processing it (narration/b-roll/assembly/upload takes a few minutes), or a real
production failure left it there for a human to look at. Either way, overwriting it out
from under an in-flight or unresolved run causes an actual git conflict (a real
modify/delete conflict, not just a rejected push retryable by fetch+rebase) and silently
destroys whatever `produce-upload.yml` was doing with it -- this has happened in
production. Report in your summary that a script was already pending and end the run;
don't investigate further, that's out of scope per "On failure" below.

## 1. Read this run's inputs

- `state/latest_trend_seed.json` -- `{"candidates": [...]}`, up to 3 topic seeds
  (title/category/view-count signal each), fetched moments ago and already deduped
  against `used_topics.json` by source video ID. Never the source video's actual
  content. Pick whichever candidate gives the best distinct angle -- you don't have to
  use `candidates[0]`. Each `seed_category` is one of a fixed set (technology, science
  facts, life hacks, history, true crime mystery, personal finance, space, psychology,
  fitness, AI news) -- copy the one you pick verbatim into your script's `category`
  field (see step 2). Don't invent a new category string even if it feels more precise;
  the performance-feedback loop only works if categories stay consistent across videos.
  If the list is short (1-2 entries, or entries with `source_video_id: null`), that
  means nothing fresh turned up this run -- see step 2 for what to do about that.
- `state/performance_summary.md` -- refreshed once/day, timed right before the first
  fire of the day, so treat it as current for today (YouTube Analytics data itself
  only settles on a ~24-48h cycle, so it can't usefully be fresher than that anyway).
  Your goal for this run is channel growth: maximize views and watch-through/engagement
  (`avg_view_pct` is the engagement proxy here -- higher means people watch more of the
  short before dropping off), not just "write something." Let the *category* ranking
  actively steer which candidate and angle you pick -- prefer topics in categories that
  are outperforming. If present, also read the "Top hook styles" section and let it
  steer your `hook_type` choice (see step 2) the same way -- it's a second, independent
  lever on engagement, separate from topic category. The "by topic" section is
  reference only -- those exact topics are already used, so their number isn't
  repeatable. Don't over-fit to a tiny sample, though -- with only a handful of videos
  so far, differences are still mostly noise, so treat both rankings as a lean, not a
  hard rule, until more data accumulates.
- `state/used_topics.json` -- the last ~40 topics already covered. This is the variety
  safety rail.

## 2. Write an original script -- this is your job, not a script's

Pick one candidate from `state/latest_trend_seed.json` and use it as inspiration only
-- write an **original** commentary/take. Do not summarize, transcribe, or closely
paraphrase the seed video -- riff on the topic, don't reuse the source. A candidate's
specific source video is loose inspiration, not a requirement -- you are not obligated
to make a video "about" it specifically.

**If every candidate still seems too close to something in `used_topics.json` (same
underlying trend, published recently -- should be rare now that candidates are
pre-deduped by source video ID), do NOT skip the run.** Each `seed_category` is broad
(e.g. "life hacks" covers far more than one viral clip) -- pick a different specific
topic or angle within one of the candidates' categories (or a clearly related one)
that hasn't been covered, and write about that instead. Only skip entirely (see "On
failure" below) if you genuinely cannot find any distinct angle at all across every
candidate -- that should be very rare; treat it as a last resort, not the default
response to a duplicate seed.

Save it as `state/pending_script.json` matching the shape documented in
`pipeline/script_schema.py`:

- `topic`, `category` (copied verbatim from your chosen candidate's `seed_category`),
  `seed_source_video_id` (copied verbatim from that candidate's `source_video_id`,
  which may be `null` -- copy it either way, this is what lets a future run exclude
  this exact source video from being reselected), `title` (<=100 chars), `description`,
  `tags`
- `hook_type`: pick one of `question`, `shocking_fact`, `myth_bust`, `list`, `story`,
  `challenge` -- whichever actually matches how you wrote beat 0. Copy the label
  verbatim (don't invent a new one) so the performance-feedback loop can compare
  apples to apples across videos, same reasoning as `category`.
- `beats`: 3-12 entries, each `{"text": "...", "broll_query": "..."}`. Two retention
  rules that matter more than anything else here since most drop-off on Shorts happens
  in the first couple seconds: (1) beat 0 must land the hook itself immediately --
  the surprising claim, question, or premise -- no throat-clearing preamble like "so
  today we're talking about" before it. (2) the last beat should close with a light,
  natural call-to-action (e.g. inviting a comment with their own take, or a reason to
  follow for more) -- comments/likes/follows are engagement signals, not just a nicety.
  Don't force an identical CTA phrasing every time; vary it so it doesn't read as
  copy-pasted spam.
- title: prefer a genuine curiosity gap or a concrete number/claim over a generic
  label, but it must accurately reflect what the video actually delivers -- a
  clickbait/content mismatch tanks retention and hurts future recommendation, which
  works directly against the growth goal.
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

This should be rare -- per step 2, a duplicate seed alone is not a reason to skip,
since the category is broad enough to find a different angle. Only skip, without
committing anything, if the seed data itself looks malformed/unusable, or you've
genuinely tried and cannot find any distinct angle within the category at all. If you
do skip, explain why concretely in your final summary (not just "seed was similar") --
what angles you actually considered and why none worked. The health/pause tracking for
actual production failures (TTS, upload, etc.) is handled by `produce-upload.yml`
itself, not by you.
