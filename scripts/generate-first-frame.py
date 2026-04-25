#!/usr/bin/env python3
"""Generate a first frame image via fal.ai (Nano Banana 2 / Imagen-class) for Sora/Seedance i2v input.

Refactor 2026-04: Replicate gateway removed. fal.ai is now the single gateway for
images (`fal-ai/nano-banana-2`), A-roll (Sora 2), and B-roll (Seedance 2.0). Single
FAL_KEY drives the whole video pipeline.

Default model: fal-ai/nano-banana-2 (Google Nano Banana 2 = Gemini 3 Pro Image hosted on fal).
Override via --model or FIRST_FRAME_MODEL env. Other valid IDs:
  - fal-ai/nano-banana             (Nano Banana 1, cheaper/older)
  - fal-ai/imagen4/preview         (Google Imagen 4)
  - fal-ai/imagen4                 (Imagen 4 stable)

Reads creator profile to inject identity traits into the prompt.
Saves output immediately — fal.ai returns a CDN URL (fal.media) which is
ephemeral; we download to disk before returning.
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime


def load_text(path):
    return Path(path).read_text(encoding='utf-8')


def append_log(log_path, row):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('# Output Log\n\n| Timestamp | Label | Model | Prompt File | Provider URL | Saved File | Notes |\n|---|---|---|---|---|---|---|\n', encoding='utf-8')
    with open(path, 'a', encoding='utf-8') as f:
        f.write(f"| {row['timestamp']} | {row['label']} | {row['model']} | {row['prompt_file']} | {row['provider_url']} | {row['saved_file']} | {row['notes']} |\n")


def fal_generate(api_key, model, prompt, aspect_ratio='9:16', output_format='png', timeout=300):
    """POST to fal.run/{model} with prompt + aspect_ratio. Returns first image URL."""
    url = f"https://fal.run/{model}"
    payload = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "output_format": output_format,
    }
    headers = {
        "Authorization": f"Key {api_key}",
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
        raise RuntimeError(f"fal.ai HTTP {e.code}: {err_body}") from e

    images = body.get("images") or []
    if not images:
        raise RuntimeError(f"fal.ai returned no images: {json.dumps(body)[:600]}")
    image_url = images[0].get("url")
    if not image_url:
        raise RuntimeError(f"fal.ai image entry has no url: {json.dumps(images[0])[:300]}")
    return image_url


def download_file(url, dest, timeout=120):
    req = urllib.request.Request(url, headers={'User-Agent': 'scrollclaw/refactor-2026-04'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(data)
    return len(data)


def main():
    ap = argparse.ArgumentParser(description='Generate first frame via fal.ai (Nano Banana 2).')
    ap.add_argument('--prompt-file', required=True, help='Text file with the image prompt')
    ap.add_argument('--output-file', required=True, help='Where to save the generated image')
    ap.add_argument('--creator', help='Path to creator profile .md (optional, for identity context)')
    ap.add_argument('--log-file', default=None, help='Output log file path (optional)')
    ap.add_argument('--label', default='frame1', help='Label for log entry')
    ap.add_argument('--model', default=os.environ.get('FIRST_FRAME_MODEL', 'fal-ai/nano-banana-2'),
                    help='fal.ai model slug (default from FIRST_FRAME_MODEL env or fal-ai/nano-banana-2)')
    ap.add_argument('--fallback-model', default='fal-ai/nano-banana',
                    help='Fallback model on primary failure')
    ap.add_argument('--aspect-ratio', default='9:16', help='Aspect ratio (9:16 default)')
    ap.add_argument('--output-format', default='png', help='Output format')
    ap.add_argument('--timeout-seconds', type=int, default=300)
    ap.add_argument('--no-fallback', action='store_true')
    args = ap.parse_args()

    api_key = os.environ.get('FAL_KEY')
    if not api_key:
        print('FAL_KEY is not set', file=sys.stderr)
        sys.exit(2)

    prompt = load_text(args.prompt_file).strip()

    if args.creator and Path(args.creator).exists():
        creator_text = load_text(args.creator)
        if '## Prompt Invariants' in creator_text:
            invariants = creator_text.split('## Prompt Invariants')[1].split('##')[0].strip()
            prompt = f"{invariants}\n\nScene: {prompt}"

    models = [args.model] + ([] if args.no_fallback else [args.fallback_model])
    models = [m for i, m in enumerate(models) if m and m not in models[:i]]

    last_error = None
    for idx, model in enumerate(models):
        try:
            image_url = fal_generate(
                api_key, model, prompt, args.aspect_ratio,
                args.output_format, args.timeout_seconds,
            )
            size = download_file(image_url, args.output_file)
            if args.log_file:
                append_log(args.log_file, {
                    'timestamp': datetime.now().isoformat(timespec='seconds'),
                    'label': args.label,
                    'model': model,
                    'prompt_file': args.prompt_file,
                    'provider_url': image_url,
                    'saved_file': args.output_file,
                    'notes': f'saved ({size} bytes)' + (' via fallback' if idx > 0 else ''),
                })
            print(json.dumps({
                'ok': True,
                'model': model,
                'output_file': args.output_file,
                'provider_url': image_url,
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
                    'provider_url': f'fal.ai:{model}',
                    'saved_file': 'n/a',
                    'notes': f'failed: {last_error[:240]}',
                })
            if idx == len(models) - 1:
                print(json.dumps({'ok': False, 'error': last_error}, indent=2), file=sys.stderr)
                sys.exit(1)


if __name__ == '__main__':
    main()
