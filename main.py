import os
import uuid
from typing import Dict, Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://correamendes.wpcomstaging.com")
DEMO_KEY = os.getenv("DEMO_KEY", "")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = FastAPI(title="S&M OS 6.1 — Demo Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSIONS: Dict[str, Dict[str, Any]] = {}

REQUIRED_FIELDS = [
    "area_subarea",
    "fase",
    "objetivo_cliente",
    "fatos_cronologia",
    "provas_existentes",
    "urgencia_prazo",
    "partes",
    "valor_envovido",
]

QUESTION_ORDER = [
    ("area_subarea", "Qual a área/subárea? (ex.: cível/consumidor/indenizatória)"),
    ("fase", "Qual a fase? (consultivo / pré-contencioso / processo / recurso / execução)"),
    ("objetivo_cliente", "Qual o objetivo do cliente? (o que ele quer obter)"),
    ("partes", "Quem são as partes? (autor/réu e relação entre eles)"),
    ("fatos_cronologia", "Conte os fatos em ordem (cronologia objetiva; datas aproximadas ok)."),
    ("provas_existentes", "Quais provas/documentos você já tem? (liste)"),
    ("urgencia_prazo", "Há urgência ou prazo crítico? (qual?)"),
    ("valor_envovido", "Qual o valor envolvido/impacto? (se não souber, estimativa)"),
]

SYSTEM_OS = """Você é o S&M OS 6.1 (Diagnóstico Jurídico Inteligente).
Regras: Compliance OAB, LGPD, sigilo. Proibido inventar fatos/provas/jurisprudência.
Separar FATO/INF/HIP. Força da tese não é promessa de êxito.

Saída obrigatória:
1. CLASSIFICAÇÃO DO CASO
2. SÍNTESE
3. QUESTÃO JURÍDICA
4. ANÁLISE TÉCNICA
5. FORÇA DA TESE
6. CONFIABILIDADE DA ANÁLISE
7. PROVAS
8. RISCOS
9. CENÁRIOS
10. ANÁLISE ECONÔMICA (se houver base mínima)
11. RENTABILIDADE (se houver base mínima)
12. SCORES (0–100)
13. RED TEAM
14. ESTRATÉGIA
15. AÇÕES PRIORITÁRIAS
16. PENDÊNCIAS
17. ALERTAS
18. REFLEXÃO FINAL

Se dados insuficientes: rotular ANÁLISE PRELIMINAR e conclusões condicionais.
"""

def auth_or_401(x_demo_key: str | None):
    if not DEMO_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: DEMO_KEY not set.")
    if not x_demo_key or x_demo_key != DEMO_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

def next_missing(data: Dict[str, Any]) -> str:
    for key, question in QUESTION_ORDER:
        if not data.get(key):
            return question
    return ""

def is_sufficient(data: Dict[str, Any]) -> bool:
    return all(bool(data.get(k)) for k in REQUIRED_FIELDS)

def generate_report(data: Dict[str, Any]) -> str:
    user_case = f"""CASO (dados coletados):
- Área/Subárea: {data.get('area_subarea')}
- Fase: {data.get('fase')}
- Objetivo: {data.get('objetivo_cliente')}
- Partes: {data.get('partes')}
- Fatos (cronologia): {data.get('fatos_cronologia')}
- Provas existentes: {data.get('provas_existentes')}
- Urgência/Prazo: {data.get('urgencia_prazo')}
- Valor envolvido: {data.get('valor_envovido')}
"""
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_OS},
            {"role": "user", "content": user_case},
        ],
        temperature=TEMPERATURE,
    )
    return resp.choices[0].message.content

class ChatIn(BaseModel):
    session_id: str
    message: str

@app.get("/health")
def health():
    return {"ok": True, "service": "sm-os-demo", "version": "0.1.0"}

@app.post("/session/new")
def session_new(x_demo_key: str | None = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = str(uuid.uuid4())
    SESSIONS[sid] = {"data": {}}
    return {"session_id": sid, "message": "Vamos iniciar o diagnóstico.\n\n" + QUESTION_ORDER[0][1]}

@app.post("/chat")
def chat(inp: ChatIn, x_demo_key: str | None = Header(default=None)):
    auth_or_401(x_demo_key)
    s = SESSIONS.get(inp.session_id)
    if not s:
        return {"message": "Sessão inválida. Recarregue a página para iniciar novamente."}

    data = s["data"]

    # Save the user's answer into the first missing field
    for key, _question in QUESTION_ORDER:
        if not data.get(key):
            data[key] = inp.message.strip()
            break

    if is_sufficient(data):
        report = generate_report(data)
        return {"message": "✅ Dados suficientes. Gerando relatório estruturado…", "report": report}

    return {"message": next_missing(data)}
