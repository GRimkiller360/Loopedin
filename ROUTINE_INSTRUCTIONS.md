# Routine runbook

<!-- PROTECTED-SECTION markers below bound content the "Loopedin Self-Improvement"
cloud routine (runs periodically, reads real performance data, edits this file) must
NEVER touch, regardless of what performance data seems to suggest -- these are hard
legal/policy/trust constraints, not tunable creative choices. Everything outside
PROTECTED-SECTION blocks is fair game for evidence-backed edits. See
state/ruleset_changelog.json for a log of every change that routine has made and why. -->

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
  use `candidates[0]`. Each `seed_category` is one of a fixed set (**science facts,
  psychology, space, history** -- deliberately narrowed from a broader original list
  to build a coherent channel identity; see `pipeline/script_schema.py`'s `CATEGORIES`
  comment for the full reasoning) -- copy the one you pick verbatim into your script's
  `category` field (see step 2). Don't invent a new category string even if it feels
  more precise; the performance-feedback loop only works if categories stay consistent
  across videos.
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
  medium/20-40s, ~90-130 for long/40-58s). **If the short/<=20s bucket has no entries
  (n=0) in this file, deliberately target it this run instead of defaulting to
  medium/long** -- an option with zero data isn't "unproven," it's untested, and Shorts
  specifically reward loop-rewatches (a 15s video watched twice reads as 100%+
  retention), which this channel has never actually tried. Once short has a real
  sample size to compare against medium/long, go back to letting the data decide
  normally. Independent of this channel's own data, external research on Shorts
  retention converges on **15-30s as the general sweet spot** (enough room for a real
  micro-story, short enough that patience never runs out) -- treat that as a mild prior
  when this channel's own numbers don't yet clearly favor a different length, not as a
  rule that overrides real data once there's enough of it. **Top publish hours** and
  **Top seed momentum
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

<!-- PROTECTED-SECTION: START -->
Pick one candidate from `state/latest_trend_seed.json` and use it as inspiration only
-- write an **original** commentary/take. Do not summarize, transcribe, or closely
paraphrase the seed video -- riff on the topic, don't reuse the source. A candidate's
specific source video is loose inspiration, not a requirement -- you are not obligated
to make a video "about" it specifically.
<!-- PROTECTED-SECTION: END -->

**Before committing to an angle, apply a genuine value bar, separate from hook
craft.** A perfectly executed hook/payoff/pacing still fails if the underlying fact
isn't actually worth knowing -- execution can't rescue a boring claim. Ask: would you
personally stop and tell a friend this, unprompted? Would it surprise someone who
already has a passing interest in this category, not just someone with zero
knowledge? If the honest answer is "it's mildly interesting but not really surprising
or useful," pick a different candidate or a different specific angle within the same
category -- don't force a weak fact through strong packaging. This is a filter on
*which* angle to commit to, applied before hook-planning below, not a substitute for
the hook-planning process itself.

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

**A generic pattern-observation is not the same as "no preamble," and it performs like
preamble anyway.** Real retention data from this channel makes this concrete: two
videos about phones opened almost identically --
"Every year a new flagship drops, and every year someone says it's a downgrade"
(29.7% avg view) vs.
"Every year, a tech company promises their newest phone is the best one yet"
(72.0% avg view, because beat 1 immediately punctures that promise with the actual
complaint). Both are technically preamble-free. The failing one restates a sentiment
the viewer already holds -- nothing to resolve, no reason to keep watching. The best
hook in this channel's history skips setup entirely and states the counter-intuitive
fact itself: "Stock markets spend more days near all-time highs than they do in a
dip" (97.8% avg view) -- no scene-setting, the claim *is* the first sentence. When
drafting `hook_candidates`, actively reject any option that a viewer would already
agree with before you finish saying it; that's the tell for a pattern-observation
dressed up as a hook, not a real one.

**When picking the winner, specificity beats genre-fit.** Don't default to whichever
candidate matches the top-performing `hook_type` in `performance_summary.md` if a
more concrete option is sitting right there in your own `hook_candidates` list -- a
real named study, an exact number, or a specific unexpected mechanism will
out-perform a generic phrasing of the same idea even if the generic one happens to be
worded as a `shocking_fact`. Case in point from this channel: a gym-anxiety script
drafted four hook candidates, one of which was "A psychologist once sent students
into a room wearing an embarrassing t-shirt, certain everyone would notice -- almost
no one did" (a real, specific experiment) -- and the run picked the generic
"Almost nobody at the gym is watching you" instead, because that one was tagged
`shocking_fact` and shocking_fact was the top-ranked style. That's backwards: the
t-shirt study is inherently harder to have already heard, gives the viewer a concrete
image, and still delivers the exact same underlying insight. `hook_type` tells you
*how* to say something; it should never override *which* option actually says
something less generic. When two candidates make the same underlying point, the one
with a specific, checkable detail wins, regardless of which `hook_type` label either
one carries.

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
(e.g. "psychology" covers far more than one viral clip) -- pick a different specific
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
- `payoff_mechanism`: one sentence, >=20 words, stating the real causal reason behind
  this video's claim -- written before the beats, and its content must actually appear
  in `beats[1:]`. See the payoff rule (item 3) below for why this exists and what
  "real mechanism" vs. "metaphor" actually means in practice.
- `beats`: 3-12 entries, each `{"text": "...", "broll_query": "..."}`. Every
  `broll_query` must be a **short, literal keyword phrase -- 3-6 concrete nouns/
  adjectives, not a cinematic sentence** (e.g. `"person wearing black mask"`, not
  `"close-up hand slowly putting on a plain black mask, dramatic side lighting, slow
  motion"`). This is a hard technical constraint, not a style preference: the b-roll
  provider (Pixabay) does simple keyword-OR matching across the whole query string with
  no scene understanding, so a long descriptive sentence dilutes the match and returns
  unrelated footage matched on a single stray word (verified in production: a mask
  query worded as a full sentence returned an ocean wave, a tiger, and a CPU socket
  instead of anything mask-related; the same concept as a short phrase returned
  entirely on-topic results). Keep every beat's query this short, not just beat 0.
  Before drafting beats, pick a deliberate **story shape** for this topic rather than
  defaulting to a flat list of facts -- research on faceless/narrated short-form
  consistently finds the gap is story structure, not editing polish. Choose whichever
  genuinely fits: **curiosity-gap** (open a specific unanswered question, resolve it at
  the end), **problem-solution** (a real friction/mistake, then the fix), or
  **mystery-reveal** (withhold one concrete detail from beat 0, deliver it as the
  payoff). Whichever shape, beat 0 must *open* a specific, nameable gap -- not vague
  intrigue -- so there's something concrete left to close.

  Retention rules that matter more than anything else here:
  1. **Beat 0 must land the hook itself immediately, within the first ~2-3 seconds of
     spoken narration (roughly the first 6-8 words at this channel's ~2.2 words/sec
     pace)** -- the surprising claim, question, or premise, with zero throat-clearing
     preamble like "so today we're talking about." This isn't a vibe, it's a real
     documented threshold -- short-form attention spans have compressed to ~2 seconds,
     and hooks under 2 seconds measurably outperform longer intros. Never open on
     setup, context, or scene-framing; open on the payoff, the claim itself, or the
     single most visually/conceptually striking moment of the whole topic.
  2. **Beat 0's `broll_query` needs the same bar as its text, within the short-phrase
     constraint above.** Every other beat can use a straightforwardly descriptive
     query, but a generic/calm stock clip on beat 0 undercuts a strong hook -- the
     viewer processes the visual before they've processed a single word of narration.
     Pick the 3-6 keywords that point at something visually arresting or surprising
     matching the claim (motion, an unexpected image, the specific concrete thing the
     hook is about) rather than a generic establishing shot of the general topic.
  3. **The closing beat must actually deliver the payoff the hook promised, not just
     conclude.** This is distinct from the loop-back rule below -- payoff means the
     specific gap/question/tension opened in beat 0 gets a real, concrete answer or
     resolution by the end (the mechanism, the number, the twist -- whatever beat 0
     implicitly promised). A video that ends on a vague summary or a restated claim
     without actually resolving what beat 0 opened will lose viewers right before the
     end, which is the single worst place to lose them -- they were one beat away from
     a satisfying close. If you can't point to the exact sentence that pays off beat
     0's specific promise, the script isn't done.
     **A metaphor or poetic restatement of the phenomenon is NOT a payoff, even though
     it can look like one.** Real, evidence-backed failure from this channel: a
     Zeigarnik-effect video hooked on "why do you remember an unpaid bill perfectly,
     but forget it the moment it's paid?" (a genuine why-question), named the effect
     and re-described the same observation in beat 1, then closed with "your brain is
     refusing to close the tab -- what's still open in yours?" That final line *sounds*
     like a payoff (it's vivid, it callbacks) but never actually answers *why* --
     it restates the phenomenon in different words instead of explaining the mechanism
     behind it. A real payoff for that exact hook would name the actual reason (e.g.
     unfinished tasks stay loaded in working memory as an active, unresolved goal,
     which is *why* closing them relieves the mental tension) -- something a viewer
     couldn't have already guessed from the hook alone. Before finalizing, ask: does
     the closing beat teach the viewer something they didn't already know from beat 0,
     or does it just say the same thing more evocatively? Only the former is a payoff.
     **This rule failed to hold as prose alone -- it's now also enforced structurally.**
     Despite the Zeigarnik example above already being documented, a later video (the
     gravity-tunnel-through-Earth script) made the identical mistake: its entire
     explanation was "gravity inside pulls like a spring, not a straight drop" -- a
     metaphor asserted with zero elaboration on *why* that produces equal travel times
     regardless of tunnel length. Advisory text alone clearly isn't sufficient to
     prevent this pattern from recurring. `script_schema.py` now requires a top-level
     `payoff_mechanism` field: one sentence, **>=20 words**, stating the actual causal
     reason in plain language, written *before* you draft the beats (same reasoning as
     `hook_candidates` in step 2.1 -- force the real content to exist before it gets
     compressed). `quality_gate.py` then checks that `payoff_mechanism`'s content
     actually resembles something in `beats[1:]` -- it can't just sit in the file
     unused while a beat quietly reverts to metaphor. If you genuinely cannot state the
     mechanism in >=20 real words, that is itself a signal the angle was picked before
     you understood it well enough to explain it -- go back and either research the
     actual reason or pick a different angle, don't pad with filler to clear the count.
     **If a real mechanism needs more room than an ultra-short video allows, let the
     video run longer (up to the existing ~58s/130-word cap) rather than compressing
     the explanation into an assertion** -- a slightly longer video that actually makes
     sense beats a shorter one that doesn't; the 15-30s prior in step 1 is a mild lean
     for topics that fit it naturally, not a ceiling that justifies cutting the
     explanation itself.
  4. **The closing beat should *also* loop back to the opening, not just deliver the
     payoff and stop.** Shorts reward rewatches specifically -- someone who watches a
     15-second video twice because the ending sends them back to the start reads as
     retention over 100%, which is a stronger algorithmic signal than a single
     high-retention watch. Write the last beat so it calls back to a specific word,
     image, or claim from beat 0 (a twist on it, a callback phrase, an answer that
     recontextualizes the opening question) so replaying from the top feels rewarding,
     not repetitive. This is concrete, not just "make it good" -- if you can't point to
     which specific word or image in beat 0 the ending calls back to, it isn't looping
     yet. Payoff and loop-back should coexist in the same closing beat, not compete for
     space -- the payoff line often *is* the callback, recontextualized.
  5. **The last beat should also close with a light, natural call-to-action -- and a
     subscribe/follow ask specifically needs to show up far more than it has been.**
     Real evidence from this channel: across the last 9 videos, 8 closed with a
     comment-inviting CTA ("what's yours?", "comment below", "sound familiar?") and
     only 1 actually asked people to follow/subscribe -- and subscriber conversion has
     been flat at 0.00/1k views in every category the whole time. That's not a
     coincidence to keep repeating: a comment ask and a subscribe ask are not
     interchangeable, and defaulting to "comment" almost every time means the video is
     essentially never asking for the thing that actually grows the channel. **Aim for
     genuinely mixing both across videos, not picking whichever feels more natural for
     this specific topic every time** -- roughly half of videos should close with an
     explicit subscribe/follow nudge (varied phrasing: "follow for more [topic]
     breakdowns," "there's a new one of these every few hours, follow if you want the
     next one," "follow before you forget this," etc.), not just a comment prompt.
     This can still coexist with the loop-back (a callback line immediately followed
     by the CTA). Don't force identical CTA phrasing every time either way; vary it so
     it doesn't read as copy-pasted spam.
  6. **Before finalizing, check the middle, not just the opener -- each beat is a
     micro-reveal, not a restatement.** A strong hook still loses viewers if beats 1
     through N-1 sag; re-read beats 1 through the second-to-last and ask: does each one
     add a genuinely new specific detail that moves the story shape forward (per the
     curiosity-gap/problem-solution/mystery-reveal choice above), or does any beat just
     restate/pad what the previous one already said? A beat that doesn't earn its place
     (no new information, no rising tension) is a place viewers drop off even after a
     great hook. Cut or rewrite any beat that fails this check rather than leaving it in
     to hit a word-count target.
  7. **Keep the hook's vivid, plain-spoken register through the explanation beats, not
     just the opener.** Real evidence from this channel: a color-vision video hooked
     with "your eye is **wired** to slam them together" (vivid, punchy), then beat 1
     shifted into "they code color as **opposing pairs**, red versus green, blue
     versus yellow" -- textbook-lecture phrasing. The average viewer watched almost
     exactly to the end of that beat and no further, dropping off right before the
     payoff beat that followed. A register shift from "someone telling you something
     wild" to "a textbook explaining a mechanism" is itself a place viewers leave,
     independent of whether the beat contains new information. When writing an
     explanation beat, keep using the same kind of concrete, spoken-aloud phrasing as
     the hook -- if a beat reads like it belongs in a textbook rather than something
     you'd say out loud to a friend, rewrite it in the hook's voice before moving on,
     don't just check that the *content* is new (rule 6 above already covers content;
     this is about *how it's said*, a separate failure mode).
  8. **Mark the single most load-bearing word/number per beat with `**double
     asterisks**`** for caption emphasis (bold highlight color + size bump when
     burned in -- see `pipeline/script_schema.py`). At most 1-2 marked words per
     beat, and only the specific number/claim/twist that beat exists to deliver --
     marking everything makes nothing stand out, which defeats the purpose. Not
     every beat needs one; a transitional beat with no single standout word doesn't
     need forced emphasis.
- title: prefer a genuine curiosity gap or a concrete number/claim over a generic
  label, but <!-- PROTECTED-SECTION: START -->it must accurately reflect what the
  video actually delivers, full stop -- never loosen this even if data seems to show
  a mismatched/exaggerated title getting more clicks. A clickbait/content mismatch is
  a policy and trust problem, not just a retention one; it is off-limits to the
  self-improvement routine as an optimization lever.<!-- PROTECTED-SECTION: END -->
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
