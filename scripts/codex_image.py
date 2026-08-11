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

1. The output path is requested, not controlled. Codex honours an absolute path when
   asked plainly, so that is what this does; left to itself it invents a descriptive
   name under ./generated/. A locator covers the miss, scoped to the working directory
   and to ~/.codex minus the caches.

2. Reference images work, and well — verified. The tool takes `referenced_image_paths`,
   and a portrait generated from the creator reference held the face, the glasses and
   the hair convincingly at 941x1672. So Codex is a real option for first frames, not
   only for text cards.

3. The sandbox has to be off ON THIS HOST. Codex normally runs tool calls inside its
   bundled bubblewrap, which here dies with:

       bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted

   and every file read fails with it — including the reference image. Every sandbox
   mode routes through that helper, so `--sandbox danger-full-access` is the only
   setting that works. That flag lets the model run shell commands unsandboxed, which
   is a real trade: it is defensible here because the prompt is narrow and the host is
   the operator's own, but it should be a conscious choice. Pass --sandboxed to keep
   the sandbox on where bubblewrap does work.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def newest_candidate(roots, since):
    """Last-resort locator for when Codex ignores the requested path.

    Deliberately narrow: plugin and skill caches under ~/.codex hold hundreds of
    preview.png assets, and Codex refreshes them mid-session. An earlier version
    diffed that tree and cheerfully returned a template thumbnail as the generated
    image — a false success is worse than a clean failure."""
    best = None
    for root in roots:
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if (f.suffix.lower() in EXTS and f.is_file()
                    and f.stat().st_mtime >= since
                    and "/plugins/cache/" not in str(f)
                    and "/skills/" not in str(f)):
                if best is None or f.stat().st_mtime > best.stat().st_mtime:
                    best = f
    return best


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
    p.add_argument("--reference", help="reference image — holds face, hair and glasses well")
    p.add_argument("--sandboxed", action="store_true",
                   help="keep Codex's sandbox on. Off by default because its bundled "
                        "bubblewrap cannot start on this host and every file read fails.")
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

    out = Path(a.output_file).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.time() - 5          # small slack: mtime vs. wall clock
    instruction = (
        f"Use your built-in image_gen tool to generate exactly one image.\n"
        f"Prompt: {prompt}\n"
        f"Size: {a.size}. Quality: {a.quality}.\n"
        f"Save the result at exactly this absolute path: {out}\n"
    )
    if a.reference:
        ref = Path(a.reference).resolve()
        if not ref.exists():
            sys.exit(f"Error: reference not found: {ref}")
        # The tool parameter is referenced_image_paths; naming it beats describing it,
        # because "use this image as a reference" alone sometimes gets read as prose.
        instruction += (f"Pass referenced_image_paths=[\"{ref}\"] so the subject keeps the "
                        f"same face, hair and glasses as that photo.\n")
    # Codex is a coding agent first: told only "make an image", it sometimes decides the
    # helpful thing is to write a script that calls an image API. Ruling that out keeps
    # the run on the subscription instead of silently reaching for a metered key.
    instruction += ("Do not write code, do not install anything, do not call any external "
                    "API — use image_gen only, then report the path of the file you made.")

    cmd = ["codex", "exec", "--skip-git-repo-check"]
    if not a.sandboxed:
        cmd += ["--sandbox", "danger-full-access"]
    cmd.append(instruction)

    print(f"Codex image · GPT Image · {a.size} · quality {a.quality}"
          + ("" if a.sandboxed else " · sandbox off (bubblewrap unavailable here)"))
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout)
    except subprocess.TimeoutExpired:
        sys.exit(f"Error: codex exec timed out after {a.timeout}s")

    combined = (run.stdout or "") + (run.stderr or "")
    if not out.exists():
        found = newest_candidate([Path.cwd(), CODEX_HOME], started)
        if found:
            shutil.copy(found, out)
            print(f"  (Codex saved to {found}; moved into place)")
        else:
            hint = ""
            if "bwrap" in combined or "sandbox helper failed" in combined:
                hint = ("\n  The sandbox helper failed — rerun without --sandboxed, or fix "
                        "bubblewrap on this host.")
            sys.exit("Error: no image was produced.\n"
                     "  Codex may have refused, exhausted plan quota, or answered with code "
                     "instead of calling image_gen." + hint +
                     f"\n  Output tail:\n{combined[-700:]}")

    size = out.stat().st_size
    print(f"  ✓ {out} ({size/1e3:.0f} KB) · covered by the ChatGPT plan")

    if a.log_file:
        Path(a.log_file).parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(a.log_file, "a", encoding="utf-8") as f:
            f.write(f"| {stamp} | {a.label} | gpt-image (codex) | still | codex:oauth | "
                    f"{a.output_file} | plan |\n")


if __name__ == "__main__":
    main()
