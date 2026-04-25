#!/usr/bin/env python3
"""Generate a first frame image with cross-provider fallback (fal.ai → Replicate).

Refactor 2026-04: fal.ai consolidates the video pipeline (Sora 2, Seedance 2.0,
Nano Banana 2). Replicate stays as a resilience fallback in case fal.ai is
degraded — same model family (Google Nano Banana), different gateway.

Provider routing is automatic by model slug prefix:
  - "fal-ai/..."   → fal.ai (single POST to fal.run, returns CDN URL)
  - "google/..."   → Replicate (predictions + poll + download)

Defaults:
  - Primary:  fal-ai/nano-banana-2     (FIRST_FRAME_MODEL env override)
  - Fallback: google/nano-banana-2     (Replicate, FIRST_FRAME_FALLBACK env)

Both gateways host Google's Nano Banana 2 — qualidade equivalente.
Saves output immediately to disk (CDN URLs are ephemeral).
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime


# ─── Helpers ───────────────────────────────────────────────────────────


def load_text(path):
    return Path(path).read_text(encoding='utf-8')


def append_log(log_path, row):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('# Output Log\n\n| Timestamp | Label | Model | Prompt File | Provider URL | Saved File | Notes |\n|---|---|---|---|---|---|---|\n', encoding='utf-8')
    with open(path, 'a', encoding='utf-8') as f:
        f.write(f"| {row['timestamp']} | {row['label']} | {row['model']} | {row['prompt_file']} | {row['provider_url']} | {row['saved_file']} | {row['notes']} |\n")


def download_file(url, dest, timeout=120):
    req = urllib.request.Request(url, headers={'User-Agent': 'scrollclaw/refactor-2026-04'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(data)
    return len(data)


def provider_for(model):
    """Route a model slug to its provider."""
    if model.startswith("fal-ai/"):
        return "fal"
    if "/" in model and not model.startswith("fal-ai/"):
        # google/nano-banana-2, black-forest-labs/flux-dev, etc.
        return "replicate"
    raise ValueError(f"Cannot route model '{model}' — expected 'fal-ai/...' or 'owner/name'")


# ─── Provider: fal.ai ──────────────────────────────────────────────────


def fal_generate(api_key, model, prompt, aspect_ratio='9:16', output_format='png', timeout=300):
    """POST to fal.run/{model}. Returns first image URL."""
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


# ─── Provider: Replicate ───────────────────────────────────────────────


def _replicate_http(method, url, token, payload=None, timeout=120):
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def replicate_generate(api_key, model, prompt, aspect_ratio='9:16', output_format='png',
                       poll_seconds=3, timeout=300):
    """Replicate predictions API: create + poll + extract URL."""
    owner, name = model.split('/', 1)
    create_url = f"https://api.replicate.com/v1/models/{owner}/{name}/predictions"
    create_payload = {
        "input": {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "output_format": output_format,
        }
    }
    prediction = _replicate_http("POST", create_url, api_key, create_payload)
    get_url = prediction["urls"]["get"]
    started = time.time()
    while True:
        result = _replicate_http("GET", get_url, api_key)
        status = result.get("status")
        if status == "succeeded":
            output = result.get("output")
            if isinstance(output, list) and output:
                return output[0]
            if isinstance(output, str):
                return output
            if isinstance(output, dict):
                for v in output.values():
                    if isinstance(v, str) and v.startswith("http"):
                        return v
                    if isinstance(v, list):
                        for x in v:
                            if isinstance(x, str) and x.startswith("http"):
                                return x
            raise RuntimeError(f"Replicate succeeded but no URL in output: {json.dumps(output)[:300]}")
        if status in ("failed", "canceled"):
            raise RuntimeError(f"Replicate {status}: {result.get('error', 'unknown')}")
        if time.time() - started > timeout:
            raise TimeoutError(f"Replicate poll timed out after {timeout}s")
        time.sleep(poll_seconds)


# ─── Dispatcher ────────────────────────────────────────────────────────


def generate(model, prompt, aspect_ratio, output_format, timeout):
    """Route to the right provider, return URL of generated image."""
    provider = provider_for(model)
    if provider == "fal":
        key = os.environ.get("FAL_KEY")
        if not key:
            raise RuntimeError("FAL_KEY is not set (required for fal-ai/* models)")
        return fal_generate(key, model, prompt, aspect_ratio, output_format, timeout)
    if provider == "replicate":
        key = os.environ.get("REPLICATE_API_TOKEN")
        if not key:
            raise RuntimeError("REPLICATE_API_TOKEN is not set (required for fallback to Replicate)")
        return replicate_generate(key, model, prompt, aspect_ratio, output_format, timeout=timeout)
    raise RuntimeError(f"Unknown provider for model '{model}'")


# ─── Main ──────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(
        description='Generate first frame via fal.ai with Replicate fallback.',
    )
    ap.add_argument('--prompt-file', required=True, help='Text file with the image prompt')
    ap.add_argument('--output-file', required=True, help='Where to save the generated image')
    ap.add_argument('--creator', help='Path to creator profile .md (optional, for identity context)')
    ap.add_argument('--log-file', default=None, help='Output log file path (optional)')
    ap.add_argument('--label', default='frame1', help='Label for log entry')
    ap.add_argument('--model',
                    default=os.environ.get('FIRST_FRAME_MODEL', 'fal-ai/nano-banana-2'),
                    help='Primary model (FIRST_FRAME_MODEL env override). fal-ai/* or owner/name.')
    ap.add_argument('--fallback-model',
                    default=os.environ.get('FIRST_FRAME_FALLBACK', 'google/nano-banana-2'),
                    help='Fallback model on primary failure (cross-provider OK)')
    ap.add_argument('--aspect-ratio', default='9:16', help='Aspect ratio (9:16 default)')
    ap.add_argument('--output-format', default='png', help='Output format')
    ap.add_argument('--timeout-seconds', type=int, default=300)
    ap.add_argument('--no-fallback', action='store_true')
    args = ap.parse_args()

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
            image_url = generate(model, prompt, args.aspect_ratio,
                                 args.output_format, args.timeout_seconds)
            size = download_file(image_url, args.output_file)
            provider = provider_for(model)
            if args.log_file:
                append_log(args.log_file, {
                    'timestamp': datetime.now().isoformat(timespec='seconds'),
                    'label': args.label,
                    'model': model,
                    'prompt_file': args.prompt_file,
                    'provider_url': image_url,
                    'saved_file': args.output_file,
                    'notes': f'saved ({size} bytes) via {provider}' + (' [fallback]' if idx > 0 else ''),
                })
            print(json.dumps({
                'ok': True,
                'model': model,
                'provider': provider,
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
                    'provider_url': f'{provider_for(model) if "/" in model else "?"}:{model}',
                    'saved_file': 'n/a',
                    'notes': f'failed: {last_error[:240]}',
                })
            if idx == len(models) - 1:
                print(json.dumps({'ok': False, 'error': last_error}, indent=2), file=sys.stderr)
                sys.exit(1)


if __name__ == '__main__':
    main()
