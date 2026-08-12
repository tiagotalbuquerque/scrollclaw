#!/usr/bin/env bash
set -euo pipefail

# make-room-tone.sh — synthesise the ambience layer post-production expects.
#
# The doctrine calls clean studio audio an anti-pattern and tells you to add room
# ambience, but the repo ships none: *.wav is gitignored, so a committed asset would
# not survive a clone. Generating it is better anyway — no binary blob, and the tone
# is reproducible and tunable per campaign.
#
# What "room tone" means here: the noise floor a phone picks up in a real room. Air
# handling, distant building hum, the mic's own hiss. Nobody should notice it; they
# notice its absence, which is what makes AI audio sound synthetic. A generated clip
# sits near digital silence (measured -77 dBFS on a Grok talking head); real phone
# footage sits closer to -55.
#
# Usage:
#   bash scripts/make-room-tone.sh                       # 40s office tone
#   bash scripts/make-room-tone.sh --preset room --seconds 60 --output tone.wav
#
# Then: post-production.sh --ambient <file> --ambient-vol 0.8

PRESET="office"; SECONDS_DUR=40; OUTPUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --preset)  PRESET="$2";       shift 2 ;;
    --seconds) SECONDS_DUR="$2";  shift 2 ;;
    --output)  OUTPUT="$2";       shift 2 ;;
    -h|--help) sed -n '3,20p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# Each preset is a band-limit plus a level. Brown noise (not white) because a room's
# floor is weighted to the low end; the lowpass keeps it from reading as hiss.
case "$PRESET" in
  office) FILTER="highpass=f=45,lowpass=f=1800";  LEVEL="-30dB" ;;  # HVAC + building hum
  room)   FILTER="highpass=f=60,lowpass=f=2400";  LEVEL="-32dB" ;;  # quiet domestic room
  street) FILTER="highpass=f=30,lowpass=f=3000";  LEVEL="-26dB" ;;  # window onto traffic
  *) echo "Unknown preset: $PRESET (office | room | street)" >&2; exit 1 ;;
esac

OUTPUT="${OUTPUT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/assets/audio/room-tone-${PRESET}.wav}"
mkdir -p "$(dirname "$OUTPUT")"

FADE=$(awk -v d="$SECONDS_DUR" 'BEGIN{printf "%.2f", d-1.5}')
ffmpeg -v error -f lavfi \
  -i "anoisesrc=color=brown:duration=${SECONDS_DUR}:sample_rate=44100:amplitude=0.35" \
  -af "${FILTER},volume=${LEVEL},afade=t=in:st=0:d=1.5,afade=t=out:st=${FADE}:d=1.5" \
  -ac 1 "$OUTPUT" -y

MEAN=$(ffmpeg -hide_banner -nostats -i "$OUTPUT" -af volumedetect -f null - 2>&1 \
       | grep -oP 'mean_volume: \K[-0-9.]+')
echo "✓ $OUTPUT · ${SECONDS_DUR}s · preset ${PRESET} · mean ${MEAN} dB"
echo "  Use with: post-production.sh --ambient \"$OUTPUT\" --ambient-vol 0.8"
