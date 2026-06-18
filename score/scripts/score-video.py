#!/usr/bin/env python3
"""Score a UGC video against the 7-dimension virality rubric via a vision model.

Implements the methodology in score/references/virality-scoring.md:
  - Extracts evenly-spaced frames from the video
  - Sends frames + brief + transcript context to a vision model (default Gemini Flash)
  - Returns a structured JSON score card with weighted score and go/no-go decision

Brand- and campaign-agnostic. Every input (video, brief, transcript, audience,
platform, model) is config-driven via flags or JSON. The same script serves any
campaign by swapping its config — no hardcoded assumptions about niche, voice,
or audience.

JSON config schema:
{
  "video": "path/to/captioned.mp4",
  "brief": "path/to/brief.md  OR  inline brief text",
  "transcript": "path/to/transcript.txt  OR  inline transcript text",
  "output": "path/to/scores/score-01.json",
  "platform": "Instagram Reels paid Meta",
  "audience": "ICP description (mãe-cuidadora 38-55, ...)",
  "n_frames": 8,
  "model": "gemini-2.5-flash",
  "api_key_env": "GEMINI_API_KEY"
}

CLI usage:
  python3 score-video.py --config workspace/campaigns/<slug>/score-config.json

  python3 score-video.py \\
      --video clip.mp4 \\
      --brief workspace/campaigns/<slug>/brief.md \\
      --transcript transcript.txt \\
      --output workspace/campaigns/<slug>/scores/score-01.json \\
      --platform "Instagram Reels paid Meta" \\
      --audience "Brazilian mothers 38-55 caring for child with epilepsy/TEA"
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


# ─────────────────────────────────────────────
# Rubric (mirrors score/references/virality-scoring.md)
# ─────────────────────────────────────────────

RUBRIC = """\
Score this UGC video against 7 dimensions (each 0-100):

1. Hook strength — does the first 2 seconds stop a fast scroll?
2. Emotional impact — does the viewer feel a specific emotion that matches the target ICP's lived experience?
3. Pacing flow — does every second earn the next?
4. Text readability — captions legible on mobile at scroll speed, within platform safe zones?
5. Scroll-stop power — visually distinct from typical feed content for this niche?
6. Completion likelihood — will the target viewer watch the full duration?
7. Shareability — would someone forward this to a friend in a similar situation?

HARD GATE: if Hook < 50, automatic NO-GO regardless of other scores.

Weighted formula:
  weighted = (hook * 0.20) + (scroll_stop * 0.20) + (completion * 0.20)
           + (pacing * 0.15) + (emotional * 0.15) + (readability * 0.10)

GO threshold: weighted >= 70.
"""

OUTPUT_SCHEMA = """\
Return ONLY a JSON object with this exact shape:
{
  "hook": <0-100>,
  "emotional": <0-100>,
  "pacing": <0-100>,
  "readability": <0-100>,
  "scroll_stop": <0-100>,
  "completion": <0-100>,
  "shareability": <0-100>,
  "weighted": <number>,
  "go": <true|false>,
  "top_fix": "<single highest-leverage fix; specific, implementable in <2h; or empty string if go=true and weighted >= 80>",
  "notes_per_dimension": {
    "hook": "<one sentence>",
    "emotional": "<one sentence>",
    "pacing": "<one sentence>",
    "readability": "<one sentence>",
    "scroll_stop": "<one sentence>",
    "completion": "<one sentence>",
    "shareability": "<one sentence>"
  }
}
"""


# ─────────────────────────────────────────────
# Video helpers
# ─────────────────────────────────────────────

def get_video_duration(video_path):
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
         '-of', 'json', video_path],
        capture_output=True, text=True, timeout=15,
    )
    return float(json.loads(result.stdout)['format']['duration'])


def extract_frames(video_path, n_frames, frames_dir):
    """Extract n_frames evenly-spaced PNGs into frames_dir. Returns list of (timestamp, path)."""
    Path(frames_dir).mkdir(parents=True, exist_ok=True)
    duration = get_video_duration(video_path)
    timestamps = [round(i * duration / n_frames, 2) for i in range(n_frames)]
    paths = []
    for i, t in enumerate(timestamps):
        out = Path(frames_dir) / f"f{i:02d}_t{t}.png"
        subprocess.run(
            ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
             '-ss', str(t), '-i', video_path, '-frames:v', '1', str(out)],
            check=True,
        )
        paths.append((t, str(out)))
    return paths


def load_text_or_path(value):
    """If value is an existing file path, return its contents. Otherwise return as-is."""
    if not value:
        return ""
    try:
        p = Path(value)
        if p.exists() and p.is_file():
            return p.read_text(encoding='utf-8')
    except OSError:
        pass
    return str(value)


# ─────────────────────────────────────────────
# Prompt assembly
# ─────────────────────────────────────────────

def build_prompt(brief, transcript, platform, audience, n_frames, duration):
    parts = [
        "You are evaluating a finished UGC ad video.",
        "",
    ]
    if platform:
        parts.append(f"PLATFORM: {platform}")
    if audience:
        parts.append(f"TARGET AUDIENCE: {audience}")
    if platform or audience:
        parts.append("")
    if brief:
        parts.append("CAMPAIGN BRIEF / CONTEXT:")
        parts.append(brief)
        parts.append("")
    if transcript:
        parts.append("AUDIO TRANSCRIPT:")
        parts.append(transcript)
        parts.append("")
    parts.append(f"You will see {n_frames} frames distributed across the {duration:.1f}-second video.")
    parts.append("")
    parts.append(RUBRIC)
    parts.append("")
    parts.append(OUTPUT_SCHEMA)
    return "\n".join(parts)


# ─────────────────────────────────────────────
# Gemini call
# ─────────────────────────────────────────────

def call_gemini(prompt, frames, api_key, model):
    contents_parts = [{"text": prompt}]
    for _, frame_path in frames:
        b64 = base64.b64encode(Path(frame_path).read_bytes()).decode("ascii")
        contents_parts.append({"inline_data": {"mime_type": "image/png", "data": b64}})

    body = {
        "contents": [{"parts": contents_parts}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.2,
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = json.loads(resp.read())

    text = raw["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


# ─────────────────────────────────────────────
# OpenRouter fallback (Gemini via OpenRouter)
# ─────────────────────────────────────────────

def call_openrouter(prompt, frames, api_key, model):
    """Same scoring call routed through OpenRouter's OpenAI-style API.

    Used when the direct Gemini call fails (e.g. credits depleted). max_tokens
    is capped because the OpenRouter account runs on a low free-tier balance;
    the JSON score card fits comfortably under the cap.
    """
    or_model = model if "/" in model else f"google/{model}"
    content = [{"type": "text", "text": prompt}]
    for _, frame_path in frames:
        b64 = base64.b64encode(Path(frame_path).read_bytes()).decode("ascii")
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}})

    body = {
        "model": or_model,
        "max_tokens": 4000,  # ponytail: capped for low free-tier balance; raise if account upgraded
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": content}],
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = json.loads(resp.read())
    return json.loads(raw["choices"][0]["message"]["content"])


def score_video(prompt, frames, model):
    """Score via Gemini direct (primary); fall back to OpenRouter on failure."""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            return call_gemini(prompt, frames, gemini_key, model)
        except Exception as e:  # noqa: BLE001 — any failure routes to fallback
            print(f"  ⚠ Gemini direct failed ({e}); trying OpenRouter fallback…",
                  file=sys.stderr)
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if or_key:
        return call_openrouter(prompt, frames, or_key, model)
    sys.exit("Error: scoring failed — set GEMINI_API_KEY and/or OPENROUTER_API_KEY")


def load_keys_env():
    """Load secrets/keys.env into os.environ (without overriding existing vars)."""
    path = Path(os.environ.get("EDGE_KEYS_ENV",
                               Path.home() / "edge-of-chaos/secrets/keys.env"))
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def resolve(args, cfg, key, default=None):
    """Pick CLI arg value > config value > default."""
    val = getattr(args, key.replace('-', '_'), None)
    if val is None:
        val = cfg.get(key, default)
    return val


def main():
    ap = argparse.ArgumentParser(description="Score a UGC video against virality rubric (Gemini vision)")
    ap.add_argument('--config', help="JSON config file (any field can be overridden by CLI flags)")
    ap.add_argument('--video', help="Path to video file to score")
    ap.add_argument('--brief', help="Path to brief.md or inline brief text")
    ap.add_argument('--transcript', help="Path to transcript file or inline transcript text")
    ap.add_argument('--output', help="Path to write the JSON score card")
    ap.add_argument('--platform', help="Distribution platform context (e.g. 'Instagram Reels paid Meta')")
    ap.add_argument('--audience', help="Target ICP / audience description")
    ap.add_argument('--n-frames', type=int, default=None)
    ap.add_argument('--model', default=None)
    ap.add_argument('--api-key-env', default=None)
    ap.add_argument('--frames-dir', default=None, help="Where to save extracted frames (default: /tmp/score-frames-<pid>)")
    args = ap.parse_args()

    cfg = {}
    if args.config:
        cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))

    video = resolve(args, cfg, 'video')
    output = resolve(args, cfg, 'output')
    if not video or not output:
        sys.exit("Error: --video and --output (or equivalent in --config) are required")

    brief = load_text_or_path(resolve(args, cfg, 'brief'))
    transcript = load_text_or_path(resolve(args, cfg, 'transcript'))
    platform = resolve(args, cfg, 'platform')
    audience = resolve(args, cfg, 'audience')
    n_frames = int(resolve(args, cfg, 'n-frames', 8) or 8)
    model = resolve(args, cfg, 'model', 'gemini-2.5-flash')
    frames_dir = resolve(args, cfg, 'frames-dir') or f"/tmp/score-frames-{os.getpid()}"

    load_keys_env()
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")):
        sys.exit("Error: set GEMINI_API_KEY (primary) and/or OPENROUTER_API_KEY (fallback)")

    if not Path(video).exists():
        sys.exit(f"Error: video not found: {video}")

    duration = get_video_duration(video)
    frames = extract_frames(video, n_frames, frames_dir)
    prompt = build_prompt(brief, transcript, platform, audience, n_frames, duration)

    score = score_video(prompt, frames, model)

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(score, ensure_ascii=False, indent=2), encoding='utf-8')

    print(json.dumps(score, ensure_ascii=False, indent=2))
    print(f"\n✓ Score saved: {output}")
    print(f"  Decision: {'GO' if score.get('go') else 'NO-GO'} (weighted {score.get('weighted')})")


if __name__ == '__main__':
    main()
