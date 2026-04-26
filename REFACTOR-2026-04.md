# ScrollClaw Refactor — abr/2026

Fork: `tiagotalbuquerque/scrollclaw` (origin) ← upstream `TheMattBerman/scrollclaw`. Branch: `refactor/2026-04-direct-apis`. Motivação: o projeto upstream foi desenhado em dez/2025 — desde então saíram modelos novos e APIs diretas que evitam intermediários (Replicate, OpenRouter).

## Objetivos (revisão 2 — 2026-04-25 22:10)

1. **Eliminar Replicate** — first frame migra de Replicate (`google/nano-banana-2`) para **fal.ai** (`fal-ai/nano-banana-2`). Mesmo modelo Google por baixo, gateway consolidado com Sora/Seedance. Validado por curl: 768×1376 PNG em ~10s.
2. **Eliminar OpenRouter** — virality scoring vai para Gemini API direct (modelos texto rodam free tier) com fallback OpenAI GPT-5.5.
3. **Substituir Kling 3 → Seedance 2.0** para B-roll (mantém fal.ai como gateway, troca de model slug).
4. **Adicionar ElevenLabs** — chave recuperada de `~/.config/acessoverde/av-video-keys.env` ✅.

**Resultado da consolidação:** 1 única chave paga obrigatória (`FAL_KEY`) + 1 free (`GEMINI_API_KEY` texto) + 1 opcional (`ELEVENLABS_API_KEY` já em mãos). Replicate e OpenRouter completamente eliminados.

## Mapa de substituições

| Etapa | Hoje (upstream) | Refator (este fork) | Mudança de custo |
|---|---|---|---|
| First frame | Replicate `google/nano-banana-2` (~$0.005/img) | fal.ai `fal-ai/nano-banana-2` | Mesmo modelo, gateway consolidado com Sora/Seedance — drop Replicate |
| A-roll Talking Head | Sora 2 via fal.ai (~$0.10–0.15/s) | **Mantém** Sora 2 via fal.ai | Sem mudança — Sora native voice + lip sync ainda é melhor que TTS + V2V |
| A-roll fallback | Kling 3 via fal.ai | Seedance 2.0 standard via fal.ai (~$0.30/s) ou Kling 3 (~$0.10/s) | Configurável; default Seedance pela qualidade + áudio nativo bundled |
| B-roll | Kling 3 via fal.ai (`fal-ai/kling-video/v3/pro`) | Seedance 2.0 fast via fal.ai (`bytedance/seedance-2.0/fast/text-to-video` ~$0.024/s ou `image-to-video` ~$0.15/s) | **Mais barato** no tier fast; 480/720/1080p, áudio nativo grátis |
| Virality score | OpenRouter → Gemini | Gemini direct (`gemini-3-pro` ou `gemini-2.5-pro`) | Default Gemini 2.5 Pro (mais barato), config flag para GPT-5.5 |
| Voice consistency | ElevenLabs S2S (já era opcional) | ElevenLabs S2S (chave da prod) | Sem mudança no provedor |

## Arquivos a tocar (10)

### Bloco 1 — Configuração
- `scripts/check-deps.sh` — **remove** `REPLICATE_API_TOKEN` obrigatório; **adiciona** `GEMINI_API_KEY` obrigatório (virality, texto free tier); **mantém** `FAL_KEY` obrigatório; opcional `OPENAI_API_KEY` (alt virality) e `ELEVENLABS_API_KEY`.
- `.env.example` — criar com novo conjunto de chaves.

### Bloco 2 — First frame (Replicate → fal.ai)
- `scripts/generate-first-frame.py` — reescrita para chamar fal.ai (`https://fal.run/fal-ai/nano-banana-2`) com `Authorization: Key $FAL_KEY`. Response retorna `images[0].url` (URL do CDN fal-media), download imediato. Modelo default: `fal-ai/nano-banana-2`. Env override: `FIRST_FRAME_MODEL`.

### Bloco 3 — Vídeo (Kling → Seedance 2.0)
- `scripts/generate-broll.sh` — troca endpoint `fal-ai/kling-video/v3/pro` por `bytedance/seedance-2.0/fast` (default) e `bytedance/seedance-2.0` (pro). Adiciona flag `--quality fast|standard`.
- `scripts/generate-clip.sh` — adiciona provider `seedance` (text-to-video e image-to-video). Mantém `sora` como default para A-roll talking head. Remove `replicate` provider (não usaremos mais Replicate em lugar nenhum).
- `scripts/extend-clip.sh` — atualiza para Seedance se aplicável.
- `b-roll/references/kling-api.md` → renomeia para `seedance-api.md` e reescreve.
- `b-roll/references/orchestrator.md` — atualiza model refs.
- `animate/references/sora-api.md` — atualiza fallback refs (Sora → Seedance).

### Bloco 4 — Virality (OpenRouter → Gemini direct)
- `score/references/virality-scoring.md` — reescreve seção "vision model" para indicar Gemini API direct (`gemini-2.5-pro` default, `gpt-5.5` alt).
- `score/SKILL.md` — atualiza orquestração caso descreva prompts de scoring.

### Bloco 5 — Documentação
- `README.md` — atualiza tabela "What you need" + arquitetura.
- `SKILL.md` (root + `_system/SKILL.md`) — atualiza referências de modelos e fluxo.
- `SMOKE-TEST.md` — atualiza exemplo de teste com novos endpoints.

## Plano de execução

1. **Commit 1** — REFACTOR doc + `.env.example` (este).
2. **Commit 2** — Bloco 1 (check-deps + env). Verificar com `bash scripts/check-deps.sh` que mensagens novas saem corretas (sem chamar API).
3. **Commit 3** — Bloco 2 (first frame). Smoke test isolado: rodar gen 1 imagem com prompt simples e validar PNG saiu.
4. **Commit 4** — Bloco 3 (vídeo). Smoke test: rodar 1 clip Seedance 2.0 fast text-to-video 5s, validar mp4.
5. **Commit 5** — Bloco 4 (virality direct). Smoke test: passar 1 frame + script para Gemini 2.5 Pro e validar JSON score.
6. **Commit 6** — Bloco 5 (docs).
7. **Smoke test full pipeline** — Talking Head 15s sobre tema acessoverde (post-7 do plano editorial: "Defensoria Pública faz HC?").
8. Se passar: PR no fork (não no upstream) → merge para `main` do fork → deploy em peter tosh.

## Riscos e fallbacks

- **Gemini 3 Pro Image preview pode rate-limit.** Ter fallback para `gemini-2.5-flash-image` (cheaper, mais rápido) configurado via env override.
- **Seedance 2.0 standard tem áudio nativo + safety filter mais permissivo que Kling**, mas é mais caro. Default fast tier ($0.024/s) para B-roll que não precisa de qualidade hero.
- **Seedance 2.0 i2v rejeita first frames com rosto humano** (smoke test 2026-04-25): "partner_validation_failed: likeness of real people". Por isso A-roll skipa Seedance i2v e cai direto pra Kling 3 fal.ai.
- **Sora 2 está em deprecation gradual** (já notado no README upstream). Plano B atual: A-roll cai para Kling 3 fal.ai (com áudio, $0.084-0.168/s) — Seedance i2v não é viável para talking head.
- **GPT-5.5 não aceita vídeo nativo** — precisa extrair frames antes (3–5 frames evenly-spaced). Gemini 2.5/3 Pro aceita vídeo MP4 direto via Files API. Por isso default = Gemini para virality.
- **ElevenLabs key recuperada** de `~/.config/acessoverde/av-video-keys.env`.

## Graceful degradation chains (refator v2)

### A-roll (talking head com rosto + diálogo)

```
1. Sora 2 fal.ai i2v        $0.10/s    ← primary
2. Kling 3 fal.ai i2v       $0.084-0.168/s (com áudio)
3. Kling 3 Replicate        ~$0.10-0.15/s (emergency)
```

Seedance 2.0 i2v **propositadamente excluído** (rejeita rostos). `--provider seedance` ainda existe mas com warning.

### B-roll (cenas, produto, ambiente — sem rosto)

```
1. Seedance 2.0 fast fal.ai      $0.024/s          ← primary, melhor cost/quality
2. Kling 3 fal.ai pro            $0.112/s (s/audio) | $0.168/s (c/audio)
3. Kling 3 Replicate             ~$0.10-0.15/s (emergency)
```

Seedance 2.0 standard ($0.30/s) **propositadamente pulado** — Kling 3 fal pro
sem áudio ($0.112/s) é 2.7× mais barato com qualidade equivalente para B-roll.
B-roll não precisa de áudio nativo (vem da A-roll continuous voice).

**Custo de fallback por 15s de B-roll (3×5s):**
- Cadeia primária (Seedance fast OK): $0.36
- Fast falha → Kling fal pro s/audio: $1.68 (4.7× mais caro mas viável)
- Antes da otimização (Seedance standard como step 2): $4.50 (12.5× mais caro)

### First frame (image gen)

```
1. fal-ai/nano-banana-2          $0.08/img  ← primary
2. fal-ai/nano-banana            $0.039/img (cheaper, lower quality)
3. google/nano-banana-2 Replicate $0.067-0.151/img (resilience fallback)
```

### Virality scoring

```
1. gemini-2.5-flash direct       free tier  ← primary, JSON structured output
2. gemini-2.5-pro                billing required (skip se sem billing)
3. openai gpt-5.5                $0.005/1K input + frame extraction
```

## Compromisso de qualidade

Não merge para fork main sem:
1. `check-deps.sh` 100% green com FAL+GEMINI obrigatórias.
2. 1 first frame gerado (~$0.13).
3. 1 B-roll de 5s gerado (~$0.12).
4. 1 score retornando JSON 0–100 válido.
5. 1 vídeo Talking Head completo passando virality 70+.

Custo do smoke test full: ~$3–5 (similar ao orçamento do batch de 30 imagens do plano editorial social).
