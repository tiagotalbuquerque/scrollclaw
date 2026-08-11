---
name: scrollclaw-score
description: "Score UGC videos for virality before publishing. 7 criteria, 0-100 each. Only 70+ gets published."
metadata:
  openclaw:
    emoji: "📊"
    user-invocable: true
    triggers:
      - "score video"
      - "virality score"
      - "ugc score"
      - "rate ugc"
      - "video quality check"
---

# Virality Scoring

Score every video before publishing. Read `references/virality-scoring.md` for the full methodology.

## Criteria

7 dimensions, scored 0-100 each:

1. **Hook Strength** — Does frame 1 + first 3 seconds force a pause?
2. **Emotional Impact** — Does it trigger a visceral response (not just "interesting")?
3. **Pacing** — Does every second earn the next second?
4. **Text Readability** — Are captions legible on mobile, properly timed?
5. **Scroll-Stop Power** — Would this stop a thumb mid-scroll in a feed?
6. **Completion Likelihood** — Will viewers watch to the end?
7. **Shareability** — Would someone send this to a friend?

## Thresholds

| Score | Action |
|-------|--------|
| 70+ | **Publish.** Ship it. |
| 60-69 | Polish — specific fixes can save it. Identify which dimensions are dragging. |
| Below 60 | **Regenerate.** Don't polish a bad clip. Go back to the failing stage. |

## Process

Use the canonical script `score/scripts/score-video.py` — extracts evenly-spaced frames, builds the rubric prompt, calls a vision model, writes the JSON score card.

**Vision backend** (`--provider`): `gemini` keeps the Gemini→OpenRouter chain;
`grok` scores with Grok vision on the xAI subscription (OAuth from
`~/.grok/auth.json`), which costs nothing per pass and is the right default when
Gemini's free tier is rate-limited. `auto` picks from the model name — anything
`grok-*` routes to Grok. Like the video provider, `grok` does not fall back:
scoring silently on a different model would make score cards incomparable across
a campaign.

```bash
python3 score/scripts/score-video.py --provider grok --model grok-4.5 \
  --video clips/final-01.mp4 --brief workspace/campaigns/<slug>/brief.md \
  --output workspace/campaigns/<slug>/scores/score-01.json
```

```bash
python3 score/scripts/score-video.py \
  --video workspace/campaigns/<slug>/clips/final-01.mp4 \
  --brief workspace/campaigns/<slug>/brief.md \
  --transcript workspace/campaigns/<slug>/transcript.txt \
  --platform "Instagram Reels paid Meta" \
  --audience "<ICP description>" \
  --output workspace/campaigns/<slug>/scores/score-01.json
```

Or via JSON config (every CLI flag has a config-file equivalent — see `score-video.py` docstring). Brand-agnostic: video, brief, transcript, audience, platform, model are all config-driven. Same script serves any campaign.

After scoring, route based on threshold:

1. Weighted ≥ 70 → ship (subject to human approval gate per campaign policy).
2. Weighted 60-69 → polish; identify weakest dimensions and route back:
   - Hook / scroll-stop weak → `/first-frame` (regenerate canonical face)
   - Pacing / completion weak → `/persona` (restructure script)
   - Emotional impact weak → `/persona` (better language from research)
   - Text readability weak → `/assemble` (fix captions — usually `burn-captions.py` config)
   - Audio / voice issues → `/assemble` (re-run S2S)
3. Weighted < 60 OR Hook < 50 → regenerate; don't polish a bad clip.

The script auto-applies the hard gate (Hook < 50 → NO-GO) and the weighted formula from `references/virality-scoring.md`.

## Brand Memory Integration

### Reads
| File | Purpose |
|------|---------|
| `workspace/campaigns/<slug>/clips/final-*.mp4` | Video(s) to score |
| `workspace/campaigns/<slug>/brief.md` | Original campaign goals — score against intent, not just virality |
| `workspace/campaigns/<slug>/persona-research.md` | Whether the script language matches real customer language |

### Writes
| File | Notes |
|------|-------|
| `workspace/campaigns/<slug>/scores/score-<version>.md` | Score card with per-dimension breakdown and go/no-go |
| `workspace/campaigns/<slug>/learnings.md` | Append-only — what worked, what didn't, threshold notes |

### Context loading

```
📊 Score context loaded:
  ✓ Campaign: ridge-q1
  ✓ Final clips: 2 (workspace/campaigns/ridge-q1/clips/final-*.mp4)
  ✓ Brief: workspace/campaigns/ridge-q1/brief.md
  ✓ Persona research: workspace/campaigns/ridge-q1/persona-research.md
```

### Learnings append format

After every score, append to `workspace/campaigns/<slug>/learnings.md`:

```markdown
## YYYY-MM-DD — <campaign-slug> <format>

**Score:** <average>/100 (<go/no-go>)
**Dimension breakdown:** Hook <n> | Emotional <n> | Pacing <n> | Captions <n> | Scroll-stop <n> | Completion <n> | Share <n>

**What worked:**
- [specific finding]

**What didn't:**
- [specific finding]

**Remediation:** [if no-go: which skill to route back to and why]
```

Never overwrite this file. Only append.

## Contract

### Input
- Required: at least one final assembled clip
- Optional: campaign brief, persona research, prior campaign learnings
- Format: workspace video files plus markdown context
- Source: `/assemble`, `workspace/campaigns/<slug>/brief.md`, and `persona-research.md`

### Output
- Produces: one score card per clip, a go/no-go decision, and appended learnings
- Format: markdown files in `workspace/campaigns/<slug>/scores/` plus append-only entries in `learnings.md`
- Default behavior: score every final clip against the 7 dimensions and route the user back to the failing stage if the score is below 70
- Downstream use: publish/no-publish decision and future campaign learnings

### Validation
- Pre-conditions: final clip exists and is watchable end to end
- Post-conditions: score includes a dimension breakdown, a threshold decision, and clear remediation if needed
- Failure checks: do not recommend publishing without a real breakdown; if the clip is unscorable or missing context, flag the gap explicitly

## Output

- Virality score card in `workspace/campaigns/<slug>/scores/score-<version>.md`
- Go/no-go decision
- Learnings appended to `workspace/campaigns/<slug>/learnings.md`
- If no-go: specific remediation routing

## Batch Scoring

For campaign batches, score all variants and rank. Publish only the 70+ tier. Below-60 variants get regenerated, not tweaked.

```bash
bash scripts/batch-campaign.sh \
  --workspace workspace/campaigns/<slug> \
  --creators workspace/campaigns/<slug>/creators/ \
  --formats "talking-head,pov-demo,podcast-clip" \
  --dual-output
```
