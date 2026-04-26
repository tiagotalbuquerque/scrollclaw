#!/usr/bin/env bash
set -euo pipefail

# Generate B-roll video via Seedance 2.0 on fal.ai (primary) with Kling 3 fallback.
# Refactor 2026-04: trocou Kling 3 → Seedance 2.0 (mais barato no tier fast,
# áudio nativo bundled, 4–15s, 480/720/1080p).
# Replicate Kling Omni mantido como fallback de resiliência.
# Supports text-to-video, image-to-video, and reference-to-video.
#
# Usage:
#   # Text to video (Seedance 2.0 fast — default)
#   bash generate-broll.sh --prompt-file scene.txt --output broll.mp4 --seconds 5
#
#   # Image to video (Seedance 2.0 i2v)
#   bash generate-broll.sh --image product.png --prompt-file motion.txt --output broll.mp4
#
#   # Standard quality (mais caro, mais detalhe)
#   bash generate-broll.sh --quality standard --prompt-file scene.txt --output broll.mp4
#
#   # Forçar Kling 3 (legado)
#   bash generate-broll.sh --model kling --prompt-file scene.txt --output broll.mp4

PROMPT_FILE=""
IMAGE=""
OUTPUT=""
SECONDS_DUR="5"
ASPECT_RATIO="9:16"
GENERATE_AUDIO="true"
PROVIDER="fal"
QUALITY="fast"      # fast | standard
MODEL_FAMILY="seedance"  # seedance | kling
RESOLUTION="720p"
POLL_INTERVAL=10
TIMEOUT=600
LOG_FILE=""
LABEL="broll"

usage() {
    echo "Usage: generate-broll.sh --prompt-file FILE --output FILE [options]"
    echo ""
    echo "Options:"
    echo "  --prompt-file FILE    Scene/motion description (required)"
    echo "  --image FILE          Start image URL for i2v mode (optional)"
    echo "  --output FILE         Output video path (required)"
    echo "  --seconds N           Duration in seconds, 4-15 (default: 5)"
    echo "  --aspect-ratio RATIO  Aspect ratio (default: 9:16)"
    echo "  --resolution RES      480p | 720p | 1080p (default: 720p)"
    echo "  --quality LEVEL       fast (~\$0.024/s) or standard (~\$0.30/s) — Seedance only"
    echo "  --model FAMILY        seedance (default) | kling (legacy via fal)"
    echo "  --no-audio            Disable audio generation"
    echo "  --provider NAME       fal (default) or replicate (Replicate Kling fallback)"
    echo "  --log-file FILE       Append to output log"
    echo "  --label TEXT          Label for log entry"
    echo "  --timeout N           Timeout in seconds (default: 600)"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prompt-file) PROMPT_FILE="$2"; shift 2 ;;
        --image) IMAGE="$2"; shift 2 ;;
        --output) OUTPUT="$2"; shift 2 ;;
        --seconds) SECONDS_DUR="$2"; shift 2 ;;
        --aspect-ratio) ASPECT_RATIO="$2"; shift 2 ;;
        --resolution) RESOLUTION="$2"; shift 2 ;;
        --quality) QUALITY="$2"; shift 2 ;;
        --model) MODEL_FAMILY="$2"; shift 2 ;;
        --no-audio) GENERATE_AUDIO="false"; shift ;;
        --provider) PROVIDER="$2"; shift 2 ;;
        --log-file) LOG_FILE="$2"; shift 2 ;;
        --label) LABEL="$2"; shift 2 ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

[[ -z "$PROMPT_FILE" ]] && { echo "Error: --prompt-file is required"; usage; }
[[ -z "$OUTPUT" ]] && { echo "Error: --output is required"; usage; }

PROMPT=$(cat "$PROMPT_FILE")

# Validate duration (Seedance 2.0 = 4–15s; Kling = 5–10s)
if [[ "$SECONDS_DUR" -lt 4 || "$SECONDS_DUR" -gt 15 ]]; then
    echo "Error: --seconds must be between 4 and 15 (Seedance 2.0 range)" >&2
    exit 1
fi

# Resolve fal.ai endpoint based on model family + quality + image
fal_endpoint() {
    if [[ "$MODEL_FAMILY" == "kling" ]]; then
        # Legacy Kling 3 path (fallback if Seedance is unavailable)
        if [[ -n "$IMAGE" ]]; then
            echo "https://queue.fal.run/fal-ai/kling-video/v3/pro/image-to-video"
        else
            echo "https://queue.fal.run/fal-ai/kling-video/v3/pro/text-to-video"
        fi
        return
    fi
    # Seedance 2.0 (default)
    local prefix="bytedance/seedance-2.0"
    if [[ "$QUALITY" == "fast" ]]; then
        prefix="${prefix}/fast"
    fi
    if [[ -n "$IMAGE" ]]; then
        echo "https://queue.fal.run/${prefix}/image-to-video"
    else
        echo "https://queue.fal.run/${prefix}/text-to-video"
    fi
}

# ---------------------------------------------------------------------------
# fal.ai provider — Seedance 2.0 (default) or Kling 3 (--model kling)
# ---------------------------------------------------------------------------
generate_fal() {
    [[ -z "${FAL_KEY:-}" ]] && { echo "Error: FAL_KEY not set"; exit 2; }

    ENDPOINT=$(fal_endpoint)
    local mode_desc
    if [[ -n "$IMAGE" ]]; then mode_desc="image-to-video"; else mode_desc="text-to-video"; fi

    if [[ "$MODEL_FAMILY" == "seedance" ]]; then
        # Seedance 2.0 schema: prompt, resolution, duration (string), aspect_ratio, generate_audio, image_url (i2v)
        # Note: Seedance uses "resolution" + duration as string; Kling uses "duration" as int
        if [[ -n "$IMAGE" ]]; then
            INPUT_JSON=$(jq -n \
                --arg prompt "$PROMPT" \
                --arg image "$IMAGE" \
                --arg duration "$SECONDS_DUR" \
                --arg ar "$ASPECT_RATIO" \
                --arg res "$RESOLUTION" \
                --argjson audio "$GENERATE_AUDIO" \
                '{prompt: $prompt, image_url: $image, duration: $duration, aspect_ratio: $ar, resolution: $res, generate_audio: $audio}')
        else
            INPUT_JSON=$(jq -n \
                --arg prompt "$PROMPT" \
                --arg duration "$SECONDS_DUR" \
                --arg ar "$ASPECT_RATIO" \
                --arg res "$RESOLUTION" \
                --argjson audio "$GENERATE_AUDIO" \
                '{prompt: $prompt, duration: $duration, aspect_ratio: $ar, resolution: $res, generate_audio: $audio}')
        fi
        echo "Mode: ${mode_desc} (Seedance 2.0 ${QUALITY} via fal.ai)"
    else
        # Kling 3 legacy schema (kept for emergency fallback)
        if [[ -n "$IMAGE" ]]; then
            INPUT_JSON=$(jq -n \
                --arg prompt "$PROMPT" \
                --arg image "$IMAGE" \
                --argjson duration "$SECONDS_DUR" \
                --arg ar "$ASPECT_RATIO" \
                --argjson audio "$GENERATE_AUDIO" \
                '{prompt: $prompt, start_image_url: $image, duration: $duration, aspect_ratio: $ar, generate_audio: $audio}')
        else
            INPUT_JSON=$(jq -n \
                --arg prompt "$PROMPT" \
                --argjson duration "$SECONDS_DUR" \
                --arg ar "$ASPECT_RATIO" \
                --argjson audio "$GENERATE_AUDIO" \
                '{prompt: $prompt, duration: $duration, aspect_ratio: $ar, generate_audio: $audio}')
        fi
        echo "Mode: ${mode_desc} (Kling 3 legacy via fal.ai)"
    fi

    echo "Endpoint: $ENDPOINT"
    echo "Duration: ${SECONDS_DUR}s | Aspect: $ASPECT_RATIO | Resolution: $RESOLUTION | Audio: $GENERATE_AUDIO"

    # Submit to queue
    RESPONSE=$(curl -s -X POST "$ENDPOINT" \
        -H "Authorization: Key $FAL_KEY" \
        -H "Content-Type: application/json" \
        -d "$INPUT_JSON")

    # Use the exact status_url returned by fal.ai — do NOT construct manually
    STATUS_URL=$(echo "$RESPONSE" | jq -r '.status_url // empty')
    REQUEST_ID=$(echo "$RESPONSE" | jq -r '.request_id // empty')

    if [[ -z "$STATUS_URL" || -z "$REQUEST_ID" ]]; then
        echo "Error submitting to fal.ai:" >&2
        echo "$RESPONSE" | jq . >&2
        exit 1
    fi

    echo "Request: $REQUEST_ID"
    echo "Polling: $STATUS_URL"

    # Poll using the exact status_url
    START_TIME=$(date +%s)
    while true; do
        POLL=$(curl -s -H "Authorization: Key $FAL_KEY" "$STATUS_URL")
        STATUS=$(echo "$POLL" | jq -r '.status')

        case "$STATUS" in
            COMPLETED)
                echo "Generation complete!"
                # Fetch the result from the response_url
                RESPONSE_URL=$(echo "$RESPONSE" | jq -r '.response_url // empty')
                if [[ -n "$RESPONSE_URL" ]]; then
                    RESULT=$(curl -s -H "Authorization: Key $FAL_KEY" "$RESPONSE_URL")
                else
                    RESULT="$POLL"
                fi
                break
                ;;
            FAILED)
                ERROR=$(echo "$POLL" | jq -r '.error // "unknown error"')
                echo "Generation failed: $ERROR" >&2
                return 1
                ;;
            *)
                ELAPSED=$(( $(date +%s) - START_TIME ))
                if [[ $ELAPSED -gt $TIMEOUT ]]; then
                    echo "Timeout after ${TIMEOUT}s" >&2
                    return 1
                fi
                echo "  Status: $STATUS (${ELAPSED}s elapsed)"
                sleep "$POLL_INTERVAL"
                ;;
        esac
    done

    # Download output — fal.ai returns video url in .video.url
    VIDEO_URL=$(echo "$RESULT" | jq -r '.video.url // empty')
    if [[ -z "$VIDEO_URL" || "$VIDEO_URL" == "null" ]]; then
        echo "Error: No video URL in response" >&2
        echo "$RESULT" | jq . >&2
        return 1
    fi

    mkdir -p "$(dirname "$OUTPUT")"
    curl -s -o "$OUTPUT" "$VIDEO_URL"
    echo "Saved: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"

    # Log
    if [[ -n "$LOG_FILE" ]]; then
        local model_name
        if [[ "$MODEL_FAMILY" == "seedance" ]]; then
            if [[ "$QUALITY" == "fast" ]]; then
                model_name="bytedance/seedance-2.0/fast"
            else
                model_name="bytedance/seedance-2.0"
            fi
        else
            model_name="fal-ai/kling-v3-pro"
        fi
        log_entry "$model_name" "$REQUEST_ID"
    fi

    echo ""
    echo "Done. Request ID: $REQUEST_ID"
}

# ---------------------------------------------------------------------------
# Replicate provider (fallback)
# ---------------------------------------------------------------------------
generate_replicate() {
    [[ -z "${REPLICATE_API_TOKEN:-}" ]] && { echo "Error: REPLICATE_API_TOKEN not set"; exit 2; }

    MODEL="kwaivgi/kling-v3-omni-video"

    # Build input JSON
    INPUT_JSON=$(jq -n \
        --arg prompt "$PROMPT" \
        --argjson duration "$SECONDS_DUR" \
        --arg ar "$ASPECT_RATIO" \
        '{prompt: $prompt, duration: $duration, aspect_ratio: $ar}')

    if [[ -n "$IMAGE" ]]; then
        INPUT_JSON=$(echo "$INPUT_JSON" | jq --arg img "$IMAGE" '. + {start_image: $img}')
        echo "Mode: image-to-video (Kling Omni via Replicate)"
    else
        echo "Mode: text-to-video (Kling Omni via Replicate)"
    fi

    PAYLOAD=$(jq -n --argjson input "$INPUT_JSON" '{input: $input}')

    echo "Model: $MODEL"
    echo "Duration: ${SECONDS_DUR}s | Aspect: $ASPECT_RATIO"

    RESPONSE=$(curl -s -X POST "https://api.replicate.com/v1/models/${MODEL}/predictions" \
        -H "Authorization: Token $REPLICATE_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD")

    PREDICTION_ID=$(echo "$RESPONSE" | jq -r '.id // empty')
    GET_URL=$(echo "$RESPONSE" | jq -r '.urls.get // empty')

    if [[ -z "$PREDICTION_ID" || -z "$GET_URL" ]]; then
        echo "Error creating prediction:" >&2
        echo "$RESPONSE" | jq . >&2
        exit 1
    fi

    echo "Prediction: $PREDICTION_ID"
    echo "Polling..."

    START_TIME=$(date +%s)
    while true; do
        POLL=$(curl -s -H "Authorization: Token $REPLICATE_API_TOKEN" "$GET_URL")
        STATUS=$(echo "$POLL" | jq -r '.status')

        case "$STATUS" in
            succeeded)
                echo "Generation complete!"
                break
                ;;
            failed|canceled)
                ERROR=$(echo "$POLL" | jq -r '.error // "unknown error"')
                echo "Generation $STATUS: $ERROR" >&2
                exit 1
                ;;
            *)
                ELAPSED=$(( $(date +%s) - START_TIME ))
                if [[ $ELAPSED -gt $TIMEOUT ]]; then
                    echo "Timeout after ${TIMEOUT}s" >&2
                    exit 1
                fi
                echo "  Status: $STATUS (${ELAPSED}s elapsed)"
                sleep "$POLL_INTERVAL"
                ;;
        esac
    done

    # Download output
    mkdir -p "$(dirname "$OUTPUT")"
    VIDEO_URL=$(echo "$POLL" | jq -r '.output // empty')
    if [[ -n "$VIDEO_URL" && "$VIDEO_URL" != "null" ]]; then
        curl -s -o "$OUTPUT" "$VIDEO_URL"
        echo "Saved: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
    else
        echo "Error: No output URL in response" >&2
        echo "$POLL" | jq '.output' >&2
        exit 1
    fi

    # Log
    if [[ -n "$LOG_FILE" ]]; then
        log_entry "$MODEL" "$PREDICTION_ID"
    fi

    echo ""
    echo "Done. Prediction ID: $PREDICTION_ID"
}

# ---------------------------------------------------------------------------
# Shared logging
# ---------------------------------------------------------------------------
log_entry() {
    local model="$1"
    local run_id="$2"
    TIMESTAMP=$(date -Iseconds)
    mkdir -p "$(dirname "$LOG_FILE")"
    if [[ ! -f "$LOG_FILE" ]]; then
        echo '# Output Log' > "$LOG_FILE"
        echo '' >> "$LOG_FILE"
        echo '| Timestamp | Label | Model | Seconds | Provider | Output | Notes |' >> "$LOG_FILE"
        echo '|---|---|---|---|---|---|---|' >> "$LOG_FILE"
    fi
    echo "| $TIMESTAMP | $LABEL | $model | $SECONDS_DUR | $PROVIDER | $OUTPUT | id=$run_id |" >> "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# Dispatch — B-roll graceful degradation chain (refator 2026-04 v2)
#
# Default cadeia: Seedance fast → Seedance standard → Kling 3 fal → Kling 3 Replicate
#
# Custos por segundo @720p (ordem da preferência):
#   Seedance 2.0 fast t2v  $0.024/s   ← primary (best cost/quality)
#   Seedance 2.0 fast i2v  $0.15/s
#   Seedance 2.0 standard  $0.30/s    ← se fast falhar
#   Kling 3 fal i2v        $0.084-0.168/s ← se Seedance família cair
#   Kling 3 Replicate      ~$0.10-0.15/s estimate ← emergency
#
# B-roll é tipicamente sem rosto humano, então Seedance i2v aceita normalmente
# (filter de "likeness" só ativa em rostos pessoa).
# ---------------------------------------------------------------------------

# Wrapper para tentar Seedance standard (não fast) caso fast falhe
generate_fal_standard() {
    local SAVED_QUALITY="$QUALITY"
    QUALITY="standard"
    local result
    if generate_fal; then
        result=0
    else
        result=1
    fi
    QUALITY="$SAVED_QUALITY"
    return $result
}

# Wrapper para tentar Kling 3 fal.ai caso Seedance família falhe
generate_fal_kling() {
    local SAVED_FAMILY="$MODEL_FAMILY"
    MODEL_FAMILY="kling"
    local result
    if generate_fal; then
        result=0
    else
        result=1
    fi
    MODEL_FAMILY="$SAVED_FAMILY"
    return $result
}

case "$PROVIDER" in
    fal)
        # Default chain: Seedance fast → Seedance standard → Kling fal → Kling Replicate
        if ! generate_fal; then
            echo ""
            if [[ "$MODEL_FAMILY" == "seedance" && "$QUALITY" == "fast" ]]; then
                echo "Seedance fast failed — trying Seedance standard..."
                if ! generate_fal_standard; then
                    echo ""
                    echo "Seedance standard failed — falling back to Kling 3 fal.ai..."
                    if ! generate_fal_kling; then
                        echo ""
                        echo "Kling 3 fal.ai failed — falling back to Kling 3 Replicate..."
                        PROVIDER="replicate"
                        generate_replicate
                    fi
                fi
            elif [[ "$MODEL_FAMILY" == "seedance" ]]; then
                echo "Seedance standard failed — falling back to Kling 3 fal.ai..."
                if ! generate_fal_kling; then
                    PROVIDER="replicate"
                    generate_replicate
                fi
            else
                # MODEL_FAMILY=kling já, cair direto pra Replicate
                echo "Kling 3 fal.ai failed — falling back to Kling 3 Replicate..."
                PROVIDER="replicate"
                generate_replicate
            fi
        fi
        ;;
    replicate)
        generate_replicate
        ;;
    *)
        echo "Error: unknown provider '$PROVIDER' (use 'fal' or 'replicate')" >&2
        exit 1
        ;;
esac
