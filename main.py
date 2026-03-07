import os
import uuid
from typing import Dict, Any, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI


# =======================
# CONFIG
# =======================
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://correamendes.wpcomstaging.com")
DEMO_KEY = os.getenv("DEMO_KEY", "")  # REQUIRED
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# =======================
# APP (MUST exist for uvicorn main:app)
# =======================
app = FastAPI(title="S&M OS 6.1 — Demo Backend", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FIELDS_ORDER = [
    ("area_subarea", "Qual a área/subárea? (ex.: cível/consumidor/indenizatória)"),
    ("fase", "Qual a fase? (consultivo / pré-contencioso / processo / recurso / execução)"),
    ("objetivo_cliente", "Qual o objetivo do cliente? (o que ele quer obter)"),
    ("partes", "Quem são as partes? (autor/réu e relação entre eles)"),
    ("fatos_cronologia", "Conte os fatos em ordem (cronologia objetiva; datas aproximadas ok)."),
    ("provas_existentes", "Quais provas/documentos você já tem? (liste)"),
    ("urgencia_prazo", "Há urgência ou prazo crítico? (qual?)"),
    ("valor_envovido", "Qual o valor envolvido/impacto? (se não souber, estimativa)"),
]

REQUIRED_FIELDS = [k for k, _ in FIELDS_ORDER]

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


# =======================
# HELPERS
# =======================
def auth_or_401(x_demo_key: Optional[str]):
    if not DEMO_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: DEMO_KEY not set.")
    if not x_demo_key or x_demo_key != DEMO_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def next_missing(state: Dict[str, Any]) -> str:
    for key, question in FIELDS_ORDER:
        if not state.get(key):
            return question
    return ""


def is_sufficient(state: Dict[str, Any]) -> bool:
    return all(bool(state.get(k)) for k in REQUIRED_FIELDS)


def generate_report(state: Dict[str, Any]) -> str:
    if client is None:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY não configurada no Render (Environment).")

    user_case = f"""CASO (dados coletados):
- Área/Subárea: {state.get('area_subarea')}
- Fase: {state.get('fase')}
- Objetivo: {state.get('objetivo_cliente')}
- Partes: {state.get('partes')}
- Fatos (cronologia): {state.get('fatos_cronologia')}
- Provas existentes: {state.get('provas_existentes')}
- Urgência/Prazo: {state.get('urgencia_prazo')}
- Valor envolvido: {state.get('valor_envovido')}
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


# =======================
# MODELS
# =======================
class SessionOut(BaseModel):
    session_id: str
    message: str
    state: Dict[str, Any]


class ChatIn(BaseModel):
    session_id: str
    message: str
    state: Dict[str, Any] = {}


class ChatOut(BaseModel):
    message: str
    state: Dict[str, Any]
    report: Optional[str] = None


# =======================
# API
# =======================
@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "sm-os-demo",
        "version": "0.2.0",
        "has_openai_key": bool(OPENAI_API_KEY),
        "allowed_origin": ALLOWED_ORIGIN,
    }


@app.post("/session/new", response_model=SessionOut)
def session_new(x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = str(uuid.uuid4())
    state: Dict[str, Any] = {}
    return SessionOut(session_id=sid, message="Vamos iniciar o diagnóstico.\n\n" + FIELDS_ORDER[0][1], state=state)


@app.post("/chat", response_model=ChatOut)
def chat(inp: ChatIn, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)

    state = inp.state or {}

    # Guardar respuesta en el primer campo faltante
    for key, _question in FIELDS_ORDER:
        if not state.get(key):
            state[key] = (inp.message or "").strip()
            break

    if is_sufficient(state):
        report = generate_report(state)
        return ChatOut(message="✅ Dados suficientes. Gerando relatório estruturado…", state=state, report=report)

    return ChatOut(message=next_missing(state), state=state)


# =======================
# WIDGET (iframe)
# =======================
@app.get("/widget", response_class=HTMLResponse)
def widget():
    return HTMLResponse(
        """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>S&M OS 6.1 — Demo</title>
  <style>
    body{margin:0;font-family:system-ui,Segoe UI,Arial;background:#07080c;color:#eef1f7}
    .wrap{max-width:900px;margin:0 auto;padding:18px}
    .box{background:#0f111a;border:1px solid rgba(245,196,81,.25);border-radius:18px;overflow:hidden}
    .head{padding:16px 18px;background:linear-gradient(135deg,#0b0d12,#171b24);border-bottom:3px solid #f5c451}
    .title{font-weight:800;letter-spacing:.3px}
    .sub{opacity:.8;font-size:13px;margin-top:4px}
    #chatLog{height:420px;overflow:auto;padding:16px;background:#07080c}
    .row{display:flex;gap:10px;padding:14px;background:#0b0d12;border-top:1px solid rgba(255,255,255,.08)}
    input{flex:1;padding:12px;border-radius:12px;border:1px solid rgba(255,255,255,.12);background:#0f111a;color:#eef1f7;outline:none}
    button{padding:12px 14px;border-radius:12px;border:1px solid rgba(245,196,81,.35);
      background:linear-gradient(180deg,#f5c451,#c9921c);font-weight:900;cursor:pointer;color:#1a1204}
    .pill{display:flex;gap:10px;padding:14px;background:#0b0d12;border-top:1px solid rgba(255,255,255,.08);align-items:center}
    .badge{font-size:12px;color:rgba(245,196,81,.95);border:1px solid rgba(245,196,81,.25);padding:6px 10px;border-radius:999px;background:rgba(245,196,81,.06)}
    .btn2{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);color:#eef1f7;font-weight:800}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="box">
      <div class="head">
        <div class="title">S&M OS 6.1 — Diagnóstico Jurídico Inteligente (DEMO)</div>
        <div class="sub">Chat guiado • Relatório estruturado • Sem expor o módulo interno</div>
      </div>

      <div class="pill">
        <span class="badge">Ativação</span>
        <input id="keyInput" placeholder="Cole aqui o DEMO_KEY" />
        <button id="keyBtn">Ativar</button>
        <button id="resetBtn" class="btn2" style="margin-left:auto;">Reiniciar</button>
      </div>

      <div id="chatLog"></div>

      <div class="row">
        <input id="chatInput" placeholder="Digite aqui..." disabled />
        <button id="chatSend" disabled>Enviar</button>
      </div>
    </div>
  </div>

<script>
  const STORE_KEY="sm_os_demo_key";
  let DEMO_KEY=localStorage.getItem(STORE_KEY)||"";
  let sessionId=null;
  let state={};

  const log=document.getElementById("chatLog");
  const input=document.getElementById("chatInput");
  const btn=document.getElementById("chatSend");
  const keyInput=document.getElementById("keyInput");
  const keyBtn=document.getElementById("keyBtn");
  const resetBtn=document.getElementById("resetBtn");

  keyInput.value=DEMO_KEY;

  function addMsg(role,text){
    const wrap=document.createElement("div");
    wrap.style.marginBottom="12px";
    wrap.style.display="flex";
    wrap.style.justifyContent=role==="user"?"flex-end":"flex-start";

    const bubble=document.createElement("div");
    bubble.style.maxWidth="78%";
    bubble.style.padding="12px";
    bubble.style.borderRadius="14px";
    bubble.style.whiteSpace="pre-wrap";
    bubble.style.lineHeight="1.45";
    bubble.style.fontSize="14px";
    bubble.style.background=role==="user"?"rgba(245,196,81,.16)":"rgba(255,255,255,.06)";
    bubble.style.border=role==="user"?"1px solid rgba(245,196,81,.22)":"1px solid rgba(255,255,255,.10)";
    bubble.textContent=text;

    wrap.appendChild(bubble);
    log.appendChild(wrap);
    log.scrollTop=log.scrollHeight;
  }

  async function fetchJson(url, options){
    const res = await fetch(url, options);
    let data = {};
    try { data = await res.json(); } catch(e) {}
    if(!res.ok){
      const msg = data.detail || data.message || ("HTTP " + res.status);
      throw new Error(msg);
    }
    return data;
  }

  function setReady(ready){
    input.disabled = !ready;
    btn.disabled = !ready;
  }

  async function startSession(){
    if(!DEMO_KEY){
      addMsg("assistant","Cole o DEMO_KEY e clique em Ativar.");
      return;
    }
    setReady(false);
    addMsg("assistant","⏳ Iniciando sessão…");
    try{
      const data = await fetchJson("/session/new", { method:"POST", headers:{ "x-demo-key": DEMO_KEY }});
      sessionId = data.session_id;
      state = data.state || {};
      addMsg("assistant", data.message);
      setReady(true);
      input.focus();
    } catch(err){
      addMsg("assistant", "⚠️ Erro ao iniciar: " + err.message);
      setReady(false);
    }
  }

  async function send(){
    const text = input.value.trim();
    if(!text) return;
    input.value="";
    addMsg("user", text);
    setReady(false);
    addMsg("assistant", "⏳ Processando…");

    try{
      const payload = { session_id: sessionId || "local", message: text, state: state || {} };
      const data = await fetchJson("/chat", {
        method:"POST",
        headers:{ "Content-Type":"application/json", "x-demo-key": DEMO_KEY },
        body: JSON.stringify(payload)
      });
      state = data.state || state;
      addMsg("assistant", data.message || "(sem mensagem)");
      if(data.report){
        addMsg("assistant", "✅ RELATÓRIO GERADO:\\n\\n" + data.report);
      }
      setReady(true);
    } catch(err){
      addMsg("assistant", "⚠️ Falha: " + err.message + "\\nClique em Reiniciar e continue.");
      setReady(false);
    }
  }

  keyBtn.addEventListener("click", ()=>{
    DEMO_KEY = keyInput.value.trim();
    localStorage.setItem(STORE_KEY, DEMO_KEY);
    addMsg("assistant","Código registrado.");
    startSession();
  });

  resetBtn.addEventListener("click", ()=>{
    sessionId = null;
    state = {};
    addMsg("assistant","🔄 Reiniciando…");
    startSession();
  });

  btn.addEventListener("click", send);
  input.addEventListener("keydown", (e)=>{ if(e.key==="Enter") send(); });

  addMsg("assistant", DEMO_KEY ? "Código encontrado. Clique em Ativar." : "Cole o DEMO_KEY e clique em Ativar.");
</script>
</body>
</html>
"""
    )
