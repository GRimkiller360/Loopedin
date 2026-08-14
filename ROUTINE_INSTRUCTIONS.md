# Routine runbook

You are running one unattended fire of the AI Shorts channel automation. No human
reviews this run's output before it goes live. Follow these steps in order and do not
skip the health check.

## 0. Health check

Read `state/routine_health.json`. If `paused` is true, STOP immediately -- do not run
the pipeline, do not upload anything. Report the pause reason in your final summary and
end the run. A human needs to reset it manually.

## 1. Set up the environment

Run `bash setup_env.sh` once at the start of the run (installs ffmpeg + Python deps).

## 2. Get a trend seed

```
python pipeline/trend_source.py --out work/trend_seed.json
```

This returns a *topic seed* only (a title/category/view-count signal) -- never the
source video's actual content. Read `work/trend_seed.json`.

## 3. Write an original script -- this is your job, not a script's

Using the trend seed as inspiration only, write an **original** commentary/take. Do
not summarize, transcribe, or closely paraphrase the seed video -- riff on the topic,
don't reuse the source. Save it as `work/script.json` matching the shape documented in
`pipeline/script_schema.py`:

- `topic`, `title` (<=100 chars), `description`, `tags`
- `beats`: 3-12 entries, each `{"text": "...", "broll_query": "..."}`
- keep total narration under ~130 words so the final video stays under 58s

Validate it:

```
python pipeline/script_schema.py work/script.json
```

Cross-check `script["topic"]` against the last ~40 entries in
`state/used_topics.json`. If it's too similar to a recent one, pick a different angle
before continuing -- this is the variety safety rail; don't skip it.

## 4. Narration audio

```
python pipeline/tts.py --script work/script.json --out work/narration.mp3
```

## 5. B-roll

```
python pipeline/broll.py --script work/script.json --work-dir work/broll
```

This prints a JSON list of clip paths -- save it to `work/broll_clips.json`.

## 6. Assemble the video

```
python pipeline/assemble.py --script work/script.json --narration work/narration.mp3 \
    --clips work/broll_clips.json --work-dir work/assemble --out work/final.mp4
```

## 7. Upload

```
python pipeline/upload.py --video work/final.mp4 --script work/script.json --privacy public
```

On success, record the topic so future runs avoid repeating it:

```
python -c "from pipeline.state_utils import record_used_topic; record_used_topic('state/used_topics.json', '<topic>', '<video_id>')"
```

## 8. Performance feedback -- only on the run whose hour is 0 UTC

Once/day is enough; don't burn Analytics quota on every fire.

```
python pipeline/analytics_feedback.py
```

Read the printed summary. There's nowhere to persist "lessons learned" beyond this
run's own judgment -- let it inform which categories/angles you lean into on your next
fire.

## 9. On any failure at any step

Run this immediately, then stop:

```
python -c "from pipeline.state_utils import record_failure; import pipeline.config as c; print(record_failure('state/routine_health.json', '<short reason>', c.CONSECUTIVE_FAILURES_TO_PAUSE))"
```

If it prints `True`, the routine just paused itself -- say so clearly in your final
summary so the human knows to look.

## 10. On success

```
python -c "from pipeline.state_utils import record_success; record_success('state/routine_health.json')"
```

## 11. Commit

Commit and push `state/*.json` (never commit anything under `work/` -- it's gitignored
scratch space) with a short message noting the topic and video ID.
