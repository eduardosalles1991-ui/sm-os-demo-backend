# S&M OS 6.1 — Demo Backend (Render)

## O que é
MVP para testar chat guiado (intake) + relatório estruturado, sem login/pagamento.

## Endpoints
- GET /health
- POST /session/new (header: x-demo-key)
- POST /chat (header: x-demo-key)

## Variáveis de ambiente (Render)
- OPENAI_API_KEY (obrigatória)
- OPENAI_MODEL (opcional, default: gpt-4o-mini)
- ALLOWED_ORIGIN (obrigatória) ex.: https://correamendes.wpcomstaging.com
- DEMO_KEY (obrigatória) string aleatória
- TEMPERATURE (opcional) default 0.2

## Render — Start Command
uvicorn main:app --host 0.0.0.0 --port $PORT
