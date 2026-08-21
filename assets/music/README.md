# Background music tracks

Drop a handful of royalty-free / CC0 `.mp3` files here — `assemble.py` mixes one in
under the narration at low volume. Nothing is bundled by default; without any files
here, videos are assembled with narration audio only (no error, just silence in the
gaps).

Good CC0/royalty-free sources to pull from manually: YouTube's own Audio Library
(studio.youtube.com → Audio Library — explicitly cleared for use on YouTube), Free
Music Archive (filter by CC0), Pixabay Music. Keep the set small (5-10 tracks) and
check each one's license terms before adding it.

## Mood tagging (optional but recommended)

Add a `tags.json` file here mapping each filename to mood tags, e.g.:

```json
{
  "curious-piano-loop.mp3": ["curious", "calm"],
  "epic-drone.mp3": ["epic", "awe"],
  "mystery-strings.mp3": ["mysterious", "dramatic"]
}
```

`assemble.py`'s `CATEGORY_MOODS` maps each content category to preferred tags
(currently just history -> mysterious/dramatic/epic, since that's the channel's only
active category). A track matching one of the current script's category's preferred
tags gets picked over a plain random choice. Without `tags.json`, or for any file it
doesn't mention, selection just falls back to fully random -- this is additive, not
required.
