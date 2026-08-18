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
  Your goal for this run is channel growth -- maximize views, watch-through, and
  subscriber conversion, not just "write something." Every ranking section below shows
  three numbers per entry, in this priority order when they disagree:
  1. `avg_view_pct` -- retention (do people watch the whole thing). Primary signal.
  2. `subs/1k views` -- does this actually convert viewers into subscribers. Weight
     this seriously: monetization needs 1,000 subscribers *and* the view threshold, not
     views alone, so a video that's merely average on retention but a strong subscriber
     converter is genuinely valuable, not just a consolation stat.
  3. `likes+comments/1k views` -- explicit engagement signal, distinct from passive
     watch time.
  Sections, in order of how much they should steer this run: **Top categories** and
  **Top hook styles** actively steer which candidate/angle and `hook_type` you pick.
  **Top video lengths** should nudge your target narration word count (you only control
  words, not seconds -- roughly 2.2 words/sec, so ~45 words for short/<=20s, ~45-90 for
  medium/20-40s, ~90-130 for long/40-58s). **Top publish hours** and **Top seed momentum
  tiers** are reference-only for now -- interesting once there's a real spread of data
  behind them, but you don't control posting time and seed selection is already
  constrained by what `trend_source.py` hands you this run, so don't force a choice
  based on either yet. **Top individual topics** is reference only, not repeatable.
  **Where views are coming from** (traffic sources, e.g. SHORTS/YT_SEARCH/SUBSCRIBER) is
  channel-wide context, not a per-video lever -- useful for understanding how dependent
  the channel is on the Shorts feed algorithm specifically, nothing to act on per script.
  Don't over-fit to a tiny sample anywhere in this file -- with only a handful of videos
  so far, differences are still mostly noise; treat every ranking as a lean, not a hard
  rule, until more data accumulates.
  A separate "Recent uploads (last 48h)" section, if present, is near-real-time (raw
  views/likes/comments, refreshed several times a day, not once/day) -- an early
  velocity/reach signal ("is the algorithm currently pushing this one"), not retention
  (`avg_view_pct` isn't available yet that early). Useful context, but don't treat one
  breakout video's early raw numbers as proof a whole category/hook_type is now the
  winner.
- `state/used_topics.json` -- the last ~40 topics already covered. This is the variety
  safety rail.

## 2. Write an original script -- this is your job, not a script's

Pick one candidate from `state/latest_trend_seed.json` and use it as inspiration only
-- write an **original** commentary/take. Do not summarize, transcribe, or closely
paraphrase the seed video -- riff on the topic, don't reuse the source. A candidate's
specific source video is loose inspiration, not a requirement -- you are not obligated
to make a video "about" it specifically.

### 2.1. Plan the hook first -- before you write anything else

The hook is the single highest-leverage decision in this entire run, not a formality to
get through before the "real" work of writing the script. Most Shorts drop-off happens
in the first couple seconds; a mediocre hook with a great script loses to a great hook
with a mediocre script, because almost nobody sees the rest if the opening doesn't land.
Treat this as its own deliberate planning step, done before you draft `beats`, not
something you back into by writing beat 0 as you go.

For your chosen topic, draft **at least 3 distinct hook options**, spanning **at least 2
different `hook_type`s** (`question`, `shocking_fact`, `myth_bust`, `list`, `story`,
`challenge`) -- forcing yourself across styles surfaces genuinely different angles
instead of three phrasings of the same idea. For each candidate, actually ask: does it
land the surprise/claim/question in one sentence with zero preamble? Would a stranger
mid-scroll stop for this specific line, not just "this topic in general"? Is it worded
distinctly from how the last several videos opened (check `state/used_topics.json`)?

Pick the strongest candidate. That's your `hook_type` and the basis for `beats[0]` --
light polish going from draft to final beat is fine, but it must genuinely be that
candidate, not something unrelated you wrote afterward. Record every candidate you
drafted (not just the winner) in `hook_candidates`, matching the shape in
`pipeline/script_schema.py`. This is enforced, not just advisory: `script_schema.py`
requires >=3 candidates spanning >=2 hook_types with the winning hook_type among them,
and `quality_gate.py` checks that `beats[0]` actually resembles the winning candidate
and that candidates aren't near-duplicates of each other.

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
  this exact source video from being reselected), `seed_view_count` (copied verbatim
  from that candidate's `view_count`, 0 if there was no source video), `title`
  (<=100 chars), `description`, `tags`
- `hook_type`: the winning candidate's hook_type from step 2.1 -- whichever actually
  matches how you wrote beat 0. Copy the label verbatim (don't invent a new one) so the
  performance-feedback loop can compare apples to apples across videos, same reasoning
  as `category`.
- `hook_candidates`: every hook option you drafted in step 2.1 (>=3, spanning >=2
  hook_types), each `{"hook_type": "...", "text": "..."}`.
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

**Structural variety matters, not just topic variety.** This pipeline is fully
automated and produces videos on a fixed cadence -- that alone puts it at risk of
looking like the kind of formulaic, mass-produced content YouTube's Partner Program
explicitly excludes from monetization regardless of view count. Guard against that
actively: don't let beat 0 fall into the same handful of opening phrasings run after
run (rotate genuinely between a blunt claim, a direct question, a "most people think X,
but..." reversal, a concrete number, etc. -- whichever fits `hook_type`, but vary the
actual wording), don't let every video's beat count or pacing feel identical, and don't
let the CTA in the closing beat repeat verbatim across videos. If you notice (from
`state/used_topics.json` or your own recent runs) that the last several videos all
opened the same way, treat that as a reason to deliberately open differently this time,
even if the top-performing hook_type says otherwise -- looking hand-crafted is worth
more here than micro-optimizing one metric.

Validate it before committing:

```
python pipeline/script_schema.py state/pending_script.json
```

## 3. Commit and push

**Push straight to `main` with an explicit refspec -- do not rely on plain `git push`.**
The sandbox may check you out onto an auto-provisioned `claude/*` branch rather than
`main`; a bare `git push` then happily pushes there instead, and `produce-upload.yml`
only triggers off pushes to `main`, so the script silently never gets produced even
though you report success. This has happened in production -- dozens of scripts were
stranded on unmerged branches before this rule was added.

```
git add state/pending_script.json
git commit -m "Script: <topic>"
git push origin HEAD:main
```

If that's rejected because your local main is behind (another workflow committed to
`state/` around the same time -- this happens routinely, not a sign of a real
problem), do NOT force-push or overwrite. Just:
```
git fetch origin main
git rebase origin/main
git push origin HEAD:main
```
Retry that fetch/rebase/push once or twice if needed.

If instead it's rejected for a **permission** reason (403, "protected branch", not a
fast-forward issue) -- that means direct pushes to `main` are blocked for this
session's token specifically. Fall back to a PR you merge yourself in the same run,
so the script still lands instead of stranding on a branch no one will look at:
```
git push origin HEAD:claude-script-$(date +%s)
gh pr create --base main --head <that-branch-name> --title "Script: <topic>" --body "Automated."
gh pr merge <that-branch-name> --squash --delete-branch
```
Only fall back to this if the direct push genuinely fails on a permission error, not
just a normal non-fast-forward rejection -- the direct-to-main path is strongly
preferred when it works, and this is the only bugfix that produced a `pending_script`
duplicate-write bug once before.

If both paths fail, say so plainly in your summary rather than forcing anything
through -- do not leave a half-merged or force-pushed `main`.

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
