#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPT Image stills for ScrollClaw via the Codex CLI — billed to the ChatGPT plan.

A second subscription-billed still provider alongside grok_image.py. Where each wins:

  Grok Image 2.0   locks a face from a reference image → first frames of a creator
  GPT Image 2      renders legible typography, Portuguese accents included →
                   text cards, Wall of Text pieces, hook frames with burned-in copy

Grok writes crooked, invented words when asked for text; GPT Image does not. That
difference, not raw quality, is what should decide between them.

Why the CLI instead of the REST API
-----------------------------------
The Codex OAuth token does not authenticate api.openai.com — it returns 401. The CLI
is what holds the session, and its built-in `image_gen` tool is what reaches the
model. So every call goes through `codex exec`; there is no HTTP request to build.

Two consequences worth knowing before wiring this into a batch:

1. `image_gen` takes no output path. It writes into the session directory under
   ~/.codex, so this script snapshots that tree before and after the call and moves
   whatever is new. Fragile by nature — if Codex ever changes where it writes, this
   is the part that breaks.

2. Reference-image support is UNVERIFIED. Codex was not authenticated when this was
   written, so whether `image_gen` can lock a face from a reference the way Grok's
   /images/edits does is an open question. --reference is passed through to the
   prompt and the script says plainly that it could not confirm the result. Until
   someone checks, use Grok for anything needing a consistent face.
"""
import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def images_on_disk():
    if not CODEX_HOME.exists():
        return set()
    return {p for p in CODEX_HOME.rglob("*") if p.suffix.lower() in EXTS and p.is_file()}


def require_codex():
    if not shutil.which("codex"):
        sys.exit("Error: Codex CLI not installed.\n  sudo npm install -g @openai/codex")
    status = subprocess.run(["codex", "login", "status"], capture_output=True,
                            text=True, timeout=60)
    out = (status.stdout + status.stderr).lower()
    # Check the exit code, and test for the negative phrase rather than the positive one:
    # "logged in" is a substring of "not logged in", so the obvious membership test passes
    # when it should fail — and the run then hangs inside `codex exec` waiting on an auth
    # prompt that never comes.
    if status.returncode != 0 or "not logged in" in out:
        sys.exit("Error: Codex is not authenticated.\n"
                 "  The login is OAuth in a browser — it has to be the operator, and it\n"
                 "  cannot be automated. Ask them to run:  codex login\n"
                 "  Then rerun. The session persists in ~/.codex.")


def main():
    p = argparse.ArgumentParser(description="Generate a still with GPT Image via Codex OAuth")
    p.add_argument("--prompt"); p.add_argument("--prompt-file")
    p.add_argument("--output-file", required=True)
    p.add_argument("--reference", help="reference image (support UNVERIFIED — see module docstring)")
    p.add_argument("--size", default="1024x1536",
                   help="1024x1024 | 1024x1536 (portrait) | 1536x1024 | auto")
    p.add_argument("--quality", default="high", choices=["low", "medium", "high", "auto"])
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--log-file"); p.add_argument("--label", default="first-frame")
    a = p.parse_args()

    prompt = a.prompt or (Path(a.prompt_file).read_text().strip() if a.prompt_file else None)
    if not prompt:
        sys.exit("Error: --prompt or --prompt-file required")
    require_codex()

    before = images_on_disk()

    instruction = (
        f"Use your built-in image_gen tool to generate exactly one image.\n"
        f"Prompt: {prompt}\n"
        f"Size: {a.size}. Quality: {a.quality}.\n"
    )
    if a.reference:
        ref = Path(a.reference).resolve()
        if not ref.exists():
            sys.exit(f"Error: reference not found: {ref}")
        instruction += (f"Use the image at {ref} as a visual reference for the subject's "
                        f"appearance — same face, same hair, same glasses.\n")
    # Codex is a coding agent first: told only "make an image", it sometimes decides the
    # helpful thing is to write a script that calls an image API. Ruling that out keeps
    # the run on the subscription instead of silently reaching for a metered key.
    instruction += ("Do not write code, do not install anything, do not call any external "
                    "API — use image_gen only, then report the path of the file you made.")

    print(f"Codex image · GPT Image · {a.size} · quality {a.quality}")
    try:
        run = subprocess.run(["codex", "exec", "--skip-git-repo-check", instruction],
                             capture_output=True, text=True, timeout=a.timeout)
    except subprocess.TimeoutExpired:
        sys.exit(f"Error: codex exec timed out after {a.timeout}s")

    new = sorted(images_on_disk() - before, key=lambda p: p.stat().st_mtime)
    if not new:
        tail = (run.stdout or run.stderr or "")[-700:]
        sys.exit("Error: no new image appeared under ~/.codex.\n"
                 "  Codex may have refused, run out of plan quota, or tried to solve it\n"
                 f"  by writing code instead of using image_gen. Output tail:\n{tail}")

    Path(a.output_file).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(new[-1], a.output_file)
    size = Path(a.output_file).stat().st_size
    print(f"  ✓ {a.output_file} ({size/1e3:.0f} KB) · covered by the ChatGPT plan")
    if a.reference:
        print("  ⚠ reference support is unverified — check the face before trusting it "
              "across a series; grok_image.py is the proven path for identity locking",
              file=sys.stderr)

    if a.log_file:
        Path(a.log_file).parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(a.log_file, "a", encoding="utf-8") as f:
            f.write(f"| {stamp} | {a.label} | gpt-image (codex) | still | codex:oauth | "
                    f"{a.output_file} | plan |\n")


if __name__ == "__main__":
    main()
