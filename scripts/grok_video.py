#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Grok Imagine video generation for ScrollClaw A-roll/B-roll.

Why this exists alongside the fal.ai path: Grok video bills against the xAI
*subscription* (SuperGrok / X Premium) via OAuth, not metered API credits. On a
campaign of 20 clips that is the difference between a credit top-up and no
incremental spend at all.

Dialogue and lip sync
---------------------
Since the 25 Apr 2026 update, image-to-video lip-syncs spoken dialogue and renders the
audio natively; `grok-imagine-video-1.5` (the default here) carries that. The line goes
inside the prompt, quoted — there is no separate audio parameter. Portuguese is verified
working: a pt-BR line came back transcribed verbatim at 0.98 language confidence.

Write the line with its accents. Stripping them to avoid shell-escaping trouble makes
the model pronounce what it was handed — "é" comes out as "e" — and it is obvious on
playback. Pass it with --dialogue so the phrasing is built for you. A prompt describing only camera
motion produces a person mouthing nothing, which looks like broken lip sync but is really
a prompt that never asked for speech.

Auth resolution order:
  1. ~/.grok/auth.json           OAuth bearer written by `grok login` (subscription)
  2. $XAI_API_KEY                metered API key (falls back automatically)

On the OAuth path the `cost_in_usd_ticks` the API returns is figurative — the
metered-API equivalent of the render, not a charge, since the plan covers it.
The script labels it accordingly so a batch log showing "~$85" is not mistaken
for a bill.

The Zero Data Retention wrinkle
-------------------------------
Teams with ZDR enabled cannot have xAI hold the rendered file, so the API
refuses to generate unless the caller supplies `output.upload_url` — a
destination *we* own that it can PUT the result to:

    {"code":"invalid-argument",
     "error":"Zero Data Retention teams must provide output.upload_url for video generation."}

This is a requirement to satisfy, not a setting to disable — turning ZDR off to
make the call work would trade the team's data-retention posture for a
convenience. So the script adapts instead:

  1. First attempt goes out with no upload_url. Non-ZDR teams just work, and
     nothing gets exposed to the network.
  2. Only on that specific ZDR error does it bring up a short-lived PUT
     receiver, hand xAI the address, and tear the receiver down in a `finally`
     — an open PUT endpoint is not something to leave running.

Set GROK_UPLOAD_URL to a presigned PUT destination (R2, S3) to skip the local
receiver entirely. That is the right choice for unattended batch runs, where
opening a port per clip is worse than provisioning one bucket once.
"""
import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

API = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
AUTH_JSON = Path.home() / ".grok" / "auth.json"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
TICK = 1e-9


# ───────────────────────────────────────────────────────── auth
def resolve_auth():
    """Return (bearer, source). OAuth first — it spends subscription, not credits."""
    if AUTH_JSON.exists():
        try:
            rec = list(json.loads(AUTH_JSON.read_text()).values())[0]
            token, expires = rec.get("key"), rec.get("expires_at", "")
            if token:
                if expires:
                    exp = datetime.fromisoformat(expires.replace("Z", "+00:00").split(".")[0] + "+00:00")
                    if exp < datetime.now(timezone.utc):
                        print("  ⚠ Grok OAuth token expired — run `grok login --device-auth`",
                              file=sys.stderr)
                    else:
                        return token, "oauth (subscription)"
                else:
                    return token, "oauth (subscription)"
        except Exception as e:  # noqa: BLE001 — malformed auth.json must not be fatal
            print(f"  ⚠ could not read {AUTH_JSON}: {e}", file=sys.stderr)
    key = os.environ.get("XAI_API_KEY")
    if key:
        return key, "XAI_API_KEY (metered credits)"
    sys.exit("Error: no Grok auth. Run `grok login --device-auth` (subscription) "
             "or export XAI_API_KEY (metered).")


def post(path, payload, bearer):
    req = urllib.request.Request(API + path, data=json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + bearer,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def get(path, bearer):
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + bearer})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


# ───────────────────────────────────────────────────────── ZDR receiver
class Receiver(BaseHTTPRequestHandler):
    destination = None
    received = threading.Event()

    def do_PUT(self):
        remaining = int(self.headers.get("Content-Length") or 0)
        written = 0
        with open(Receiver.destination, "wb") as f:
            while remaining > 0:
                chunk = self.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                f.write(chunk); written += len(chunk); remaining -= len(chunk)
        self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
        print(f"  ← received {written/1e6:.1f} MB", flush=True)
        Receiver.received.set()

    do_POST = do_PUT

    def log_message(self, *a):  # keep the console readable
        pass


def free_port():
    with socket.socket() as s:
        s.bind(("", 0)); return s.getsockname()[1]


def public_host():
    host = os.environ.get("GROK_PUBLIC_HOST")
    if host:
        return host
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=15) as r:
            return r.read().decode().strip()
    except Exception:
        sys.exit("Error: could not determine this host's public IP. Set GROK_PUBLIC_HOST "
                 "or GROK_UPLOAD_URL (presigned PUT) instead.")


def ufw(action, port):
    """Best-effort firewall toggle. Absence of passwordless sudo is not fatal —
    the port may already be reachable, and failing here would be a false negative."""
    try:
        subprocess.run(["sudo", "-n", "ufw", action, f"{port}/tcp"],
                       capture_output=True, timeout=30)
    except Exception:
        pass


# ───────────────────────────────────────────────────────── generation
def submit(bearer, prompt, image_path, seconds, aspect, resolution, upload_url):
    payload = {"model": os.environ.get("GROK_VIDEO_MODEL", "grok-imagine-video-1.5"),
               "prompt": prompt, "duration": seconds, "resolution": resolution}
    if image_path:
        mime = "image/png" if str(image_path).lower().endswith(".png") else "image/jpeg"
        payload["image"] = {"url": f"data:{mime};base64," +
                            base64.b64encode(Path(image_path).read_bytes()).decode()}
    else:
        payload["aspect_ratio"] = aspect
    if upload_url:
        payload["output"] = {"upload_url": upload_url}
    return post("/videos/generations", payload, bearer)


def poll(bearer, request_id, timeout):
    """Poll until done. On timeout the job may still land — the caller is told the
    id so a rerun can check it instead of paying for a second render."""
    deadline, wait = time.time() + timeout, 6
    while time.time() < deadline:
        time.sleep(wait); wait = min(wait + 2, 20)
        state = get(f"/videos/{request_id}", bearer)
        status = state.get("status")
        if status == "done":
            return state
        if status in ("failed", "error"):
            sys.exit(f"Error: generation failed — {json.dumps(state)[:300]}")
        print(f"  …{status}", flush=True)
    sys.exit(f"Error: timed out after {timeout}s. Job {request_id} may still complete — "
             f"check GET {API}/videos/{request_id} before regenerating (avoids double billing).")


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        f.write(r.read())


def log_run(log_file, label, model, seconds, output, request_id, cost, source):
    if not log_file:
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    # "cost≈" on the OAuth line is deliberate: it is what the render would have cost
    # through the metered API, not money leaving the account.
    tag = f"cost≈${cost:.2f} (plan)" if source.startswith("oauth") else f"cost=${cost:.2f}"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"| {stamp} | {label} | {model} | {seconds}s | grok:{source.split()[0]} | "
                f"{output} | id={request_id} {tag} |\n")


def main():
    p = argparse.ArgumentParser(description="Generate a clip with Grok Imagine (subscription OAuth)")
    p.add_argument("--image", help="first frame — strongly preferred; text-only drifts identity")
    p.add_argument("--prompt"); p.add_argument("--prompt-file")
    p.add_argument("--dialogue", help="Spoken line, WITH accents. The model lip-syncs it and "
                                      "renders the audio natively — omit it and you get a "
                                      "person mouthing nothing in particular.")
    p.add_argument("--lang", default="portugues brasileiro",
                   help="Spoken language, named in the prompt (default: pt-BR; verified working)")
    p.add_argument("--output", required=True)
    p.add_argument("--seconds", type=int, default=8, help="1-15")
    p.add_argument("--aspect", default="9:16", help="text-to-video only")
    p.add_argument("--resolution", default="720p", choices=["480p", "720p"])
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--log-file"); p.add_argument("--label", default="grok-clip")
    a = p.parse_args()

    prompt = a.prompt or (Path(a.prompt_file).read_text().strip() if a.prompt_file else None)
    if not prompt:
        sys.exit("Error: --prompt or --prompt-file required")

    # Dialogue goes in the prompt as natural language with the line quoted — there is no
    # separate audio field. Spelling this out matters because the failure mode is silent:
    # a motion-only prompt renders someone talking convincingly about nothing, and it
    # reads as "the lip sync is broken" when nothing was ever asked to sync.
    if a.dialogue:
        # Write the line the way it is spelled, accents included. Stripping them to dodge
        # shell-escaping worries makes the model read what it was given: "é" becomes "e",
        # "começar" becomes "comecar". It is audible immediately and it is not a model
        # limitation — the same line with accents comes back correct through STT.
        sem_acento = set("áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ").isdisjoint(a.dialogue)
        if sem_acento and a.lang.startswith("portug") and len(a.dialogue) > 40:
            print("  ⚠ dialogue has no accented characters — if the line should have them, "
                  "the pronunciation will be wrong (é read as e)", file=sys.stderr)
        prompt = (f'{prompt}\n\nEla olha para a camera e fala com naturalidade, em {a.lang}, '
                  f'com sincronia labial precisa: "{a.dialogue}" '
                  f'Sem musica de fundo, sem texto na tela.')
    if a.image and not Path(a.image).exists():
        sys.exit(f"Error: image not found: {a.image}")

    bearer, source = resolve_auth()
    print(f"Grok video · auth: {source} · {a.seconds}s {a.resolution} "
          f"{'i2v' if a.image else 't2v ' + a.aspect}")

    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    preset_url = os.environ.get("GROK_UPLOAD_URL")
    server = None
    try:
        try:
            resp = submit(bearer, prompt, a.image, a.seconds, a.aspect, a.resolution, preset_url)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 401:
                sys.exit("Error: 401 — token invalid/expired. Run `grok login --device-auth`.")
            if e.code == 403 and "credit" in body.lower():
                sys.exit("Error: 403 — metered credits exhausted. OAuth (subscription) avoids "
                         "this: run `grok login --device-auth`.")
            if not (e.code == 400 and "upload_url" in body):
                sys.exit(f"Error: HTTP {e.code} — {body[:300]}")

            # ZDR path: stand up a receiver only now, and only for this call.
            port = int(os.environ.get("GROK_UPLOAD_PORT") or free_port())
            host = public_host()
            upload_url = f"http://{host}:{port}/{Path(a.output).name}"
            print(f"  ZDR team detected — receiving upload at {upload_url}")
            Receiver.destination = a.output
            Receiver.received.clear()
            server = ThreadingHTTPServer(("0.0.0.0", port), Receiver)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            ufw("allow", port)
            resp = submit(bearer, prompt, a.image, a.seconds, a.aspect, a.resolution, upload_url)

        request_id = resp.get("request_id") or resp.get("id")
        if not request_id:
            sys.exit(f"Error: no request_id in response — {json.dumps(resp)[:300]}")
        print(f"  job {request_id}")
        state = poll(bearer, request_id, a.timeout)
        cost = state.get("usage", {}).get("cost_in_usd_ticks", 0) * TICK

        if server:
            # xAI reports done once it has PUT the file; give the socket a moment to finish.
            if not Receiver.received.wait(timeout=120):
                sys.exit("Error: job finished but the upload never arrived. Check that "
                         f"port {port} is reachable from the internet.")
        else:
            url = state.get("url") or (state.get("video") or {}).get("url") \
                or (state.get("data") or [{}])[0].get("url")
            if not url:
                sys.exit(f"Error: done but no URL — {json.dumps(state)[:300]}")
            download(url, a.output)

        size = Path(a.output).stat().st_size
        # The API reports cost_in_usd_ticks on every render, including OAuth calls.
        # On the subscription path that figure is the metered-API equivalent, not a
        # charge — the render is covered by the plan. Labelling it plainly matters:
        # a 20-clip campaign logs ~$85 of "cost" that nobody is actually billed for,
        # and someone reading the log later should not cancel a run over it.
        if source.startswith("oauth"):
            print(f"  ✓ {a.output} ({size/1e6:.1f} MB) · ~${cost:.2f} metered-equivalent "
                  f"(covered by subscription, not charged)")
        else:
            print(f"  ✓ {a.output} ({size/1e6:.1f} MB) · billed ${cost:.2f} in credits")
        log_run(a.log_file, a.label, os.environ.get("GROK_VIDEO_MODEL", "grok-imagine-video-1.5"),
                a.seconds, a.output, request_id, cost, source)
    finally:
        if server:
            server.shutdown(); server.server_close()
            ufw("delete allow", server.server_address[1])
            print("  receiver closed, firewall rule removed")


if __name__ == "__main__":
    main()
