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
# Levels and bands are calibrated against measured Sora 2 output, not guessed. In the
# gaps between words a Sora clip carries roughly -3 dB at 50-300 Hz, -15 dB at 1-3 kHz
# and -15 dB at 3-7 kHz (arbitrary but consistent scale). A first attempt at -30 dB
# lowpassed to 1.8 kHz landed ~20 dB short and never touched the presence band, so it
# was inaudible — the whole point of the layer is lost if you only add rumble.
#
# Two noise sources per preset: brown for the low bed, a quieter high-passed white for
# air and presence. A room is not just rumble; it is rumble plus the hiss a phone mic
# makes, and the ear reads the second one as "recorded somewhere real".
case "$PRESET" in
  office) LOWF="highpass=f=45,lowpass=f=600";  LOWV="-12dB"; HIV="-33dB"; HIF="highpass=f=1200,lowpass=f=7000" ;;
  room)   LOWF="highpass=f=60,lowpass=f=600";  LOWV="-15dB"; HIV="-36dB"; HIF="highpass=f=1500,lowpass=f=6500" ;;
  street) LOWF="highpass=f=30,lowpass=f=800";  LOWV="-9dB";  HIV="-30dB"; HIF="highpass=f=1000,lowpass=f=7500" ;;
  *) echo "Unknown preset: $PRESET (office | room | street)" >&2; exit 1 ;;
esac

OUTPUT="${OUTPUT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/assets/audio/room-tone-${PRESET}.wav}"
mkdir -p "$(dirname "$OUTPUT")"

FADE=$(awk -v d="$SECONDS_DUR" 'BEGIN{printf "%.2f", d-1.5}')
ffmpeg -v error \
  -f lavfi -i "anoisesrc=color=brown:duration=${SECONDS_DUR}:sample_rate=44100:amplitude=0.5" \
  -f lavfi -i "anoisesrc=color=white:duration=${SECONDS_DUR}:sample_rate=44100:amplitude=0.5" \
  -filter_complex "[0:a]${LOWF},volume=${LOWV}[lo];[1:a]${HIF},volume=${HIV}[hi];\
[lo][hi]amix=inputs=2:duration=first:normalize=0,\
afade=t=in:st=0:d=1.5,afade=t=out:st=${FADE}:d=1.5[out]" \
  -map "[out]" -ac 1 "$OUTPUT" -y

MEAN=$(ffmpeg -hide_banner -nostats -i "$OUTPUT" -af volumedetect -f null - 2>&1 \
       | grep -oP 'mean_volume: \K[-0-9.]+')
echo "✓ $OUTPUT · ${SECONDS_DUR}s · preset ${PRESET} · mean ${MEAN} dB"
echo "  Use with: post-production.sh --ambient \"$OUTPUT\" --ambient-vol 0.8"
