# Video API Reference (B-roll) — Seedance 2.0

Single source of truth for B-roll generation. Refator 2026-04 substituiu Kling 3 por Seedance 2.0 ByteDance. Validado em smoke test 2026-04-25.

**Primary provider:** fal.ai (gateway único)
**Fallback chain in `generate-broll.sh`:** Seedance 2.0 fal.ai → Kling 3 Replicate

---

## fal.ai — Seedance 2.0 (B-ROLL)

### Endpoints

| Use | Endpoint | Tier |
|-----|----------|------|
| Text-to-Video fast | `bytedance/seedance-2.0/fast/text-to-video` | ~$0.024/s @720p |
| Text-to-Video standard | `bytedance/seedance-2.0/text-to-video` | ~$0.30/s @720p |
| Image-to-Video fast | `bytedance/seedance-2.0/fast/image-to-video` | ~$0.15/s @720p |
| Image-to-Video standard | `bytedance/seedance-2.0/image-to-video` | ~$0.30/s @720p |

### Queue workflow

```bash
# Submit
curl -s -X POST "https://queue.fal.run/bytedance/seedance-2.0/fast/text-to-video" \
  -H "Authorization: Key $FAL_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Slow cinematic push-in on judge gavel resting on legal book...",
    "duration": "5",
    "aspect_ratio": "9:16",
    "resolution": "720p",
    "generate_audio": false
  }'
# Response: {"request_id":"...","status_url":"...","response_url":"..."}

# Poll using returned status_url
curl -s -H "Authorization: Key $FAL_KEY" "$STATUS_URL"
# {"status":"COMPLETED"} → fetch response_url for {"video":{"url":"..."}}
```

### Important schema differences vs Kling 3

| Field | Kling 3 | Seedance 2.0 |
|-------|---------|--------------|
| Image start | `start_image_url` | `image_url` |
| Duration type | int (3–15) | **string** (4–15) |
| Resolution | implicit by tier | explicit `resolution` field |
| Audio | `generate_audio` boolean | `generate_audio` boolean (bundled native) |
| Aspect ratios | `9:16`, `16:9`, `1:1` | `9:16`, `16:9`, `1:1`, `4:3`, `3:4`, `21:9` |

### Validated parameters (smoke test 2026-04-25)

- `duration: "5"` (string, NOT int) — 4 to 15
- `resolution: "720p"` — also accepts `480p`, `1080p`
- `aspect_ratio: "9:16"` — funciona out of the box
- `generate_audio: false` (B-roll doesn't need it; A-roll passes `true`)
- Generation time: ~92s wall time for 5s @720p

### Output format

Returned MP4: H.264, 720×1280 (9:16), 24 fps, ~1.4 MB for 5s. Solid quality for environment shots and product demos. Native audio bundled if `generate_audio: true` (no separate ElevenLabs needed for B-roll).

---

## Kling 3 (legacy, mantido como fallback emergência via Replicate)

`kwaivgi/kling-v3-omni-video` no Replicate é o único path Kling 3 retido. Usado só se fal.ai estiver fora — chamado via `generate_replicate_kling()` em `scripts/generate-clip.sh` e `scripts/generate-broll.sh`.

Chamado automaticamente se as duas tentativas fal.ai falharem. Para forçar:

```bash
bash scripts/generate-clip.sh --provider kling-replicate ...
bash scripts/generate-broll.sh --provider replicate ...
```

---

## A-roll (Sora 2) — não muda

Sora 2 via fal.ai (`fal-ai/sora-2/image-to-video`, `fal-ai/sora-2-pro/image-to-video`) continua sendo o default para A-roll talking head. Documentado em `animate/references/sora-api.md`.

Fallback A-roll: Seedance 2.0 standard (não fast) com `generate_audio: true` para sustentar diálogo. Acionado automaticamente se Sora 2 falhar.

---

## Pricing summary (abr/2026)

| Modelo | $/segundo @720p | Notas |
|--------|----------------|-------|
| Sora 2 i2v | $0.10 | Native voice + lip sync |
| Sora 2 Pro i2v | $0.30 | Higher quality talking head |
| Seedance 2.0 fast t2v | $0.024 | **B-roll default** |
| Seedance 2.0 fast i2v | $0.15 | Environment-matched B-roll |
| Seedance 2.0 t2v | $0.30 | A-roll fallback (audio bundled) |
| Seedance 2.0 i2v | $0.30 | A-roll fallback i2v |
| Kling 3 Pro i2v | $0.112 (no audio) / $0.168 (audio) | Replicate fallback |

Para 1 vídeo padrão (1 first-frame + 15s Sora + 3×5s B-roll Seedance fast + 500 chars ElevenLabs): ~**$2.17** total.
