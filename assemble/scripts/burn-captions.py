#!/usr/bin/env python3
"""Burn timed captions with inline word highlights into a video via ASS + ffmpeg.

Single-pass approach: generate an ASS subtitle file from JSON config, then run
ffmpeg with the `ass=` filter to render captions onto the video. Faster than
multi-PNG overlay for tracks with many timed segments, and supports per-word
brand-color highlights, custom fonts, custom positioning — all config-driven.

Why this exists: `caption-overlay.py` produces transparent PNGs for static
overlays. For full caption tracks with timing AND inline highlights AND brand
colors that vary across campaigns, ASS is the right tool — and ffmpeg's
libass filter renders it natively in one pass.

Brand-agnostic: every visual property is config-driven. NO hardcoded colors,
fonts, sizes, or positions. Different campaigns swap config files, never code.

JSON config schema:
{
  "video_input": "path/to/raw.mp4",
  "video_output": "path/to/captioned.mp4",
  "style": {
    "font_family": "Arial Black",      # any system / fontconfig name
    "font_size": 44,
    "primary_color": "FFFFFF",         # hex RGB; default text color
    "outline_color": "000000",
    "outline_width": 3,
    "shadow": 2,
    "back_color": "80000000",          # ASS ABGR with alpha (semi-transparent black box)
    "border_style": 1,                  # 1 = outline+shadow, 3 = opaque box
    "alignment": 2,                     # ASS numpad alignment (1-9, 2 = bottom-center)
    "margin_v": 180,                    # vertical margin from edge
    "margin_lr": 30,
    "bold": true,
    "italic": false
  },
  "highlights": [
    {"text": "ANVISA", "color": "9BD97D"},
    {"text": "habeas corpus", "color": "9BD97D"},
    {"text": "Justiça", "color": "FFD700"}     # multi-color supported
  ],
  "captions": [
    {"start": "0:00:00.00", "end": "0:00:02.40", "text": "PERGUNTA QUE VEM\\NTODA SEMANA"},
    {"start": "0:00:02.40", "end": "0:00:04.50", "text": "MÃE PODE FAZER\\NHC PELO FILHO?"}
  ],
  "encode": {                            # all optional; defaults preserve mobile compat
    "vcodec": "libx264", "profile": "main", "pix_fmt": "yuv420p",
    "crf": 20, "preset": "medium",
    "acodec": "aac", "abitrate": "128k", "ar": 44100,
    "movflags": "+faststart"
  }
}

Use `\\N` in caption text for line breaks (ASS convention).

Usage:
    python3 burn-captions.py --config workspace/campaigns/<slug>/captions.json
    python3 burn-captions.py --config captions.json --keep-ass    # debug ASS output
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


# ─────────────────────────────────────────────
# Color conversion (RGB hex → ASS BGR)
# ─────────────────────────────────────────────
# ASS uses &HAABBGGRR (alpha-blue-green-red). RGB hex is RRGGBB.

def hex_to_ass_bgr(hex_color, default_alpha='00'):
    """Convert RGB hex (e.g. '9BD97D' or '#9BD97D') to ASS '&H<AA><BB><GG><RR>'.

    If hex is already 8 chars (AARRGGBB), preserves alpha.
    If hex is 6 chars (RRGGBB), uses default_alpha.
    Pass default_alpha='00' for opaque (ASS alpha is inverted: 00=opaque, FF=transparent).
    """
    h = hex_color.lstrip('#').lstrip('&').lstrip('H').upper()
    if len(h) == 8:
        a, r, g, b = h[0:2], h[2:4], h[4:6], h[6:8]
        return f'&H{a}{b}{g}{r}'
    if len(h) == 6:
        r, g, b = h[0:2], h[2:4], h[4:6]
        return f'&H{default_alpha}{b}{g}{r}'
    raise ValueError(f"Invalid hex color: {hex_color!r} (expected 6 or 8 hex chars)")


# ─────────────────────────────────────────────
# Inline highlight expansion
# ─────────────────────────────────────────────

def apply_inline_highlights(text, highlights):
    """Wrap matched highlight terms with ASS inline color override.

    Multiple highlights with different colors are supported. Matches are
    case-insensitive. Longer terms take precedence over shorter overlapping
    matches (so 'habeas corpus' wins over 'habeas').

    Each match becomes: {\\c<bgr>&}<original-text>{\\r}
    where \\r resets to the line's default style.
    """
    if not highlights or not text:
        return text

    # Sort by length descending — longer matches claim ranges first
    sorted_h = sorted(highlights, key=lambda h: len(h.get('text', '')), reverse=True)

    text_lower = text.lower()
    matches = []
    claimed = []

    for h in sorted_h:
        term = (h.get('text') or '').lower()
        if not term:
            continue
        color = hex_to_ass_bgr(h.get('color', 'FFFF00'))
        idx = 0
        while True:
            pos = text_lower.find(term, idx)
            if pos < 0:
                break
            end = pos + len(term)
            overlap = any(not (end <= cs or pos >= ce) for cs, ce in claimed)
            if not overlap:
                matches.append((pos, end, color))
                claimed.append((pos, end))
            idx = end

    if not matches:
        return text

    matches.sort()
    out = []
    cursor = 0
    for start, end, color in matches:
        if start > cursor:
            out.append(text[cursor:start])
        out.append(f"{{\\c{color}&}}")
        out.append(text[start:end])
        out.append("{\\r}")
        cursor = end
    if cursor < len(text):
        out.append(text[cursor:])
    return ''.join(out)


# ─────────────────────────────────────────────
# Video resolution detection
# ─────────────────────────────────────────────

def get_video_resolution(video_path):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'json', video_path],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        s = data['streams'][0]
        return int(s['width']), int(s['height'])
    except (FileNotFoundError, KeyError, json.JSONDecodeError, IndexError, subprocess.TimeoutExpired):
        return None


# ─────────────────────────────────────────────
# ASS file generation
# ─────────────────────────────────────────────

ASS_FORMAT_LINE = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
    "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
    "Alignment, MarginL, MarginR, MarginV, Encoding"
)

EVENTS_FORMAT_LINE = (
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
)


def build_style_line(style_dict):
    """Build a single ASS V4+ Style line from a style dict."""
    s = style_dict
    primary = hex_to_ass_bgr(s.get('primary_color', 'FFFFFF'))
    secondary = hex_to_ass_bgr(s.get('secondary_color', '0000FF'))
    outline_color = hex_to_ass_bgr(s.get('outline_color', '000000'))

    back = s.get('back_color', '80000000')
    if not back.upper().startswith('&H'):
        back = f'&H{back}'

    return (
        f"Style: {s['_name']},"
        f"{s.get('font_family', 'Arial Black')},"
        f"{int(s.get('font_size', 44))},"
        f"{primary},{secondary},{outline_color},{back},"
        f"{1 if s.get('bold', True) else 0},"
        f"{1 if s.get('italic', False) else 0},"
        f"0,0,100,100,0,0,"
        f"{int(s.get('border_style', 1))},"
        f"{int(s.get('outline_width', 3))},"
        f"{int(s.get('shadow', 2))},"
        f"{int(s.get('alignment', 2))},"
        f"{int(s.get('margin_lr', 30))},"
        f"{int(s.get('margin_lr', 30))},"
        f"{int(s.get('margin_v', 180))},"
        f"1"
    )


def build_ass(config, video_resolution):
    """Render full ASS file content from config dict."""
    width, height = video_resolution
    style = dict(config.get('style', {}))
    style['_name'] = 'Caption'

    captions = config.get('captions', [])
    highlights = config.get('highlights', [])

    style_caption_line = build_style_line(style)

    events = []
    for c in captions:
        start = c['start']
        end = c['end']
        text = apply_inline_highlights(c['text'], highlights)
        events.append(f"Dialogue: 0,{start},{end},Caption,,0,0,0,,{text}")

    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        f"{ASS_FORMAT_LINE}\n"
        f"{style_caption_line}\n"
        "\n"
        "[Events]\n"
        f"{EVENTS_FORMAT_LINE}\n"
        + "\n".join(events)
        + "\n"
    )


# ─────────────────────────────────────────────
# ffmpeg execution
# ─────────────────────────────────────────────

def run_ffmpeg(input_path, ass_path, output_path, encode):
    """Run ffmpeg with ass= filter and encode params."""
    # ffmpeg's ass filter requires the path to be passed in a way that
    # avoids special-character issues. Escape colons (Windows drive letters)
    # by replacing with \\:; backslashes too. For most Linux paths, plain works.
    ass_arg = ass_path.replace('\\', '\\\\').replace(':', r'\:')

    cmd = [
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'warning',
        '-i', input_path,
        '-vf', f"ass={ass_arg}",
        '-c:v', encode.get('vcodec', 'libx264'),
        '-profile:v', encode.get('profile', 'main'),
        '-pix_fmt', encode.get('pix_fmt', 'yuv420p'),
        '-crf', str(encode.get('crf', 20)),
        '-preset', encode.get('preset', 'medium'),
        '-movflags', encode.get('movflags', '+faststart'),
        '-c:a', encode.get('acodec', 'aac'),
        '-b:a', encode.get('abitrate', '128k'),
        '-ar', str(encode.get('ar', 44100)),
        output_path,
    ]
    subprocess.check_call(cmd)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Burn timed captions with inline highlights into a video (ASS+ffmpeg, single pass)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument('--config', required=True, help="JSON config file path")
    ap.add_argument('--keep-ass', action='store_true', help="Save the generated ASS file next to the output (for inspection/debugging)")
    args = ap.parse_args()

    config = json.loads(Path(args.config).read_text(encoding='utf-8'))

    input_path = config['video_input']
    output_path = config['video_output']
    encode = config.get('encode', {})

    if not Path(input_path).exists():
        print(f"Error: video_input not found: {input_path}", file=sys.stderr)
        sys.exit(2)

    res = get_video_resolution(input_path)
    if not res:
        print(f"Error: could not detect resolution for {input_path}", file=sys.stderr)
        sys.exit(2)

    ass_content = build_ass(config, res)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if args.keep_ass:
        ass_path = str(Path(output_path).with_suffix('.ass'))
        Path(ass_path).write_text(ass_content, encoding='utf-8')
    else:
        with tempfile.NamedTemporaryFile('w', suffix='.ass', delete=False, encoding='utf-8') as f:
            f.write(ass_content)
            ass_path = f.name

    try:
        run_ffmpeg(input_path, ass_path, output_path, encode)
        print(json.dumps({
            'ok': True,
            'output': output_path,
            'resolution': f'{res[0]}x{res[1]}',
            'captions': len(config.get('captions', [])),
            'highlights': len(config.get('highlights', [])),
            'ass_kept': args.keep_ass,
        }, indent=2))
    finally:
        if not args.keep_ass and os.path.exists(ass_path):
            os.unlink(ass_path)


if __name__ == '__main__':
    main()
