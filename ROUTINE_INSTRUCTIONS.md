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
write or overwrite it, do not commit anything.** Its existence should mean only one
thing now: `produce-upload.yml` is still actively processing it (narration/b-roll/
assembly/upload takes a few minutes). Every failure path (quality-gate/validation
failure, quota exhaustion, an outright crash) clears this file itself before the run
ends, so a stale one sitting here for more than a few minutes past a scheduled fire
means something's actually stuck, not a routine in-progress state -- that's worth
noting in your summary, but still don't overwrite it: overwriting it out from under a
genuinely in-flight run causes an actual git conflict (a real modify/delete conflict,
not just a rejected push retryable by fetch+rebase) and silently destroys whatever
`produce-upload.yml` was doing with it -- this has happened in production. Report in
your summary that a script was already pending and end the run; don't investigate
further, that's out of scope per "On failure" below.

## 1. Read this run's inputs

- `state/latest_trend_seed.json` -- `{"candidates": [...]}`, up to 3 topic seeds
  (title/category/view-count signal each), fetched moments ago and already deduped
  against `used_topics.json` by source video ID. Never the source video's actual
  content. Pick whichever candidate gives the best distinct angle -- you don't have to
  use `candidates[0]`. Each `seed_category` is one of a fixed set (**history alone**
  as of 2026-08-21, narrowed down from a wider original list in stages -- see
  `pipeline/script_schema.py`'s `CATEGORIES` comment for the full reasoning) -- copy
  the one you pick verbatim into your script's `category` field (see step 2). Don't
  invent a new category string even if it feels more precise; the performance-feedback
  loop only works if categories stay consistent across videos.
  If the list is short (1-2 entries, or entries with `source_video_id: null`), that
  means nothing fresh turned up this run -- see step 2 for what to do about that.
- `state/performance_summary.md` -- refreshed once/day, timed right before the first
  fire of the day, so treat it as current for today (YouTube Analytics data itself
  only settles on a ~24-48h cycle, so it can't usefully be fresher than that anyway).
  Your goal for this run is channel growth -- maximize reach, watch-through, and
  subscriber conversion, not just "write something." Every ranking section below shows
  four numbers per entry, in this priority order when they disagree:
  1. `shares/1k views` -- **primary signal.** Does this video actually leave the
     channel's existing audience and reach someone new -- the one lever that reaches
     people the algorithm and the existing subscriber base never would have surfaced it
     to on their own. This is what `share_trigger` (step 2, before the hook) is written
     to move.
  2. `avg_view_pct` -- retention (do people watch the whole thing). Still worth watching
     as a floor (a video nobody finishes can't be shared either), but not the primary
     target -- don't pick a topic/angle/hook because it looks likely to retain well if
     it doesn't also give viewers a real reason to forward it.
  3. `subs/1k views` -- does this actually convert viewers into subscribers. Weight
     this seriously: monetization needs 1,000 subscribers *and* the view threshold, not
     views alone, so a video that's merely average on retention but a strong subscriber
     converter is genuinely valuable, not just a consolation stat.
  4. `likes+comments/1k views` -- explicit engagement signal, distinct from passive
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

**Where a topic naturally has one, lean toward the angle with a real person in it --
someone who did something, wanted something, or got it wrong -- over a pure abstract
mechanism.** This is a lean, not a hard requirement: don't force a human angle onto a
topic that genuinely doesn't have one, and an abstract mechanism can still win on its
own vividness. But when the choice is close, the human-stakes angle is the better
default -- real channel evidence: a named, specific human-stakes story ("Hitler's
Last-Ditch Army Was 13-Year-Olds With One Grenade Each") measurably out-reached an
abstract-mechanism video from the same window ("Every straight tunnel through Earth
takes the same 42 minutes") by roughly 1.7x in views, confirmed directly against this
channel's own numbers.

### Write the share trigger and the contradicted belief -- before the hook

Do this before drafting hook candidates, not after. A script optimized for retention
first and shareability second tends to stay retention-shaped even when a share_trigger
gets bolted on at the end -- deciding what makes this forwardable first changes what
you actually write.

**Share trigger.** Complete this sentence literally: *"A viewer sends this to ______
because they want to ______."* The first blank must name an actual relationship, not
an audience segment.
- BAD: "people interested in history" / "science fans" -- these describe a category,
  not a person, and give nobody an actual reason to act.
- GOOD: "the friend who insists cast iron needs seasoning" / "their dad, who told them
  the opposite for 20 years" / "the coworker who always says they're too busy."

If you cannot complete this sentence with a specific relationship, the topic is not
shareable as-is -- pick a sharper angle before continuing. Write the result into
`share_trigger` (>=12 words, checked structurally by `script_schema.py` and for
genericness by `quality_gate.py`).

**Contradicted belief.** Write one sentence stating what the viewer currently believes
that this video proves wrong -- store it in `contradicted_belief` (>=8 words). This
belief must be **audible in the first ~3 seconds of narration** (beat 0), not saved
for the middle of the video -- `quality_gate.py` checks it actually shows up there. If
the video doesn't contradict anything a viewer plausibly currently believes, it's a
fact, not a story, and facts don't get shared the way a corrected misconception does.

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
(e.g. "history" covers far more than one viral clip) -- pick a different specific
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
- `share_trigger`: one sentence, >=12 words, completing "a viewer sends this to
  ______ because they want to ______" with a specific relationship -- see the
  share-trigger step above. Rejected by `quality_gate.py` if it reads as a generic
  audience description instead of naming an actual person/relationship.
- `contradicted_belief`: one sentence, >=8 words, stating what the viewer currently
  believes that this video disproves -- see the share-trigger step above. Must
  actually be audible in beat 0; rejected by `quality_gate.py` if it isn't.
- `closing_comment`: one sentence, >=8 words, written fresh for THIS video's specific
  topic -- `pipeline/upload.py` posts it as a top-level comment right after upload
  (not narrated, not pinned -- the YouTube Data API has no pin endpoint at all).
  Write it the way you'd actually comment on your own video: reference the specific
  claim/fact, not a generic line that could sit under any upload -- `quality_gate.py`
  checks it actually overlaps this video's topic/title, and rejects it if it reads as
  interchangeable. A natural subscribe/follow nudge or a comment-inviting question is
  welcome, but the substance requirement is specificity, not a fixed phrase -- e.g. for
  a video about a wrongly-named historical figure, "Same thing happened with [other
  example] -- history really doesn't like giving credit to the right person" is
  specific; "Let me know your thoughts below!" is not, regardless of word count. This
  replaced a small fixed template pool (2026-08-21) that started producing
  exact-duplicate comments across videos once category stopped varying (history-only)
  -- see `state/ruleset_changelog.json`.
- `sources` (optional but strongly preferred): use `WebSearch`/`WebFetch` to find at
  least one real, independently-verifiable source for this video's central claim --
  not a source for the topic in general, the specific number/mechanism/claim you're
  actually narrating. Record what you found as `[{"url": "...", "note": "what this
  source actually confirms, <=20 words"}, ...]`. `pipeline/upload.py` appends these to
  the video description automatically -- you don't need to write them into
  `description` yourself. If `WebSearch`/`WebFetch` isn't available to you this run,
  omit the field entirely rather than fabricating a citation from memory -- a
  plausible-sounding fake source is worse than none.
- `beats`: 3-16 entries, each `{"text": "...", "broll_query": "...", "beat_role": "..."}`.
  Every `broll_query` is the image-generation prompt for that beat (`pipeline/ai_broll.py`,
  the sole b-roll source as of 2026-08-21 -- there is no Pixabay/keyword-search fallback
  any more). Write it as a **concrete, visually specific description of a single scene --
  subject, setting, and what's visibly happening**, roughly 5-15 words (e.g. `"a hand in
  black leather glove slowly pulling on a plain black mask, dim room, side light"`, not
  the bare noun phrase `"black mask"` and not an overlong paragraph). Specificity is what
  produces a good image here, not brevity -- a vague or generic prompt reliably produces
  a vague or generic image, and this channel's own measured feedback has flagged AI
  images as "too conceptual" before. A fixed style suffix (lighting/quality/aspect terms)
  is appended automatically -- don't write those into `broll_query` yourself, just
  describe the scene's actual content.

  **No beat's `broll_query` may be a generic filler image reused for an abstract
  statement.** A real published video (2026-08-24) leaned on a "flags" image for every
  beat that made a broad claim instead of a concrete one -- 22% of the whole video ended
  up showing the same picture. `pipeline/quality_gate.py` now hard-rejects any two beats
  whose `broll_query` text is too similar, but passing that check isn't the actual bar:
  every beat's text should already be concrete enough that its own specific visual is
  obvious (the reference video's own discipline -- Alexander's face on "Alexander," an
  actual snake on "asp," never a generic stand-in). If a beat's claim is abstract, find
  the one concrete thing in it a camera could actually point at, don't reach for a
  generic image because nothing else fits.

  `beat_role` must be one of `hook`, `claim`, `evidence`,
  `joke`, `hedge`, `ending` (`pipeline/script_schema.py`'s `BEAT_ROLES`) -- see the
  format below for what each one means and when to use it; `pipeline/assemble.py` uses
  this field (not beat position) to decide which cuts get a whip-blur transition vs. a
  hard cut, and which beats get a lateral pan vs. a zoom, so getting it right actually
  changes how the video looks, not just organizational bookkeeping.

  **Format: claim / evidence / joke, replacing the old single-deep-payoff structure
  (2026-08-21, explicit channel-owner instruction, informed by a structural analysis of
  a genuinely well-performing history-facts Short -- see
  `state/ruleset_changelog.json`).** This channel's format was a single topic explained
  deeply with one payoff. The new format is one topic carrying **3-4 separate
  surprising claims**, each proven, each released with a joke, building to one stronger
  claim at the end -- density and rhythm over depth. Pick a topic that genuinely
  supports 3-4 distinct, verifiable, surprising claims about it, not one claim
  stretched thin or artificially split.

  Structure (adapt to how many genuinely strong claims you actually found -- don't pad
  weak filler in to hit a beat count):
  1. **`hook` (beat 0), landing within ~2-3 seconds of spoken narration.** A reliable
     shape for this: name the *category* of what's coming before naming the *subject*
     itself, and hold the subject's name or first strong claim noticeably longer on
     screen than anything that follows it -- a viewer's attention is caught by the open
     question ("what is this about?") and rewarded by how deliberately the answer
     lands, not just by the answer's content. Step 2.1 above still applies in full --
     >=3 hook candidates, spanning >=2 hook_types, picked for specificity over
     genre-fit.

     **Sentence-loop technique -- mandatory, every video, not situational.** Write the
     hook and the `ending` beat as one continuous sentence, then split it in two -- the
     back half becomes the video's opening, the front half becomes its close. E.g.
     draft "So here are some facts about [topic] that sound made up" as one line, then
     use "FACTS ABOUT [TOPIC]..." as beat 0 and "SO HERE ARE SOME..." as the final
     beat. On loop, the ending's dangling words grammatically complete into the hook's
     opening words, so the cut between them reads as continuous speech, not a restart
     -- the ear carries continuity the picture doesn't have to. This is why `ending`
     (below) bans a spoken CTA: any closing remark after the sentence breaks the loop.
     The `ending` beat's final word must be a genuine dangling connector -- a
     determiner, preposition, or conjunction like "some," "to," "one of," "and," "so,"
     "which," "the" -- not a complete, closed sentence. `quality_gate.py` checks this
     structurally (`DANGLING_ENDING_WORDS`) because a real published video (2026-08-21)
     skipped the technique entirely and closed on a flat, complete sentence, despite
     this section already existing -- prose alone didn't hold, same as several other
     rules in this document that only started working once they were also enforced in
     code.
  2. **`claim` beats: state one surprising fact with zero support, before proving it.**
     The claim should contradict something a viewer plausibly assumes -- let it sit
     unproven for the following `evidence` beat(s) rather than justifying it
     immediately in the same breath. That gap (stated, not yet proven) is what holds
     attention through the explanation.
  3. **`evidence` beats: the proof, immediately after its claim.** This can run longer
     than other beats (up to ~19 words is fine here specifically) since it's carrying
     the actual substantiation -- but still one continuous thought, not padded.
  4. **`joke` beats: a short, genuinely original aside, not on every claim.** 4-6
     words, phrased as an appended clause (dry editorializing on what was just said),
     never a setup-with-a-separate-punchline. Write a fresh one for each specific claim
     -- a generic aside that could bolt onto any fact in any video is filler with
     punctuation, not a joke. If nothing genuinely funny comes to mind for a given
     claim, skip the joke there rather than forcing a flat one; not every claim needs
     one, but most of this format's claims should have one.
  5. **Repeat claim -> evidence -> joke 2-4 times, then close on one stronger claim
     without a joke** (the "dark turn" -- your single best, most surprising, or most
     serious claim, saved for last rather than opened with).
  6. **`hedge`: one short beat qualifying the strongest claim's certainty** (e.g.
     acknowledging it's debated, contested, or not fully confirmed), where that's
     honestly true -- this is a credibility move, not decoration, and it must only be
     used where the claim's certainty is genuinely in question. Never invent a hedge
     for a claim that's actually well-established just for the rhythm; that would be
     manufacturing false uncertainty about something true, the opposite of what this
     beat is for.
  7. **`ending`: no summary, no spoken subscribe/comment ask, no goodbye.** End on
     momentum -- a trailing thought, a final specific detail, or (per the sentence-loop
     technique above, when you used it) the dangling front half of the hook's sentence,
     ending on a determiner/preposition like "some," "to," or "one of" so it reads as
     grammatically unfinished, not a conclusion. Do not put a subscribe or
     comment CTA in the narration; that reverses this same day's earlier mandatory-CTA
     rule (see both 2026-08-21 entries in `state/ruleset_changelog.json` for why the
     later one supersedes the first). The subscribe/comment nudge is handled entirely
     by the comment `pipeline/upload.py` posts automatically after every upload, so the
     narration is free to end on pure momentum instead of splitting its last seconds
     between a hook and a housekeeping ask.

  **Sentence length: mostly 6-19 words**, shorter for `joke` beats (4-6). Break a
  longer thought into two beats rather than one long compound sentence -- short,
  declarative sentences are what keeps this pace legible instead of exhausting.

  **Never write "--" or a standalone " - " as a clause connector in beat text** (this
  document's own prose style uses it constantly -- do not copy that habit into
  narration). This is a hard technical constraint, not a style note, enforced by
  `quality_gate.py`: Google Cloud TTS treats a bare dash as non-verbal and skips it
  silently, spending zero audio time on it, while the caption pipeline still allocates
  it an on-screen slot sized to its character length -- that produces a stray "--" or
  "-" floating on screen where nothing was actually said, and throws off every
  caption's estimated timing for the rest of that beat (verified in production, 2026-
  08-21: exactly this desync was reported by the channel owner). Use a real connecting
  word instead (and, but, so, though, because, which) or just split into two beats --
  both read more naturally out loud anyway. A hyphen inside an actual compound word
  (e.g. "long-term") is fine; it's only the whitespace-padded dash-as-connector that's
  banned.

  Whichever specific claims you use, beat 0 must *open* a specific, nameable gap -- not
  vague intrigue -- so there's something concrete left to close by the final claim.

  Two rules that matter more than anything else here:
  - **Beat 0's `broll_query`/image prompt needs the same bar as its text.** A
    generic/calm visual on beat 0 undercuts a strong hook -- the viewer processes the
    image before they've processed a single word of narration. Pick keywords pointing
    at something visually striking matching the claim, not a generic establishing shot.
  - **Before finalizing, check the middle, not just the opener -- each claim/evidence
    beat is a micro-reveal, not a restatement.** Re-read every beat and ask: does it
    add a genuinely new, specific, checkable detail, or does it restate/pad what the
    previous one already said? A format built on density has zero room for a beat that
    doesn't earn its place.

  **Mark the single most load-bearing word/number per beat with `**double
  asterisks**`** for caption emphasis (color highlight + size bump when burned in --
  see `pipeline/script_schema.py`). At most 1-2 marked words per beat, and only the
  specific number/claim/twist that beat exists to deliver -- marking everything makes
  nothing stand out. Not every beat needs one; a transitional beat with no single
  standout word doesn't need forced emphasis.
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
let the `ending` beat's final line repeat verbatim across videos. If you notice (from
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
