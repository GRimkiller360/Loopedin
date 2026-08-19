#!/usr/bin/env bash
# Run once at the top of every routine session (cloud checkout is fresh each fire).
set -euo pipefail

# 180s timeout on each network-dependent step -- normal runs take ~20-30s, this just
# bounds a hung apt/pip mirror (observed: 2+ hours stuck here with no output once,
# silently blocking every scheduled fire behind it since GitHub's own job timeout is
# 6h). Failing fast and loud beats hanging silently; the workflow's own retry/on-failure
# handling takes it from there instead of nothing noticing for hours.
NET_TIMEOUT=180
RETRIES=3

# Retries a command up to RETRIES times with a short backoff -- this specific apt/pip
# step has now hung/failed twice in two days (a transient mirror issue, not a code bug),
# and a single timeout still burns an entire video slot on one bad mirror hiccup. A few
# retries absorbs that without needing a human to notice and manually re-run.
retry() {
    local attempt=1
    while true; do
        if timeout "$NET_TIMEOUT" "$@"; then
            return 0
        fi
        if [ "$attempt" -ge "$RETRIES" ]; then
            return 1
        fi
        echo "retrying ($attempt/$RETRIES): $*" >&2
        sleep $((attempt * 5))
        attempt=$((attempt + 1))
    done
}

# Check ffprobe too, not just ffmpeg -- some base images ship one without the other.
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1; then
        retry sudo apt-get update -qq && retry sudo apt-get install -y -qq ffmpeg
    else
        retry apt-get update -qq && retry apt-get install -y -qq ffmpeg
    fi
fi

retry pip install --quiet -r requirements.txt
mkdir -p work
