#!/usr/bin/env bash
set -euo pipefail

# Generate a video clip via Sora 2 (primary) with Seedance 2.0 (fal) and Replicate Kling fallbacks.
# Refactor 2026-04: substitui Kling 3 fal.ai por Seedance 2.0 na cadeia. Replicate
# Sora foi sunsetted no upstream — removido. Replicate Kling mantido como
# emergency fallback.
# Supports text-to-video and image-to-video (first frame).
#
# Fallback chain (default --provider fal):
#   Sora 2 fal.ai → Seedance 2.0 fal.ai → Kling 3 Replicate
#
# Usage:
#   # Text to video (default — tenta Sora, depois Seedance, depois Replicate Kling)
#   bash generate-clip.sh --prompt-file scene.txt --output clip.mp4 --seconds 8
#
#   # Image to video (first frame i2v)
#   bash generate-clip.sh --image frame1.png --prompt-file motion.txt --output clip.mp4 --seconds 8
#
#   # Pular Sora, usar Seedance direto (caso Sora caia)
#   bash generate-clip.sh --provider seedance --prompt-file scene.txt --output clip.mp4 --seconds 8

PROMPT_FILE=""
IMAGE=""
OUTPUT=""
SECONDS_DUR="8"
ASPECT_RATIO="portrait"
PRO="false"
PROVIDER="fal"
POLL_INTERVAL=10
TIMEOUT=600
LOG_FILE=""
LABEL="clip"

usage() {
    echo "Usage: generate-clip.sh --prompt-file FILE --output FILE [options]"
    echo ""
    echo "Options:"
    echo "  --prompt-file FILE    Scene/motion description (required)"
    echo "  --image FILE          First frame image for i2v mode (optional)"
    echo "  --output FILE         Output video path (required)"
    echo "  --seconds N           Duration in seconds (default: 8)"
    echo "  --aspect-ratio RATIO  portrait or landscape (default: portrait)"
    echo "  --pro                 Use Pro model tier"
    echo "  --provider NAME       fal | kling | seedance | kling-replicate (default: fal)"
    echo "                        fal: tenta Sora → Kling fal → Kling Replicate"
    echo "                        kling: pula Sora, vai direto para Kling fal → Kling Replicate"
    echo "                        seedance: força Seedance 2.0 (AVISO: i2v rejeita rostos)"
    echo "                        kling-replicate: emergência, vai direto para Kling Replicate"
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
        --pro) PRO="true"; shift ;;
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

# ---------------------------------------------------------------------------
# Shared: normalize aspect ratio before dispatch
# ---------------------------------------------------------------------------
MAPPED_ASPECT="$ASPECT_RATIO"
[[ "$ASPECT_RATIO" == "portrait" ]] && MAPPED_ASPECT="9:16"
[[ "$ASPECT_RATIO" == "landscape" ]] && MAPPED_ASPECT="16:9"

# ---------------------------------------------------------------------------
# Shared logging helper
# ---------------------------------------------------------------------------
log_entry() {
    local model="$1"
    local run_id="$2"
    local dur="$3"
    TIMESTAMP=$(date -Iseconds)
    mkdir -p "$(dirname "$LOG_FILE")"
    if [[ ! -f "$LOG_FILE" ]]; then
        echo '# Output Log' > "$LOG_FILE"
        echo '' >> "$LOG_FILE"
        echo '| Timestamp | Label | Model | Seconds | Provider | Output | Notes |' >> "$LOG_FILE"
        echo '|---|---|---|---|---|---|---|' >> "$LOG_FILE"
    fi
    echo "| $TIMESTAMP | $LABEL | $model | $dur | $PROVIDER | $OUTPUT | id=$run_id |" >> "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# fal.ai Sora 2 provider
# ---------------------------------------------------------------------------
generate_fal() {
    [[ -z "${FAL_KEY:-}" ]] && { echo "Error: FAL_KEY not set"; exit 2; }

    # fal.ai Sora duration must be one of: 4, 8, 12, 16, 20
    FAL_DURATION="$SECONDS_DUR"

    # Choose endpoint
    if [[ -n "$IMAGE" ]]; then
        if [[ "$PRO" == "true" ]]; then
            ENDPOINT="https://queue.fal.run/fal-ai/sora-2/image-to-video/pro"
        else
            ENDPOINT="https://queue.fal.run/fal-ai/sora-2/image-to-video"
        fi
        INPUT_JSON=$(jq -n \
            --arg prompt "$PROMPT" \
            --arg image "$IMAGE" \
            --argjson duration "$FAL_DURATION" \
            --arg ar "$MAPPED_ASPECT" \
            '{prompt: $prompt, image_url: $image, duration: $duration, aspect_ratio: $ar}')
        echo "Mode: image-to-video (Sora i2v via fal.ai)"
    else
        if [[ "$PRO" == "true" ]]; then
            ENDPOINT="https://queue.fal.run/fal-ai/sora-2/text-to-video/pro"
        else
            ENDPOINT="https://queue.fal.run/fal-ai/sora-2/text-to-video"
        fi
        INPUT_JSON=$(jq -n \
            --arg prompt "$PROMPT" \
            --argjson duration "$FAL_DURATION" \
            --arg ar "$MAPPED_ASPECT" \
            '{prompt: $prompt, duration: $duration, aspect_ratio: $ar}')
        echo "Mode: text-to-video (Sora t2v via fal.ai)"
    fi

    echo "Endpoint: $ENDPOINT"
    echo "Duration: ${FAL_DURATION}s | Aspect: $MAPPED_ASPECT"

    # Submit to queue
    RESPONSE=$(curl -s -X POST "$ENDPOINT" \
        -H "Authorization: Key $FAL_KEY" \
        -H "Content-Type: application/json" \
        -d "$INPUT_JSON")

    # Use the exact status_url returned by fal.ai
    STATUS_URL=$(echo "$RESPONSE" | jq -r '.status_url // empty')
    REQUEST_ID=$(echo "$RESPONSE" | jq -r '.request_id // empty')

    if [[ -z "$STATUS_URL" || -z "$REQUEST_ID" ]]; then
        echo "Error submitting to fal.ai (Sora):" >&2
        echo "$RESPONSE" | jq . >&2
        return 1
    fi

    echo "Request: $REQUEST_ID"
    echo "Polling: $STATUS_URL"

    START_TIME=$(date +%s)
    while true; do
        POLL=$(curl -s -H "Authorization: Key $FAL_KEY" "$STATUS_URL")
        STATUS=$(echo "$POLL" | jq -r '.status')

        case "$STATUS" in
            COMPLETED)
                echo "Generation complete!"
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
                echo "Sora generation failed: $ERROR" >&2
                return 1
                ;;
            *)
                ELAPSED=$(( $(date +%s) - START_TIME ))
                if [[ $ELAPSED -gt $TIMEOUT ]]; then
                    echo "Sora timeout after ${TIMEOUT}s" >&2
                    return 1
                fi
                echo "  Status: $STATUS (${ELAPSED}s elapsed)"
                sleep "$POLL_INTERVAL"
                ;;
        esac
    done

    # Download output
    mkdir -p "$(dirname "$OUTPUT")"
    VIDEO_URL=$(echo "$RESULT" | jq -r '.video.url // empty')
    if [[ -z "$VIDEO_URL" || "$VIDEO_URL" == "null" ]]; then
        echo "Error: No video URL in Sora response" >&2
        echo "$RESULT" | jq . >&2
        return 1
    fi

    curl -s -o "$OUTPUT" "$VIDEO_URL"
    echo "Saved: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"

    # Log
    if [[ -n "$LOG_FILE" ]]; then
        local model="fal-ai/sora-2"
        [[ "$PRO" == "true" ]] && model="fal-ai/sora-2-pro"
        log_entry "$model" "$REQUEST_ID" "$FAL_DURATION"
    fi

    echo ""
    echo "Done. Request ID: $REQUEST_ID"
}

# ---------------------------------------------------------------------------
# fal.ai Seedance 2.0 provider (A-roll fallback) — refator 2026-04
# Substitui Kling 3. Áudio nativo bundled, mais barato.
# ---------------------------------------------------------------------------
generate_seedance_fal() {
    [[ -z "${FAL_KEY:-}" ]] && { echo "Error: FAL_KEY not set"; exit 2; }

    # Seedance 2.0 duration: 4-15 seconds (clamp if outside range)
    SEED_DURATION="$SECONDS_DUR"
    if [[ "$SEED_DURATION" -gt 15 ]]; then
        echo "Warning: Duration clamped from ${SEED_DURATION}s to 15s (Seedance max)" >&2
        SEED_DURATION=15
    fi
    if [[ "$SEED_DURATION" -lt 4 ]]; then
        echo "Warning: Duration clamped from ${SEED_DURATION}s to 4s (Seedance min)" >&2
        SEED_DURATION=4
    fi

    # A-roll: usa Seedance standard (não fast) por qualidade de talking head
    # Schema: prompt, image_url (i2v), duration (string), aspect_ratio, resolution, generate_audio
    if [[ -n "$IMAGE" ]]; then
        ENDPOINT="https://queue.fal.run/bytedance/seedance-2.0/image-to-video"
        # IMAGE pode ser URL ou path local; assumir que upstream passou URL ou já uploadou
        INPUT_JSON=$(jq -n \
            --arg prompt "$PROMPT" \
            --arg image "$IMAGE" \
            --arg duration "$SEED_DURATION" \
            --arg ar "$MAPPED_ASPECT" \
            '{prompt: $prompt, image_url: $image, duration: $duration, aspect_ratio: $ar, resolution: "720p", generate_audio: true}')
        echo "Mode: image-to-video (Seedance 2.0 i2v via fal.ai)"
    else
        ENDPOINT="https://queue.fal.run/bytedance/seedance-2.0/text-to-video"
        INPUT_JSON=$(jq -n \
            --arg prompt "$PROMPT" \
            --arg duration "$SEED_DURATION" \
            --arg ar "$MAPPED_ASPECT" \
            '{prompt: $prompt, duration: $duration, aspect_ratio: $ar, resolution: "720p", generate_audio: true}')
        echo "Mode: text-to-video (Seedance 2.0 t2v via fal.ai)"
    fi

    echo "Endpoint: $ENDPOINT"
    echo "Duration: ${SEED_DURATION}s | Aspect: $MAPPED_ASPECT | Resolution: 720p | Audio: true"

    # Submit to queue
    RESPONSE=$(curl -s -X POST "$ENDPOINT" \
        -H "Authorization: Key $FAL_KEY" \
        -H "Content-Type: application/json" \
        -d "$INPUT_JSON")

    # Use the exact status_url returned by fal.ai
    STATUS_URL=$(echo "$RESPONSE" | jq -r '.status_url // empty')
    REQUEST_ID=$(echo "$RESPONSE" | jq -r '.request_id // empty')

    if [[ -z "$STATUS_URL" || -z "$REQUEST_ID" ]]; then
        echo "Error submitting to fal.ai (Kling):" >&2
        echo "$RESPONSE" | jq . >&2
        return 1
    fi

    echo "Request: $REQUEST_ID"
    echo "Polling: $STATUS_URL"

    START_TIME=$(date +%s)
    while true; do
        POLL=$(curl -s -H "Authorization: Key $FAL_KEY" "$STATUS_URL")
        STATUS=$(echo "$POLL" | jq -r '.status')

        case "$STATUS" in
            COMPLETED)
                echo "Generation complete!"
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
                echo "Seedance generation failed: $ERROR" >&2
                return 1
                ;;
            *)
                ELAPSED=$(( $(date +%s) - START_TIME ))
                if [[ $ELAPSED -gt $TIMEOUT ]]; then
                    echo "Seedance timeout after ${TIMEOUT}s" >&2
                    return 1
                fi
                echo "  Status: $STATUS (${ELAPSED}s elapsed)"
                sleep "$POLL_INTERVAL"
                ;;
        esac
    done

    # Download output
    VIDEO_URL=$(echo "$RESULT" | jq -r '.video.url // empty')
    if [[ -z "$VIDEO_URL" || "$VIDEO_URL" == "null" ]]; then
        echo "Error: No video URL in Seedance response" >&2
        echo "$RESULT" | jq . >&2
        return 1
    fi

    mkdir -p "$(dirname "$OUTPUT")"
    curl -s -o "$OUTPUT" "$VIDEO_URL"
    echo "Saved: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"

    # Log
    if [[ -n "$LOG_FILE" ]]; then
        log_entry "bytedance/seedance-2.0" "$REQUEST_ID" "$SEED_DURATION"
    fi

    echo ""
    echo "Done. Request ID: $REQUEST_ID"
}

# ---------------------------------------------------------------------------
# fal.ai Kling 3 provider (A-roll fallback intermediário) — refator 2026-04
# Substitui o slot que era Seedance i2v. Motivação: Seedance 2.0 i2v rejeita
# silenciosamente first frames com rosto humano (smoke test 2026-04-25),
# então pular Seedance no A-roll e ir direto para Kling 3 fal.ai quando Sora
# falhar.
# ---------------------------------------------------------------------------
generate_kling_fal() {
    [[ -z "${FAL_KEY:-}" ]] && { echo "Error: FAL_KEY not set"; exit 2; }

    KLING_DURATION="$SECONDS_DUR"
    if [[ "$KLING_DURATION" -gt 15 ]]; then
        echo "Warning: Duration clamped from ${KLING_DURATION}s to 15s (Kling max)" >&2
        KLING_DURATION=15
    fi
    if [[ "$KLING_DURATION" -lt 3 ]]; then
        echo "Warning: Duration clamped from ${KLING_DURATION}s to 3s (Kling min)" >&2
        KLING_DURATION=3
    fi

    if [[ -n "$IMAGE" ]]; then
        ENDPOINT="https://queue.fal.run/fal-ai/kling-video/v3/pro/image-to-video"
        INPUT_JSON=$(jq -n \
            --arg prompt "$PROMPT" \
            --arg image "$IMAGE" \
            --argjson duration "$KLING_DURATION" \
            --arg ar "$MAPPED_ASPECT" \
            '{prompt: $prompt, start_image_url: $image, duration: $duration, aspect_ratio: $ar, generate_audio: true}')
        echo "Mode: image-to-video (Kling 3 i2v via fal.ai)"
    else
        ENDPOINT="https://queue.fal.run/fal-ai/kling-video/v3/pro/text-to-video"
        INPUT_JSON=$(jq -n \
            --arg prompt "$PROMPT" \
            --argjson duration "$KLING_DURATION" \
            --arg ar "$MAPPED_ASPECT" \
            '{prompt: $prompt, duration: $duration, aspect_ratio: $ar, generate_audio: true}')
        echo "Mode: text-to-video (Kling 3 t2v via fal.ai)"
    fi

    echo "Endpoint: $ENDPOINT"
    echo "Duration: ${KLING_DURATION}s | Aspect: $MAPPED_ASPECT | Audio: true"

    RESPONSE=$(curl -s -X POST "$ENDPOINT" \
        -H "Authorization: Key $FAL_KEY" \
        -H "Content-Type: application/json" \
        -d "$INPUT_JSON")
    STATUS_URL=$(echo "$RESPONSE" | jq -r '.status_url // empty')
    REQUEST_ID=$(echo "$RESPONSE" | jq -r '.request_id // empty')

    if [[ -z "$STATUS_URL" || -z "$REQUEST_ID" ]]; then
        echo "Error submitting to fal.ai (Kling):" >&2
        echo "$RESPONSE" | jq . >&2
        return 1
    fi
    echo "Request: $REQUEST_ID"
    echo "Polling: $STATUS_URL"

    START_TIME=$(date +%s)
    while true; do
        POLL=$(curl -s -H "Authorization: Key $FAL_KEY" "$STATUS_URL")
        STATUS=$(echo "$POLL" | jq -r '.status // empty')
        case "$STATUS" in
            COMPLETED)
                echo "Generation complete!"
                RESPONSE_URL=$(echo "$RESPONSE" | jq -r '.response_url // empty')
                if [[ -n "$RESPONSE_URL" ]]; then
                    RESULT=$(curl -s -H "Authorization: Key $FAL_KEY" "$RESPONSE_URL")
                else
                    RESULT="$POLL"
                fi
                break ;;
            FAILED|ERROR)
                ERROR=$(echo "$POLL" | jq -r '.error // "unknown"')
                echo "Kling generation failed: $ERROR" >&2
                return 1 ;;
            ""|null)
                # fal.ai content filter silent reject — body empty
                ELAPSED=$(( $(date +%s) - START_TIME ))
                if [[ $ELAPSED -gt 90 ]]; then
                    echo "Kling fal.ai silent reject (likely content filter) after ${ELAPSED}s" >&2
                    return 1
                fi
                sleep "$POLL_INTERVAL" ;;
            *)
                ELAPSED=$(( $(date +%s) - START_TIME ))
                if [[ $ELAPSED -gt $TIMEOUT ]]; then
                    echo "Kling timeout after ${TIMEOUT}s" >&2
                    return 1
                fi
                echo "  Status: $STATUS (${ELAPSED}s elapsed)"
                sleep "$POLL_INTERVAL" ;;
        esac
    done

    VIDEO_URL=$(echo "$RESULT" | jq -r '.video.url // empty')
    if [[ -z "$VIDEO_URL" || "$VIDEO_URL" == "null" ]]; then
        echo "Error: No video URL in Kling response" >&2
        echo "$RESULT" | jq . >&2
        return 1
    fi
    mkdir -p "$(dirname "$OUTPUT")"
    curl -s -o "$OUTPUT" "$VIDEO_URL"
    echo "Saved: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
    if [[ -n "$LOG_FILE" ]]; then
        log_entry "fal-ai/kling-v3-pro" "$REQUEST_ID" "$KLING_DURATION"
    fi
    echo ""
    echo "Done. Request ID: $REQUEST_ID"
}

# ---------------------------------------------------------------------------
# Replicate Kling provider (final fallback) — Sora-on-Replicate removida no
# refator 2026-04 (sunset upstream)
# ---------------------------------------------------------------------------
generate_replicate_kling() {
    [[ -z "${REPLICATE_API_TOKEN:-}" ]] && { echo "Error: REPLICATE_API_TOKEN not set"; exit 2; }

    MODEL="kwaivgi/kling-v3-omni-video"

    # Kling duration: 3-15 seconds (clamp if outside range)
    KLING_DURATION="$SECONDS_DUR"
    if [[ "$KLING_DURATION" -gt 15 ]]; then
        echo "Warning: Duration clamped from ${KLING_DURATION}s to 15s (Kling max)" >&2
        KLING_DURATION=15
    fi
    if [[ "$KLING_DURATION" -lt 3 ]]; then
        echo "Warning: Duration clamped from ${KLING_DURATION}s to 3s (Kling min)" >&2
        KLING_DURATION=3
    fi

    # Build input JSON — A-roll needs audio for dialogue sync
    INPUT_JSON=$(jq -n \
        --arg prompt "$PROMPT" \
        --argjson duration "$KLING_DURATION" \
        --arg ar "$MAPPED_ASPECT" \
        '{prompt: $prompt, duration: $duration, aspect_ratio: $ar, generate_audio: true}')

    if [[ -n "$IMAGE" ]]; then
        if [[ "$IMAGE" == http* ]]; then
            INPUT_JSON=$(echo "$INPUT_JSON" | jq --arg img "$IMAGE" '. + {start_image: $img}')
        else
            echo "Uploading first frame to Replicate..."
            UPLOAD_RESP=$(curl -s -X POST "https://api.replicate.com/v1/files" \
                -H "Authorization: Token $REPLICATE_API_TOKEN" \
                -F "content=@$IMAGE" \
                -F "content_type=image/png")
            FILE_URL=$(echo "$UPLOAD_RESP" | jq -r '.urls.get // empty')
            if [[ -z "$FILE_URL" ]]; then
                echo "Error uploading file:" >&2
                echo "$UPLOAD_RESP" | jq . >&2
                exit 1
            fi
            echo "Uploaded: $FILE_URL"
            INPUT_JSON=$(echo "$INPUT_JSON" | jq --arg img "$FILE_URL" '. + {start_image: $img}')
        fi
        echo "Mode: image-to-video (Kling Omni via Replicate)"
    else
        echo "Mode: text-to-video (Kling Omni via Replicate)"
    fi

    PAYLOAD=$(jq -n --argjson input "$INPUT_JSON" '{input: $input}')

    echo "Model: $MODEL"
    echo "Duration: ${KLING_DURATION}s | Aspect: $MAPPED_ASPECT | Audio: true"

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
        log_entry "$MODEL" "$PREDICTION_ID" "$KLING_DURATION"
    fi

    echo ""
    echo "Done. Prediction ID: $PREDICTION_ID"
}

# ---------------------------------------------------------------------------
# Dispatch by provider — A-roll graceful degradation chain (refator 2026-04 v2)
#
# Default cadeia: Sora 2 fal → Kling 3 fal → Kling 3 Replicate
#
# Pular Seedance 2.0 i2v aqui porque rejeita silenciosamente first frames com
# rosto humano (validado smoke test 2026-04-25). Para B-roll sem rosto,
# Seedance é preferível — vê generate-broll.sh.
#
# Custos por segundo @720p (ordem da preferência):
#   Sora 2 i2v ............ $0.10/s   ← primary, melhor lip sync
#   Kling 3 fal i2v ....... $0.084-0.168/s (com áudio)  ← fallback
#   Kling 3 Replicate ..... ~$0.10-0.15/s estimate ← emergency
# ---------------------------------------------------------------------------
case "$PROVIDER" in
    fal)
        if ! generate_fal; then
            echo ""
            echo "Sora 2 fal.ai failed — falling back to Kling 3 fal.ai..."
            if ! generate_kling_fal; then
                echo ""
                echo "Kling 3 fal.ai failed — falling back to Kling 3 Replicate..."
                PROVIDER="replicate"
                generate_replicate_kling
            fi
        fi
        ;;
    kling)
        # Pular Sora — útil se Sora estiver fora
        if ! generate_kling_fal; then
            echo ""
            echo "Kling 3 fal.ai failed — falling back to Kling 3 Replicate..."
            PROVIDER="replicate"
            generate_replicate_kling
        fi
        ;;
    seedance)
        # AVISO: Seedance 2.0 i2v rejeita first frames com rosto humano
        # (content_policy: likeness of real people). Use só se tiver certeza
        # que NÃO há rosto AI no first frame. Para t2v sem first frame OK.
        echo "WARNING: Seedance 2.0 i2v rejects human faces — use only for non-face A-roll." >&2
        if ! generate_seedance_fal; then
            echo ""
            echo "Seedance fal.ai failed — falling back to Kling 3 fal.ai..."
            if ! generate_kling_fal; then
                PROVIDER="replicate"
                generate_replicate_kling
            fi
        fi
        ;;
    kling-replicate)
        # Emergência: vai direto para Kling 3 no Replicate
        generate_replicate_kling
        ;;
    *)
        echo "Error: unknown provider '$PROVIDER' (use 'fal', 'kling', 'seedance', or 'kling-replicate')" >&2
        exit 1
        ;;
esac
