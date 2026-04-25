# ScrollClaw Refactor — abr/2026

Fork: `tiagotalbuquerque/scrollclaw` (origin) ← upstream `TheMattBerman/scrollclaw`. Branch: `refactor/2026-04-direct-apis`. Motivação: o projeto upstream foi desenhado em dez/2025 — desde então saíram modelos novos e APIs diretas que evitam intermediários (Replicate, OpenRouter).

## Objetivos

1. ~~**Eliminar Replicate** — first frame sai de `google/nano-banana-2` (proxy) para Gemini direct (`gemini-3-pro-image-preview`).~~ **REVERTIDO 2026-04-25:** Gemini API exige billing no Google Cloud para modelos de imagem (free tier = 0). Operador optou por manter Replicate como proxy para preservar free tier dos outros serviços Google. Modelo `google/nano-banana-2` no Replicate é o mesmo Nano Banana 2 do Google, só com gateway diferente — qualidade idêntica, custo ~27× menor que API direta ($0.005/img vs $0.134).
2. **Eliminar OpenRouter** — virality scoring vai direto para Gemini API (default, modelos texto rodam free tier) ou OpenAI (alt).
3. **Substituir Kling 3 → Seedance 2.0** para B-roll (mantém fal.ai como gateway, troca de model slug).
4. **Adicionar ElevenLabs** — chave recuperada de `~/.config/acessoverde/av-video-keys.env` ✅.

## Mapa de substituições

| Etapa | Hoje (upstream) | Refator (este fork) | Mudança de custo |
|---|---|---|---|
| First frame | Replicate `google/nano-banana-2` (~$0.005/img) | **Mantém** Replicate `google/nano-banana-2` | Sem mudança — Gemini direto exige billing no GCP, operador prefere preservar free tier de outros serviços |
| A-roll Talking Head | Sora 2 via fal.ai (~$0.10–0.15/s) | **Mantém** Sora 2 via fal.ai | Sem mudança — Sora native voice + lip sync ainda é melhor que TTS + V2V |
| A-roll fallback | Kling 3 via fal.ai | Seedance 2.0 standard via fal.ai (~$0.30/s) ou Kling 3 (~$0.10/s) | Configurável; default Seedance pela qualidade + áudio nativo bundled |
| B-roll | Kling 3 via fal.ai (`fal-ai/kling-video/v3/pro`) | Seedance 2.0 fast via fal.ai (`bytedance/seedance-2.0/fast/text-to-video` ~$0.024/s ou `image-to-video` ~$0.15/s) | **Mais barato** no tier fast; 480/720/1080p, áudio nativo grátis |
| Virality score | OpenRouter → Gemini | Gemini direct (`gemini-3-pro` ou `gemini-2.5-pro`) | Default Gemini 2.5 Pro (mais barato), config flag para GPT-5.5 |
| Voice consistency | ElevenLabs S2S (já era opcional) | ElevenLabs S2S (chave da prod) | Sem mudança no provedor |

## Arquivos a tocar (10)

### Bloco 1 — Configuração
- `scripts/check-deps.sh` — adiciona `GEMINI_API_KEY` obrigatório (virality scoring); mantém `REPLICATE_API_TOKEN` obrigatório (first frame); mantém `FAL_KEY` obrigatório; opcional `OPENAI_API_KEY` e `ELEVENLABS_API_KEY` (chave já em `~/.config/acessoverde/av-video-keys.env`).
- `.env.example` — criar com novo conjunto de chaves.

### Bloco 2 — First frame (mantém Replicate)
- ~~Reescrita para Gemini direct~~ **Revertido 2026-04-25** — billing no GCP exigido. Replicate fica como gateway. Sem alteração no código. Commit fc53966 revertido (arquivo restaurado de commit anterior).

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
- **Sora 2 está em deprecation gradual** (já notado no README upstream). Plano B: forçar `--provider seedance` para A-roll também, com voice via ElevenLabs S2S.
- **GPT-5.5 não aceita vídeo nativo** — precisa extrair frames antes (3–5 frames evenly-spaced). Gemini 2.5/3 Pro aceita vídeo MP4 direto via Files API. Por isso default = Gemini para virality.
- **ElevenLabs key não localizada em prod** — se não vier do operador, pipeline ainda funciona em modo single-clip (Sora native voice).

## Compromisso de qualidade

Não merge para fork main sem:
1. `check-deps.sh` 100% green com FAL+GEMINI obrigatórias.
2. 1 first frame gerado (~$0.13).
3. 1 B-roll de 5s gerado (~$0.12).
4. 1 score retornando JSON 0–100 válido.
5. 1 vídeo Talking Head completo passando virality 70+.

Custo do smoke test full: ~$3–5 (similar ao orçamento do batch de 30 imagens do plano editorial social).
