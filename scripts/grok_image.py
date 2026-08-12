#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Grok Imagine stills for ScrollClaw first frames — billed to the xAI subscription.

Completes the Grok-only path: with this plus grok_video.py, a campaign runs first
frame → A-roll → B-roll → scoring without touching fal.ai, Replicate, Gemini or
OpenRouter.

Model choice
------------
Defaults to `grok-imagine-image-quality`, which is **Imagine Image 2.0** promoted to
Quality Mode (Aug 2026) — not merely a pricier tier of the base model. The base
`grok-imagine-image` is still pinned to its March build and mangles the framing on
reference edits. Resolution is identical between them (720x1280 at 9:16), so judging
by resolution alone leads to the wrong model; the difference shows in the framing and
the face.

Reference aspect must match the output
--------------------------------------
Feed a 16:9 portrait and ask for 9:16 and the model keeps the source geometry: the
base model pillarboxes it in white and distorts the face; Quality smart-resizes but
blurs the extension. Neither is usable as a first frame. So the reference is
center-cropped to the target ratio before upload — that alone produced the cleanest
frames in testing.
"""
import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
AUTH_JSON = Path.home() / ".grok" / "auth.json"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
TICK = 1e-9
RATIOS = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0, "4:3": 4 / 3,
          "3:4": 3 / 4, "3:2": 3 / 2, "2:3": 2 / 3}


def resolve_auth():
    """OAuth first: it spends the subscription instead of metered credits."""
    if AUTH_JSON.exists():
        try:
            rec = list(json.loads(AUTH_JSON.read_text()).values())[0]
            token, expires = rec.get("key"), rec.get("expires_at", "")
            if token:
                if expires:
                    exp = datetime.fromisoformat(
                        expires.replace("Z", "+00:00").split(".")[0] + "+00:00")
                    if exp < datetime.now(timezone.utc):
                        print("  ⚠ Grok OAuth token expired — run `grok login --device-auth`",
                              file=sys.stderr)
                    else:
                        return token, "oauth (subscription)"
                else:
                    return token, "oauth (subscription)"
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ could not read {AUTH_JSON}: {e}", file=sys.stderr)
    key = os.environ.get("XAI_API_KEY")
    if key:
        return key, "XAI_API_KEY (metered credits)"
    sys.exit("Error: no Grok auth. Run `grok login --device-auth` (subscription) "
             "or export XAI_API_KEY (metered).")


def crop_to_ratio(path, ratio_name):
    """Center-crop the reference to the output ratio, biased slightly above centre so
    a head stays in frame. Returns a JPEG data URI. Without PIL the original is sent
    as-is — degraded framing beats a hard failure mid-campaign."""
    target = RATIOS.get(ratio_name)
    try:
        from PIL import Image
    except ImportError:
        print("  ⚠ Pillow missing — sending reference uncropped, framing may suffer",
              file=sys.stderr)
        return "data:image/jpeg;base64," + base64.b64encode(Path(path).read_bytes()).decode()

    im = Image.open(path).convert("RGB")
    w, h = im.size
    if target and abs((w / h) - target) > 0.02:
        if w / h > target:                      # too wide → trim the sides
            nw = int(h * target)
            cx = int(w * 0.49)                  # a hair left of centre reads better on portraits
            left = max(0, min(w - nw, cx - nw // 2))
            im = im.crop((left, 0, left + nw, h))
        else:                                   # too tall → trim the bottom first
            nh = int(w / target)
            im = im.crop((0, 0, w, min(h, nh)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=93)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def post(path, payload, bearer):
    req = urllib.request.Request(API + path, data=json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + bearer,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def download(url, dest, tries=5):
    """Generation costs, downloading does not — so only this half retries. imgen.x.ai
    also 403s a library user-agent, hence the browser UA."""
    Path(str(dest) + ".url").write_text(url)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
                f.write(r.read())
            Path(str(dest) + ".url").unlink(missing_ok=True)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"  download failed ({i+1}/{tries}): {str(e)[:90]}", file=sys.stderr)
            time.sleep(4)
    print(f"  ⚠ generated but not downloaded — URL kept at {dest}.url. Retry the "
          f"download; do NOT regenerate.", file=sys.stderr)
    return False


def main():
    p = argparse.ArgumentParser(description="Generate a first frame with Grok Imagine")
    p.add_argument("--prompt"); p.add_argument("--prompt-file")
    p.add_argument("--output-file", required=True)
    p.add_argument("--reference", help="creator reference image — required for a locked face")
    p.add_argument("--aspect-ratio", default="9:16")
    p.add_argument("--model", default=os.environ.get("GROK_IMAGE_MODEL",
                                                     "grok-imagine-image-quality"))
    p.add_argument("--log-file"); p.add_argument("--label", default="first-frame")
    a = p.parse_args()

    prompt = a.prompt or (Path(a.prompt_file).read_text().strip() if a.prompt_file else None)
    if not prompt:
        sys.exit("Error: --prompt or --prompt-file required")

    bearer, source = resolve_auth()
    payload = {"model": a.model, "prompt": prompt, "aspect_ratio": a.aspect_ratio, "n": 1}
    if a.reference:
        if not Path(a.reference).exists():
            sys.exit(f"Error: reference not found: {a.reference}")
        # /images/edits is the only endpoint that honours the reference. Passing
        # `image` to /images/generations is accepted and silently ignored — it
        # returns a different person, which is the hardest failure to notice.
        payload["image"] = {"url": crop_to_ratio(a.reference, a.aspect_ratio)}
        endpoint = "/images/edits"
    else:
        endpoint = "/images/generations"
        print("  ⚠ no --reference: identity will drift between frames", file=sys.stderr)

    print(f"Grok image · {a.model} · {a.aspect_ratio} · auth: {source}")
    try:
        resp = post(endpoint, payload, bearer)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code == 401:
            sys.exit("Error: 401 — token invalid/expired. Run `grok login --device-auth`.")
        if e.code == 403 and "credit" in body.lower():
            sys.exit("Error: 403 — metered credits exhausted. OAuth avoids this: "
                     "`grok login --device-auth`.")
        sys.exit(f"Error: HTTP {e.code} — {body[:300]}")

    cost = resp.get("usage", {}).get("cost_in_usd_ticks", 0) * TICK
    Path(a.output_file).parent.mkdir(parents=True, exist_ok=True)
    if not download(resp["data"][0]["url"], a.output_file):
        sys.exit(1)

    label = (f"~${cost:.2f} metered-equivalent (subscription)" if source.startswith("oauth")
             else f"billed ${cost:.2f}")
    print(f"  ✓ {a.output_file} · {label}")

    if a.log_file:
        Path(a.log_file).parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        tag = f"cost≈${cost:.2f} (plan)" if source.startswith("oauth") else f"cost=${cost:.2f}"
        with open(a.log_file, "a", encoding="utf-8") as f:
            f.write(f"| {stamp} | {a.label} | {a.model} | still | grok:{source.split()[0]} | "
                    f"{a.output_file} | {tag} |\n")


if __name__ == "__main__":
    main()
