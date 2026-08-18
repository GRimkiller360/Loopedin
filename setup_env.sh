#!/usr/bin/env bash
# Run once at the top of every routine session (cloud checkout is fresh each fire).
set -euo pipefail

# 180s timeout on each network-dependent step -- normal runs take ~20-30s, this just
# bounds a hung apt/pip mirror (observed once: 2+ hours stuck here with no output,
# silently blocking every scheduled fire behind it since GitHub's own job timeout is
# 6h). Failing fast and loud beats hanging silently; the workflow's own retry/on-failure
# handling takes it from there instead of nothing noticing for hours.
NET_TIMEOUT=180

# Check ffprobe too, not just ffmpeg -- some base images ship one without the other.
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1; then
        timeout "$NET_TIMEOUT" sudo apt-get update -qq && timeout "$NET_TIMEOUT" sudo apt-get install -y -qq ffmpeg
    else
        timeout "$NET_TIMEOUT" apt-get update -qq && timeout "$NET_TIMEOUT" apt-get install -y -qq ffmpeg
    fi
fi

timeout "$NET_TIMEOUT" pip install --quiet -r requirements.txt
mkdir -p work
