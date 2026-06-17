#!/usr/bin/env bash
set -euo pipefail

# Train a Higgsfield Soul reference for consistent creator identity across clips.
# Uploads 5-20 reference images, trains a Soul model, prints the reference id.
# The id goes into Soul V2 generation as custom_reference_id (same face every shot).
#
# Requires: higgsfield CLI authenticated (higgsfield auth login). Plus plan
# includes Soul V2 generations — training the ref is the one-time setup.
#
# Usage:
#   bash create-soul.sh --name Alice img1.png img2.png img3.png img4.png img5.png
#   bash create-soul.sh --name Alice --cinematic ./refs/*.png

NAME=""
MODEL_FLAG="--soul-2"   # default Soul 2.0; --cinematic switches to Soul Cinematic
TIMEOUT="45m"
IMAGES=()

usage() {
    echo "Usage: create-soul.sh --name NAME [--cinematic] IMAGE [IMAGE...]"
    echo ""
    echo "  --name NAME    Character name (required)"
    echo "  --cinematic    Train Soul Cinematic instead of Soul 2.0"
    echo "  --timeout DUR  Training timeout (default: 45m)"
    echo "  Provide 5-20 reference images of the same person."
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) NAME="$2"; shift 2 ;;
        --cinematic) MODEL_FLAG="--soul-cinematic"; shift ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        -h|--help) usage ;;
        -*) echo "Unknown option: $1"; usage ;;
        *) IMAGES+=("$1"); shift ;;
    esac
done

[[ -z "$NAME" ]] && { echo "Error: --name is required"; usage; }
command -v higgsfield >/dev/null 2>&1 || { echo "Error: higgsfield CLI not found (npm i -g @higgsfield/cli; higgsfield auth login)"; exit 2; }

# Soul training needs 5-20 images (CLI enforces too; fail early with a clear message).
if [[ ${#IMAGES[@]} -lt 5 || ${#IMAGES[@]} -gt 20 ]]; then
    echo "Error: provide 5-20 reference images (got ${#IMAGES[@]})" >&2
    exit 1
fi

# Upload each image, collecting the returned media ids.
IMAGE_ARGS=()
for img in "${IMAGES[@]}"; do
    [[ -f "$img" ]] || { echo "Error: not a file: $img" >&2; exit 1; }
    echo "Uploading $img..."
    UP=$(higgsfield upload create "$img" --json) || { echo "Upload failed: $img" >&2; exit 1; }
    ID=$(echo "$UP" | jq -r '[.. | .id? // empty] | first // empty')
    [[ -z "$ID" ]] && { echo "Error: no id from upload of $img" >&2; echo "$UP" >&2; exit 1; }
    IMAGE_ARGS+=(--image "$ID")
done

echo "Training Soul '$NAME' ($MODEL_FLAG) from ${#IMAGES[@]} images..."
CREATE=$(higgsfield soul-id create --name "$NAME" "$MODEL_FLAG" "${IMAGE_ARGS[@]}" --json) \
    || { echo "Soul create failed" >&2; exit 1; }
SOUL_ID=$(echo "$CREATE" | jq -r '[.. | .id? // empty] | first // empty')
[[ -z "$SOUL_ID" ]] && { echo "Error: no soul id in response" >&2; echo "$CREATE" >&2; exit 1; }

echo "Soul id: $SOUL_ID — waiting for training (timeout $TIMEOUT)..."
higgsfield soul-id wait "$SOUL_ID" --timeout "$TIMEOUT" || { echo "Training did not finish in $TIMEOUT" >&2; exit 1; }

echo ""
echo "Done. Soul reference ready: $SOUL_ID"
echo "Use it: higgsfield generate create text2image_soul_v2 --prompt \"...\" --custom_reference_id $SOUL_ID --wait"
