#!/usr/bin/env python3
"""Generate a first frame image via Gemini direct (Nano Banana 2 / Imagen-class) for Sora/Seedance i2v input.

Refactor 2026-04: removed Replicate proxy. Calls Google Generative Language API
(generativelanguage.googleapis.com) directly using GEMINI_API_KEY.

Default model: gemini-2.5-flash-image (~$0.039/img). Override via --model or
FIRST_FRAME_MODEL env. Other valid IDs:
  - gemini-3-pro-image-preview      (highest quality, ~$0.134/img @1-2K)
  - gemini-3-flash-image-preview    (mid-tier, ~$0.045-0.151/img)
  - nano-banana-pro-preview         (alias for gemini-3-pro-image-preview)

Reads creator profile to inject identity traits into the prompt.
Saves output immediately — Gemini returns inlineData (base64), no ephemeral URL.
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def load_text(path):
    return Path(path).read_text(encoding='utf-8')


def append_log(log_path, row):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('# Output Log\n\n| Timestamp | Label | Model | Prompt File | Provider URL | Saved File | Notes |\n|---|---|---|---|---|---|---|\n', encoding='utf-8')
    with open(path, 'a', encoding='utf-8') as f:
        f.write(f"| {row['timestamp']} | {row['label']} | {row['model']} | {row['prompt_file']} | {row['provider_url']} | {row['saved_file']} | {row['notes']} |\n")


def aspect_to_dims(aspect_ratio):
    """Map aspect ratio to (width, height) for Gemini image generation."""
    table = {
        '9:16': (1024, 1820),   # portrait, IG/TikTok
        '16:9': (1820, 1024),   # landscape
        '1:1':  (1024, 1024),   # square
        '4:5':  (1024, 1280),   # IG portrait
        '3:4':  (960, 1280),
        '4:3':  (1280, 960),
    }
    return table.get(aspect_ratio, (1024, 1820))


def generate_image(api_key, model, prompt, aspect_ratio, output_format='png', timeout=300):
    """Call Gemini :generateContent with image-output config. Returns raw image bytes."""
    width, height = aspect_to_dims(aspect_ratio)
    url = f"{GEMINI_BASE}/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {"parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {
                "aspectRatio": aspect_ratio,
            },
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "scrollclaw/refactor-2026-04",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini HTTP {e.code}: {err_body}") from e

    candidates = body.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {json.dumps(body)[:600]}")
    parts = candidates[0].get("content", {}).get("parts", [])
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            return base64.b64decode(inline["data"])
    raise RuntimeError(f"Gemini returned no inlineData image part: {json.dumps(body)[:600]}")


def main():
    ap = argparse.ArgumentParser(description='Generate first frame via Gemini direct.')
    ap.add_argument('--prompt-file', required=True, help='Text file with the image prompt')
    ap.add_argument('--output-file', required=True, help='Where to save the generated image')
    ap.add_argument('--creator', help='Path to creator profile .md (optional, for identity context)')
    ap.add_argument('--log-file', default=None, help='Output log file path (optional)')
    ap.add_argument('--label', default='frame1', help='Label for log entry')
    ap.add_argument('--model', default=os.environ.get('FIRST_FRAME_MODEL', 'gemini-2.5-flash-image'),
                    help='Gemini image model (default from FIRST_FRAME_MODEL env or gemini-2.5-flash-image)')
    ap.add_argument('--fallback-model', default='gemini-3-pro-image-preview',
                    help='Fallback model on primary failure')
    ap.add_argument('--aspect-ratio', default='9:16', help='Aspect ratio (9:16 default)')
    ap.add_argument('--output-format', default='png', help='Output format')
    ap.add_argument('--timeout-seconds', type=int, default=300)
    ap.add_argument('--no-fallback', action='store_true')
    args = ap.parse_args()

    api_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
    if not api_key:
        print('GEMINI_API_KEY (or GOOGLE_API_KEY) is not set', file=sys.stderr)
        sys.exit(2)

    prompt = load_text(args.prompt_file).strip()

    if args.creator and Path(args.creator).exists():
        creator_text = load_text(args.creator)
        if '## Prompt Invariants' in creator_text:
            invariants = creator_text.split('## Prompt Invariants')[1].split('##')[0].strip()
            prompt = f"{invariants}\n\nScene: {prompt}"

    models = [args.model] + ([] if args.no_fallback else [args.fallback_model])
    # Skip fallback if same as primary
    models = [m for i, m in enumerate(models) if m and m not in models[:i]]

    last_error = None
    for idx, model in enumerate(models):
        try:
            img_bytes = generate_image(
                api_key, model, prompt, args.aspect_ratio,
                args.output_format, args.timeout_seconds,
            )
            out_path = Path(args.output_file)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(img_bytes)
            size = len(img_bytes)
            if args.log_file:
                append_log(args.log_file, {
                    'timestamp': datetime.now().isoformat(timespec='seconds'),
                    'label': args.label,
                    'model': model,
                    'prompt_file': args.prompt_file,
                    'provider_url': f'gemini:{model}',
                    'saved_file': args.output_file,
                    'notes': f'saved ({size} bytes)' + (' via fallback' if idx > 0 else ''),
                })
            print(json.dumps({
                'ok': True,
                'model': model,
                'output_file': args.output_file,
                'provider': 'gemini-direct',
                'bytes': size,
                'fallback_used': idx > 0,
            }, indent=2))
            return
        except Exception as e:
            last_error = str(e)
            if args.log_file:
                append_log(args.log_file, {
                    'timestamp': datetime.now().isoformat(timespec='seconds'),
                    'label': args.label,
                    'model': model,
                    'prompt_file': args.prompt_file,
                    'provider_url': f'gemini:{model}',
                    'saved_file': 'n/a',
                    'notes': f'failed: {last_error[:240]}',
                })
            if idx == len(models) - 1:
                print(json.dumps({'ok': False, 'error': last_error}, indent=2), file=sys.stderr)
                sys.exit(1)


if __name__ == '__main__':
    main()
