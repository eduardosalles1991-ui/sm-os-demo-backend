import os
import uuid
from typing import Dict, Any, Optional

import openai
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI


# =======================
# CONFIG
# =======================
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://correamendes.wpcomstaging.com")
DEMO_KEY = os.getenv("DEMO_KEY", "")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()


# =======================
# APP
# =======================
app = FastAPI(title="S&M OS 6.1 — Demo Backend", version="0.4.0")

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


def get_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY não configurada no Render (Environment).")
    return OpenAI(api_key=OPENAI_API_KEY)


def generate_report(state: Dict[str, Any]) -> str:
    client = get_client()
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


def friendly_openai_error(e: Exception) -> HTTPException:
    if isinstance(e, openai.RateLimitError):
        return HTTPException(
            status_code=429,
            detail="Sem crédito/quota na API no momento (insufficient_quota). Verifique Billing/Créditos na OpenAI."
        )
    if isinstance(e, openai.AuthenticationError):
        return HTTPException(status_code=401, detail="OPENAI_API_KEY inválida ou sem permissão.")
    if isinstance(e, openai.NotFoundError):
        return HTTPException(status_code=404, detail=f"Modelo/endpoint não encontrado. Verifique OPENAI_MODEL: {MODEL}")
    if isinstance(e, openai.BadRequestError):
        return HTTPException(status_code=400, detail=f"BadRequest na OpenAI: {str(e)}")
    if isinstance(e, openai.APITimeoutError):
        return HTTPException(status_code=504, detail="Timeout na OpenAI. Tente novamente.")
    return HTTPException(status_code=500, detail=f"Erro ao gerar relatório: {type(e).__name__}: {str(e)}")


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
        "version": "0.4.0",
        "has_openai_key": bool(OPENAI_API_KEY),
        "allowed_origin": ALLOWED_ORIGIN,
        "model": MODEL,
    }


@app.post("/session/new", response_model=SessionOut)
def session_new(x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = str(uuid.uuid4())
    state: Dict[str, Any] = {}
    return SessionOut(
        session_id=sid,
        message="Vamos iniciar o diagnóstico.\n\n" + FIELDS_ORDER[0][1],
        state=state
    )


@app.post("/chat", response_model=ChatOut)
def chat(inp: ChatIn, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)

    state = inp.state or {}

    for key, _question in FIELDS_ORDER:
        if not state.get(key):
            state[key] = (inp.message or "").strip()
            break

    if is_sufficient(state):
        try:
            report = generate_report(state)
            return ChatOut(message="✅ Dados suficientes. Gerando relatório estruturado…", state=state, report=report)
        except HTTPException:
            raise
        except Exception as e:
            raise friendly_openai_error(e)

    return ChatOut(message=next_missing(state), state=state)


# =======================
# WIDGET HTML (TRANSPARENT EMBED MODE)
# =======================
WIDGET_HTML_TRANSPARENT = r"""
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>S&M OS 6.1 — Widget</title>
  <style>
    :root{
      --panel: rgba(15,17,26,.62);
      --panel2: rgba(11,13,18,.55);
      --text:#eef1f7;
      --muted:rgba(238,241,247,.72);
      --gold:#f5c451;
      --line:rgba(255,255,255,.12);
      --line2:rgba(245,196,81,.22);
      --radius:18px;
    }

    *{box-sizing:border-box}
    html, body { height:100%; }
    body{
      margin:0;
      background: transparent !important; /* ✅ deixa o vídeo do WP aparecer */
      color: var(--text);
      font-family: system-ui, -apple-system, Segoe UI, Inter, Arial;
    }

    /* Remove qualquer "moldura" interna: ocupa 100% do iframe */
    .shell{
      height:100%;
      display:flex;
      flex-direction:column;
      padding: 0;                 /* ✅ sem bordas internas pretas */
      background: transparent;    /* ✅ */
    }

    /* HEADER interno leve */
    .head{
      margin: 0 0 10px 0;
      padding: 12px 14px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      backdrop-filter: blur(10px);
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:12px;
    }
    .brand{display:flex; align-items:center; gap:10px; min-width:0}
    .logo{
      width:34px;height:34px;border-radius:12px;
      display:grid;place-items:center;
      font-weight:900;
      color: rgba(245,196,81,.95);
      background: rgba(245,196,81,.12);
      border: 1px solid var(--line2);
      flex:0 0 auto;
    }
    .twrap{min-width:0}
    .title{font-weight:900; font-size:14px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
    .sub{margin-top:3px; font-size:12px; color:var(--muted)}

    .pills{display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end}
    .pill{
      font-size:12px;
      padding:7px 10px;
      border-radius:999px;
      border:1px solid var(--line2);
      background: rgba(245,196,81,.10);
      color: rgba(245,196,81,.95);
      white-space:nowrap;
    }

    /* MAIN GRID */
    .grid{
      flex:1;
      display:grid;
      grid-template-columns: 1.2fr .8fr;
      gap: 10px;
      min-height: 0;
    }
    @media (max-width: 980px){
      .grid{ grid-template-columns: 1fr; }
      .side{ display:none; }
    }

    /* CHAT PANEL */
    .chat{
      display:flex;
      flex-direction:column;
      min-height:0;
      gap:10px;
    }

    .activation{
      display:flex; gap:10px; align-items:center;
      padding:12px 14px;
      border-radius: var(--radius);
      background: var(--panel2);
      border:1px solid var(--line);
      backdrop-filter: blur(10px);
    }
    .badge{
      font-size:12px; padding:6px 10px;
      border-radius:999px;
      border:1px solid var(--line2);
      background: rgba(245,196,81,.10);
      color: rgba(245,196,81,.95);
      white-space:nowrap;
    }
    .key{
      flex:1;
      padding:12px;
      border-radius:12px;
      border:1px solid rgba(255,255,255,.16);
      background: rgba(0,0,0,.25);
      color: var(--text);
      outline:none;
    }
    .btn{
      padding:12px 14px;
      border-radius:12px;
      border:1px solid rgba(245,196,81,.35);
      background: linear-gradient(180deg, rgba(245,196,81,.95), rgba(201,146,28,.95));
      font-weight:900;
      cursor:pointer;
      color:#1a1204;
    }
    .btn2{
      padding:12px 14px;
      border-radius:12px;
      border:1px solid rgba(255,255,255,.18);
      background: rgba(255,255,255,.06);
      color: var(--text);
      font-weight:900;
      cursor:pointer;
    }

    .progress{
      display:flex; align-items:center; gap:10px;
      padding:10px 14px;
      border-radius: var(--radius);
      background: var(--panel2);
      border:1px solid var(--line);
      backdrop-filter: blur(10px);
    }
    .bar{
      height:8px; border-radius:999px;
      background: rgba(255,255,255,.10);
      overflow:hidden;
      flex:1;
    }
    .bar > div{
      height:100%;
      width:0%;
      background: linear-gradient(90deg, rgba(245,196,81,.95), rgba(245,196,81,.25));
      transition: width .25s ease;
    }
    .step{font-size:12.5px; color:var(--muted); white-space:nowrap}

    #chatLog{
      flex:1;
      min-height:0;
      overflow:auto;
      padding:14px;
      border-radius: var(--radius);
      background: rgba(0,0,0,.18);   /* ✅ deixa vídeo aparecer */
      border:1px solid rgba(255,255,255,.10);
      backdrop-filter: blur(6px);
    }

    .msgWrap{margin-bottom:12px;display:flex}
    .msgWrap.user{justify-content:flex-end}
    .bubble{
      max-width:78%;
      padding:12px;
      border-radius:14px;
      white-space:pre-wrap;
      line-height:1.45;
      font-size:14px;
      box-shadow:none;            /* ✅ remove halo */
    }
    .bot .bubble{
      background: rgba(255,255,255,.08);
      border:1px solid rgba(255,255,255,.12);
    }
    .user .bubble{
      background: rgba(245,196,81,.16);
      border:1px solid rgba(245,196,81,.22);
    }

    .notice{
      margin:10px 0;
      padding:10px 12px;
      border-radius:14px;
      border:1px solid rgba(255,255,255,.12);
      background: rgba(255,255,255,.06);
      color: rgba(255,255,255,.86);
      font-size:13px;
    }
    .err{ border-color: rgba(255,112,112,.25); background: rgba(255,112,112,.10); color:#ffd6d6; }
    .ok{ border-color: rgba(122,255,170,.25); background: rgba(122,255,170,.10); color:#d8ffe8; }

    .row{
      display:flex; gap:10px;
      padding:12px 14px;
      border-radius: var(--radius);
      background: var(--panel2);
      border:1px solid var(--line);
      backdrop-filter: blur(10px);
    }
    .input{
      flex:1;
      padding:12px;
      border-radius:12px;
      border:1px solid rgba(255,255,255,.16);
      background: rgba(0,0,0,.25);
      color: var(--text);
      outline:none;
    }

    /* SIDE PANEL */
    .side{
      display:flex;
      flex-direction:column;
      gap:10px;
      min-height:0;
    }
    .card{
      border-radius: var(--radius);
      background: var(--panel);
      border:1px solid var(--line);
      backdrop-filter: blur(10px);
      padding:14px;
    }
    .card h3{
      margin:0 0 10px 0;
      font-size:13px;
      color: rgba(245,196,81,.95);
    }
    .kv{
      display:grid;
      grid-template-columns: 1fr;
      gap:8px;
      font-size:13px;
      color: rgba(255,255,255,.82);
    }
    .kv b{ color: rgba(255,255,255,.92); }

    .actions{display:flex; gap:10px; flex-wrap:wrap; margin-top:10px}
    .smallbtn{
      padding:10px 12px;
      border-radius:12px;
      border:1px solid rgba(255,255,255,.18);
      background: rgba(255,255,255,.06);
      color: var(--text);
      font-weight:900;
      cursor:pointer;
      font-size:12.5px;
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="head">
      <div class="brand">
        <div class="logo">S&M</div>
        <div class="twrap">
          <div class="title">Diagnóstico Jurídico Inteligente</div>
          <div class="sub">S&M OS 6.1 • Chat guiado • Relatório estruturado</div>
        </div>
      </div>
      <div class="pills">
        <span class="pill">DEMO • Sem expor módulo interno</span>
        <span class="pill" id="statusPill">Status: pronto</span>
      </div>
    </div>

    <div class="grid">
      <div class="chat">
        <div class="activation">
          <span class="badge">Ativação</span>
          <input class="key" id="keyInput" placeholder="Cole aqui o DEMO_KEY" />
          <button class="btn" id="keyBtn">Ativar</button>
          <button class="btn2" id="resetBtn">Reiniciar</button>
        </div>

        <div class="progress">
          <div class="bar"><div id="barFill"></div></div>
          <div class="step" id="stepText">Etapa 0/8</div>
        </div>

        <div id="chatLog"></div>

        <div class="row">
          <input class="input" id="chatInput" placeholder="Digite aqui..." disabled />
          <button class="btn" id="chatSend" disabled>Enviar</button>
        </div>
      </div>

      <div class="side">
        <div class="card">
          <h3>Dados capturados</h3>
          <div class="kv" id="kv"></div>
          <div class="actions">
            <button class="smallbtn" id="copyStateBtn">Copiar dados</button>
            <button class="smallbtn" id="copyReportBtn">Copiar relatório</button>
          </div>
        </div>

        <div class="card">
          <h3>Como usar (1 minuto)</h3>
          <div class="kv">
            <div>1) Ative com a DEMO_KEY</div>
            <div>2) Responda 8 perguntas rápidas</div>
            <div>3) Receba o relatório estruturado</div>
          </div>
        </div>

        <div class="card">
          <h3>Nota de compliance</h3>
          <div class="kv">
            <div>Assistivo. Revisão humana obrigatória em decisões críticas.</div>
            <div>Sem promessas. Sem fabricação de prova.</div>
          </div>
        </div>
      </div>
    </div>
  </div>

<script>
  const STORE_KEY="sm_os_demo_key";
  const fieldLabels = {
    area_subarea: "Área/Subárea",
    fase: "Fase",
    objetivo_cliente: "Objetivo",
    partes: "Partes",
    fatos_cronologia: "Fatos (cronologia)",
    provas_existentes: "Provas existentes",
    urgencia_prazo: "Urgência/Prazo",
    valor_envovido: "Valor/Impacto",
  };
  const fieldOrder = Object.keys(fieldLabels);

  let DEMO_KEY = localStorage.getItem(STORE_KEY) || "";
  let sessionId = null;
  let state = {};
  let lastReport = "";

  const log = document.getElementById("chatLog");
  const input = document.getElementById("chatInput");
  const btn = document.getElementById("chatSend");
  const keyInput = document.getElementById("keyInput");
  const keyBtn = document.getElementById("keyBtn");
  const resetBtn = document.getElementById("resetBtn");
  const statusPill = document.getElementById("statusPill");
  const barFill = document.getElementById("barFill");
  const stepText = document.getElementById("stepText");
  const kv = document.getElementById("kv");
  const copyStateBtn = document.getElementById("copyStateBtn");
  const copyReportBtn = document.getElementById("copyReportBtn");

  keyInput.value = DEMO_KEY;

  function setStatus(text){ statusPill.textContent = "Status: " + text; }

  function progress(){
    let filled = 0;
    for(const k of fieldOrder){ if(state && state[k]) filled++; }
    const pct = Math.round((filled / fieldOrder.length) * 100);
    barFill.style.width = pct + "%";
    stepText.textContent = "Etapa " + filled + "/" + fieldOrder.length;
  }

  function escapeHtml(s){
    return s.replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
  }

  function renderKV(){
    kv.innerHTML = "";
    for(const k of fieldOrder){
      const v = (state && state[k]) ? state[k] : "—";
      const div = document.createElement("div");
      div.innerHTML = "<b>" + fieldLabels[k] + ":</b><br/>" + escapeHtml(String(v)).slice(0, 280);
      kv.appendChild(div);
    }
    progress();
  }

  function addMsg(role, text){
    const wrap = document.createElement("div");
    wrap.className = "msgWrap " + (role === "user" ? "user" : "bot");

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;

    wrap.appendChild(bubble);
    log.appendChild(wrap);
    log.scrollTop = log.scrollHeight;
  }

  function addNotice(text, type=""){
    const div = document.createElement("div");
    div.className = "notice " + type;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
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
      addNotice("Cole o DEMO_KEY e clique em Ativar.", "err");
      return;
    }
    setReady(false);
    setStatus("iniciando");
    addNotice("⏳ Iniciando sessão…");
    try{
      const data = await fetchJson("/session/new", { method:"POST", headers:{ "x-demo-key": DEMO_KEY }});
      sessionId = data.session_id;
      state = data.state || {};
      lastReport = "";
      renderKV();
      addMsg("bot", data.message);
      setReady(true);
      setStatus("ativo");
      input.focus();
    }catch(err){
      addNotice("⚠️ Erro ao iniciar: " + err.message, "err");
      setStatus("erro");
      setReady(false);
    }
  }

  async function send(){
    const text = input.value.trim();
    if(!text) return;
    input.value="";
    addMsg("user", text);
    setReady(false);
    setStatus("processando");
    addNotice("⏳ Processando…");

    try{
      const payload = { session_id: sessionId || "local", message: text, state: state || {} };
      const data = await fetchJson("/chat", {
        method:"POST",
        headers:{ "Content-Type":"application/json", "x-demo-key": DEMO_KEY },
        body: JSON.stringify(payload)
      });

      state = data.state || state;
      renderKV();

      addMsg("bot", data.message || "(sem mensagem)");
      if(data.report){
        lastReport = data.report;
        addNotice("✅ Relatório gerado. Você pode copiar no painel.", "ok");
        addMsg("bot", data.report);
      }
      setReady(true);
      setStatus("ativo");
    }catch(err){
      addNotice("⚠️ Falha: " + err.message + " • Clique em Reiniciar se necessário.", "err");
      setStatus("erro");
      setReady(false);
    }
  }

  keyBtn.addEventListener("click", ()=>{
    DEMO_KEY = keyInput.value.trim();
    localStorage.setItem(STORE_KEY, DEMO_KEY);
    addNotice("Código registrado.");
    startSession();
  });

  resetBtn.addEventListener("click", ()=>{
    sessionId = null;
    state = {};
    lastReport = "";
    renderKV();
    addNotice("🔄 Reiniciando…");
    startSession();
  });

  btn.addEventListener("click", send);
  input.addEventListener("keydown", (e)=>{ if(e.key==="Enter") send(); });

  copyStateBtn.addEventListener("click", async ()=>{
    const txt = JSON.stringify(state || {}, null, 2);
    await navigator.clipboard.writeText(txt);
    addNotice("✅ Dados copiados.", "ok");
  });

  copyReportBtn.addEventListener("click", async ()=>{
    if(!lastReport){
      addNotice("Ainda não há relatório para copiar.", "err");
      return;
    }
    await navigator.clipboard.writeText(lastReport);
    addNotice("✅ Relatório copiado.", "ok");
  });

  renderKV();
  addNotice(DEMO_KEY ? "Código encontrado. Clique em Ativar." : "Cole o DEMO_KEY e clique em Ativar.");
  setStatus("pronto");
</script>
</body>
</html>
"""


@app.get("/widget", response_class=HTMLResponse)
def widget(transparent: int = Query(default=0)):
    # transparent=1 -> fundo transparente para aparecer o vídeo do WP
    if transparent == 1:
        return HTMLResponse(WIDGET_HTML_TRANSPARENT)
    # fallback: se quiser, pode manter o modo antigo (não incluído)
    return HTMLResponse(WIDGET_HTML_TRANSPARENT)
