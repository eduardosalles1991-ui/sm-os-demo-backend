import os
import re
import io
import uuid
import json
import base64
from typing import Any, Dict, List, Optional
import requests
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    from docx import Document as DocxDocument
except Exception:
    DocxDocument = None

# -----------------------------
# Environment
# -----------------------------
OPENAI_API_KEY        = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_MODEL          = (os.getenv("OPENAI_MODEL") or "gpt-4o").strip()
DEMO_KEY              = (os.getenv("DEMO_KEY") or "").strip()
ALLOWED_ORIGIN        = (os.getenv("ALLOWED_ORIGIN") or "*").strip()

# Prompt jurídico OS 6.1 — lido da env
OS_6_1_PROMPT         = (os.getenv("OS_6_1_PROMPT") or "").strip()

DATAJUD_ENABLED       = os.getenv("DATAJUD_ENABLED", "false").lower() == "true"
DATAJUD_BASE_URL      = (os.getenv("DATAJUD_BASE_URL") or "https://api-publica.datajud.cnj.jus.br").strip().rstrip("/")
DATAJUD_API_KEY       = (os.getenv("DATAJUD_API_KEY") or "").strip()
DATAJUD_TIMEOUT_S     = int(os.getenv("DATAJUD_TIMEOUT_S", "25"))
DATAJUD_DEFAULT_ALIAS = (os.getenv("DATAJUD_DEFAULT_ALIAS") or "").strip()
DATAJUD_SORT_FIELD    = (os.getenv("DATAJUD_SORT_FIELD") or "dataHoraUltimaAtualizacao").strip()
MANDATARIA_NOME       = (os.getenv("MANDATARIA_NOME") or "").strip()
MANDATARIA_OAB        = (os.getenv("MANDATARIA_OAB") or "").strip()

# -----------------------------
# System prompt builder
# -----------------------------
def build_system_prompt(extra_context: str = "") -> str:
    """
    Monta o system prompt final combinando:
    1. Prompt OS 6.1 da env (núcleo jurídico)
    2. Contexto adicional (dados de processo, arquivos, etc.)
    """
    base = OS_6_1_PROMPT if OS_6_1_PROMPT else (
        "Você é um assistente jurídico especializado em Direito do Trabalho brasileiro. "
        "Responda sempre em português, de forma técnica, clara e objetiva. "
        "Nunca prometa resultados. Separe sempre fato de hipótese. "
        "Cite os fundamentos legais (CLT, súmulas TST/TRT) quando relevante. "
        "Sinalize quando uma análise exigir validação humana por advogado responsável."
    )

    mandate_info = ""
    if MANDATARIA_NOME or MANDATARIA_OAB:
        mandate_info = (
            f"\n\nEste sistema opera para o escritório/mandatária: "
            f"{MANDATARIA_NOME} — {MANDATARIA_OAB}. "
            "Todas as análises são assistivas e sujeitas à revisão do advogado responsável."
        )

    context_block = ""
    if extra_context:
        context_block = f"\n\n===CONTEXTO ADICIONAL===\n{extra_context}\n===FIM DO CONTEXTO==="

    return base + mandate_info + context_block

# -----------------------------
# FastAPI app
# -----------------------------
app = FastAPI(title="SM OS Chat", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if ALLOWED_ORIGIN == "*" else [ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSIONS: Dict[str, Dict[str, Any]] = {}

# -----------------------------
# Models
# -----------------------------
class SessionOut(BaseModel):
    session_id: str
    state: Dict[str, Any] = {}

class ChatIn(BaseModel):
    session_id: str
    message: str
    state: Optional[Dict[str, Any]] = None

class ChatOut(BaseModel):
    message: str
    state: Dict[str, Any] = {}

class DataJudSearchRequest(BaseModel):
    alias: Optional[str] = None
    numero_processo: Optional[str] = None
    classe_nome: Optional[str] = None
    assunto_nome: Optional[str] = None
    tribunal: Optional[str] = None
    orgao_julgador: Optional[str] = None
    size: int = 10
    cursor: Optional[str] = None

# -----------------------------
# Helpers
# -----------------------------
def auth_or_401(x_demo_key: Optional[str]) -> None:
    if DEMO_KEY and x_demo_key != DEMO_KEY:
        raise HTTPException(status_code=401, detail="Não autorizado")

def get_session_state(session_id: str) -> Dict[str, Any]:
    return SESSIONS.setdefault(
        session_id,
        {
            "messages": [],
            "uploaded_contexts": [],
            "last_datajud_search": None,
        },
    )

def normalize_process_number(numero: str) -> str:
    return re.sub(r"\D", "", numero or "")

PROCESS_NUMBER_RE = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")

def detect_process_numbers(text: str) -> List[str]:
    if not text:
        return []
    found = PROCESS_NUMBER_RE.findall(text)
    out, seen = [], set()
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out

def infer_datajud_alias_from_number(numero: str) -> Optional[str]:
    m = re.match(r"(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})", numero or "")
    if not m:
        return None
    justice  = m.group(4)
    tribunal = m.group(5)
    try:
        tr_num = int(tribunal)
    except Exception:
        return None
    if justice == "5":
        return f"api_publica_trt{tr_num}"
    if justice == "4":
        return f"api_publica_trf{tr_num}"
    return None

def looks_like_process_summary_request(message: str) -> bool:
    msg = (message or "").lower()
    if detect_process_numbers(message):
        return True
    keywords = [
        "resumo do processo", "resumir processo", "andamento",
        "movimenta", "status do processo", "situação do processo",
        "resumo desse processo", "o que aconteceu nesse processo",
        "últimas movimentações", "ultima movimentacao",
    ]
    return any(k in msg for k in keywords)

def looks_like_natural_search_request(message: str) -> bool:
    msg = (message or "").lower()
    search_words = ["busque", "busca", "procure", "pesquise", "pesquisa",
                    "me mostre processos", "encontre processos"]
    filter_words = ["classe", "assunto", "tribunal", "órgão", "orgao"]
    return any(w in msg for w in search_words) and any(f in msg for f in filter_words)

def encode_search_after_token(values: Optional[List[Any]]) -> Optional[str]:
    if not values:
        return None
    raw = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")

def decode_search_after_token(token: Optional[str]) -> Optional[List[Any]]:
    if not token:
        return None
    try:
        raw    = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        values = json.loads(raw)
        return values if isinstance(values, list) else None
    except Exception:
        return None

def extract_text_from_upload(file: UploadFile, data: bytes) -> str:
    filename = (file.filename or "").lower()
    if filename.endswith(".txt") or filename.endswith(".md"):
        try:
            return data.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    if filename.endswith(".pdf") and PdfReader is not None:
        try:
            reader = PdfReader(io.BytesIO(data))
            texts  = []
            for page in reader.pages[:40]:
                try:
                    texts.append(page.extract_text() or "")
                except Exception:
                    pass
            return "\n".join(texts).strip()
        except Exception:
            return ""
    if filename.endswith(".docx") and DocxDocument is not None:
        try:
            doc = DocxDocument(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs if p.text).strip()
        except Exception:
            return ""
    return ""

def compact_text(text: str, limit: int = 6000) -> str:
    text = (text or "").strip()
    return text[:limit] if len(text) > limit else text

# -----------------------------
# DataJud
# -----------------------------
class DataJudError(Exception):
    pass

class DataJudService:
    def __init__(self):
        self.enabled       = DATAJUD_ENABLED
        self.base_url      = DATAJUD_BASE_URL
        self.api_key       = DATAJUD_API_KEY
        self.timeout_s     = DATAJUD_TIMEOUT_S
        self.default_alias = DATAJUD_DEFAULT_ALIAS
        self.default_sort_field = DATAJUD_SORT_FIELD

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"APIKey {self.api_key}"
        return headers

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            raise DataJudError("DataJud desabilitado.")
        if not self.base_url:
            raise DataJudError("DATAJUD_BASE_URL não configurado.")
        if not self.api_key:
            raise DataJudError("DATAJUD_API_KEY não configurado.")
        url = f"{self.base_url}{path}"
        try:
            resp = requests.post(
                url, headers=self._headers(), json=payload, timeout=self.timeout_s
            )
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            body = e.response.text if e.response is not None else ""
            raise DataJudError(f"HTTP {getattr(e.response, 'status_code', '?')}: {body[:1200]}")
        except requests.RequestException as e:
            raise DataJudError(f"Falha de conexão com DataJud: {str(e)}")
        except ValueError as e:
            raise DataJudError(f"Resposta JSON inválida: {str(e)}")

    def _build_base_payload(self, query, size=10, sort=None, search_after=None, source_fields=None):
        payload: Dict[str, Any] = {
            "size":  size,
            "query": query,
            "sort":  sort or [{self.default_sort_field: "desc"}],
        }
        if search_after:
            payload["search_after"] = search_after
        if source_fields:
            payload["_source"] = source_fields
        return payload

    def search_raw(self, alias, query, size=10, sort=None, search_after=None, source_fields=None):
        tribunal_alias = (alias or self.default_alias or "").strip()
        if not tribunal_alias:
            raise DataJudError("Alias do tribunal não informado.")
        payload = self._build_base_payload(query=query, size=size, sort=sort,
                                           search_after=search_after, source_fields=source_fields)
        return self._post(f"/{tribunal_alias}/_search", payload)

    def search_process_by_number(self, numero: str, alias: Optional[str] = None, size: int = 1):
        tribunal_alias = (alias or self.default_alias or "").strip()
        if not tribunal_alias:
            inferred = infer_datajud_alias_from_number(numero)
            tribunal_alias = inferred or ""
        if not tribunal_alias:
            raise DataJudError("Alias do tribunal não informado nem inferido do número CNJ.")
        numero_limpo = normalize_process_number(numero)
        if not numero_limpo:
            raise DataJudError("Número do processo inválido.")
        query = {"match": {"numeroProcesso": numero_limpo}}
        return self.search_raw(alias=tribunal_alias, query=query, size=size)

    def search_paginated(self, alias, query, size=10, cursor=None, sort=None, source_fields=None):
        search_after = decode_search_after_token(cursor)
        raw = self.search_raw(alias=alias, query=query, size=size, sort=sort,
                              search_after=search_after, source_fields=source_fields)
        hits_block  = ((raw or {}).get("hits") or {})
        hits        = hits_block.get("hits") or []
        total       = hits_block.get("total") or {}
        total_value = total.get("value") if isinstance(total, dict) else total
        items: List[Dict[str, Any]] = []
        next_cursor: Optional[str]  = None
        for hit in hits:
            source = hit.get("_source") or {}
            items.append({"_id": hit.get("_id"), "_score": hit.get("_score"),
                          "sort": hit.get("sort") or [], "source": source})
        if hits:
            last_sort   = hits[-1].get("sort") or []
            next_cursor = encode_search_after_token(last_sort) if last_sort else None
        return {"total": total_value, "count": len(items), "items": items,
                "next_cursor": next_cursor, "raw_took_ms": raw.get("took"),
                "timed_out": raw.get("timed_out", False)}

    def extract_sources(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        hits = (((raw or {}).get("hits") or {}).get("hits") or [])
        return [item.get("_source") or {} for item in hits if item.get("_source")]

    def normalize_process(self, source: Dict[str, Any]) -> Dict[str, Any]:
        movimentos        = source.get("movimentos") or []
        movimentos_sorted = sorted(movimentos, key=lambda x: x.get("dataHora") or "", reverse=True)
        ultima_mov        = movimentos_sorted[0] if movimentos_sorted else None
        assuntos          = source.get("assuntos") or []
        classe            = source.get("classe") or {}
        orgao             = source.get("orgaoJulgador") or {}
        sistema           = source.get("sistema") or {}
        formato           = source.get("formato") or {}
        return {
            "numero_processo":           source.get("numeroProcesso"),
            "tribunal":                  source.get("tribunal"),
            "grau":                      source.get("grau"),
            "data_ajuizamento":          source.get("dataAjuizamento"),
            "ultima_atualizacao":        source.get("dataHoraUltimaAtualizacao"),
            "classe_nome":               classe.get("nome"),
            "classe_codigo":             classe.get("codigo"),
            "orgao_julgador":            orgao.get("nome"),
            "sistema":                   sistema.get("nome"),
            "formato":                   formato.get("nome"),
            "assuntos":                  [a.get("nome") for a in assuntos if a.get("nome")],
            "movimentos_total":          len(movimentos),
            "ultima_movimentacao_nome":  (ultima_mov or {}).get("nome"),
            "ultima_movimentacao_data":  (ultima_mov or {}).get("dataHora"),
            "movimentos":                movimentos_sorted[:15],
            "raw":                       source,
        }

DATAJUD = DataJudService()

def build_datajud_query(req: DataJudSearchRequest) -> dict:
    must = []
    if req.numero_processo:
        numero_limpo = "".join(ch for ch in req.numero_processo if ch.isdigit())
        if numero_limpo:
            must.append({"match": {"numeroProcesso": numero_limpo}})
    if req.classe_nome:
        must.append({"match": {"classe.nome": req.classe_nome}})
    if req.assunto_nome:
        must.append({"match": {"assuntos.nome": req.assunto_nome}})
    if req.tribunal:
        must.append({"match": {"tribunal": req.tribunal}})
    if req.orgao_julgador:
        must.append({"match": {"orgaoJulgador.nome": req.orgao_julgador}})
    if not must:
        return {"match_all": {}}
    return {"bool": {"must": must}}

def build_process_context_for_gpt(proc: Dict[str, Any]) -> str:
    """
    Monta bloco de contexto estruturado do processo para injetar no prompt do GPT.
    O OS 6.1 vai analisar esses dados com toda a sua inteligência jurídica.
    """
    assuntos = ", ".join(proc.get("assuntos") or []) or "não identificados"
    movimentos_txt = []
    for mov in (proc.get("movimentos") or [])[:10]:
        nome = mov.get("nome", "Movimentação")
        data = mov.get("dataHora", "")
        movimentos_txt.append(f"  - {data} | {nome}")

    lines = [
        f"DADOS DO PROCESSO (fonte: DataJud/CNJ — {proc.get('tribunal')}):",
        f"Número: {proc.get('numero_processo')}",
        f"Tribunal: {proc.get('tribunal')} | Grau: {proc.get('grau')}",
        f"Classe: {proc.get('classe_nome')} | Órgão Julgador: {proc.get('orgao_julgador')}",
        f"Assuntos: {assuntos}",
        f"Data de ajuizamento: {proc.get('data_ajuizamento') or 'não disponível'}",
        f"Última atualização: {proc.get('ultima_atualizacao') or 'não disponível'}",
        f"Total de movimentações: {proc.get('movimentos_total')}",
        f"Última movimentação: {proc.get('ultima_movimentacao_nome') or 'não disponível'} "
        f"({proc.get('ultima_movimentacao_data') or ''})",
        "",
        "Histórico recente de movimentações (últimas 10):",
    ] + movimentos_txt

    return "\n".join(lines)

# -----------------------------
# OpenAI HTTP helper
# -----------------------------
def call_openai_chat(messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY não configurado.")
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model":       OPENAI_MODEL,
            "messages":    messages,
            "temperature": temperature,
        },
        timeout=90,
    )
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        body = e.response.text if e.response is not None else ""
        raise RuntimeError(f"Erro OpenAI: {body[:1000]}")
    data = resp.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip() \
           or "Não consegui responder agora."

# -----------------------------
# Routes
# -----------------------------
@app.get("/ping")
def ping():
    return {"ok": True, "service": "alive"}

@app.get("/health")
def health():
    return {
        "ok":    True,
        "model": OPENAI_MODEL,
        "os_61_prompt_loaded": bool(OS_6_1_PROMPT),
        "datajud": {
            "enabled":       DATAJUD_ENABLED,
            "base_url":      DATAJUD_BASE_URL,
            "default_alias": DATAJUD_DEFAULT_ALIAS,
        },
    }

@app.post("/session/new", response_model=SessionOut)
def session_new(x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = str(uuid.uuid4())
    SESSIONS[sid] = {"messages": [], "uploaded_contexts": [], "last_datajud_search": None}
    return SessionOut(session_id=sid, state={})

@app.post("/upload")
async def upload(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    x_demo_key: Optional[str] = Header(default=None),
):
    auth_or_401(x_demo_key)
    session = get_session_state(session_id)
    data    = await file.read()
    text    = extract_text_from_upload(file, data)
    text    = compact_text(text, 12000)
    session["uploaded_contexts"].append({"filename": file.filename,
                                         "content_type": file.content_type, "text": text})
    if text:
        return {"ok": True,
                "message": f"Arquivo '{file.filename}' anexado. Vou considerar o conteúdo na análise.",
                "filename": file.filename, "text_preview": text[:800]}
    return {"ok": True, "message": f"Arquivo '{file.filename}' anexado.", "filename": file.filename}

@app.get("/datajud/test-process")
def datajud_test_process(numero: str, alias: Optional[str] = None):
    try:
        raw   = DATAJUD.search_process_by_number(numero=numero, alias=alias)
        items = DATAJUD.extract_sources(raw)
        if not items:
            return {"ok": True, "found": False, "message": "Nenhum processo encontrado."}
        proc = DATAJUD.normalize_process(items[0])
        return {"ok": True, "found": True, "process": proc}
    except DataJudError as e:
        return {"ok": False, "error": str(e)}

@app.post("/datajud/search")
def datajud_search(req: DataJudSearchRequest, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    try:
        query  = build_datajud_query(req)
        result = DATAJUD.search_paginated(
            alias=req.alias, query=query,
            size=max(1, min(req.size, 50)),
            cursor=req.cursor,
            sort=[{DATAJUD_SORT_FIELD: "desc"}],
            source_fields=["numeroProcesso", "tribunal", "grau", "dataAjuizamento",
                           "dataHoraUltimaAtualizacao", "classe", "assuntos",
                           "orgaoJulgador", "movimentos", "sistema", "formato"],
        )
        items = []
        for item in result["items"]:
            source = item.get("source") or {}
            proc   = DATAJUD.normalize_process(source)
            items.append({
                "numero_processo":          proc["numero_processo"],
                "tribunal":                 proc["tribunal"],
                "grau":                     proc["grau"],
                "classe_nome":              proc["classe_nome"],
                "orgao_julgador":           proc["orgao_julgador"],
                "assuntos":                 proc["assuntos"],
                "data_ajuizamento":         proc["data_ajuizamento"],
                "ultima_atualizacao":       proc["ultima_atualizacao"],
                "ultima_movimentacao_nome": proc["ultima_movimentacao_nome"],
                "ultima_movimentacao_data": proc["ultima_movimentacao_data"],
                "movimentos_total":         proc["movimentos_total"],
            })
        return {"ok": True, "total": result["total"], "count": result["count"],
                "items": items, "next_cursor": result["next_cursor"],
                "timed_out": result["timed_out"], "raw_took_ms": result["raw_took_ms"]}
    except DataJudError as e:
        return {"ok": False, "error": str(e)}

@app.post("/chat", response_model=ChatOut)
def chat_endpoint(payload: ChatIn, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    session = get_session_state(payload.session_id)
    message = (payload.message or "").strip()
    state   = payload.state or {}

    session["messages"].append({"role": "user", "content": message})
    session["messages"] = session["messages"][-20:]

    # ------------------------------------------------------------------
    # 1) Busca natural por filtros no DataJud
    # ------------------------------------------------------------------
    if looks_like_natural_search_request(message):
        natural_req = _parse_natural_search_filters(message)
        if natural_req:
            try:
                result = datajud_search(natural_req, x_demo_key=x_demo_key)
                session["last_datajud_search"] = {"request": natural_req.dict(), "response": result}
                count = result.get("count", 0)
                total = result.get("total")
                items = result.get("items") or []
                if not items:
                    reply = "Não encontrei processos com esses filtros no DataJud."
                else:
                    lines = [
                        f"Encontrei **{count} resultado(s)**" +
                        (f" de um total de {total}" if total is not None else "") + ":\n"
                    ]
                    for item in items[:5]:
                        lines.append(
                            f"- **{item.get('numero_processo')}** | {item.get('tribunal')} | "
                            f"{item.get('classe_nome')} | {item.get('orgao_julgador')}"
                        )
                    lines.append("\nSe quiser uma análise jurídica de qualquer um deles, "
                                 "basta me enviar o número do processo.")
                    reply = "\n".join(lines)
                session["messages"].append({"role": "assistant", "content": reply})
                return ChatOut(message=reply, state=state)
            except Exception as e:
                reply = f"Não consegui concluir a pesquisa no DataJud. Motivo: {str(e)}"
                session["messages"].append({"role": "assistant", "content": reply})
                return ChatOut(message=reply, state=state)

    # ------------------------------------------------------------------
    # 2) Resumo/análise de processo — busca DataJud + GPT com OS 6.1
    # ------------------------------------------------------------------
    if DATAJUD_ENABLED and looks_like_process_summary_request(message):
        numbers = detect_process_numbers(message)
        if numbers:
            numero = numbers[0]
            try:
                alias = infer_datajud_alias_from_number(numero) or DATAJUD_DEFAULT_ALIAS or None
                raw   = DATAJUD.search_process_by_number(numero=numero, alias=alias)
                items = DATAJUD.extract_sources(raw)

                if not items:
                    reply = f"Não encontrei resultado no DataJud para o processo **{numero}**."
                    session["messages"].append({"role": "assistant", "content": reply})
                    return ChatOut(message=reply, state=state)

                proc            = DATAJUD.normalize_process(items[0])
                process_context = build_process_context_for_gpt(proc)

                # ✅ OS 6.1 analisa os dados do processo com inteligência jurídica real
                system_prompt = build_system_prompt(extra_context=process_context)
                msgs = [{"role": "system", "content": system_prompt}]

                # Histórico recente para manter contexto da conversa
                for item in session["messages"][-6:]:
                    if item["role"] in {"user", "assistant"}:
                        msgs.append(item)

                # Instrução explícita para usar os dados do processo
                analysis_instruction = (
                    f"{message}\n\n"
                    "[INSTRUÇÃO INTERNA: Os dados do processo acima foram obtidos em tempo real "
                    "via DataJud/CNJ. Use-os para realizar análise jurídica completa conforme "
                    "o protocolo OS 6.1. Identifique fase processual, movimentações relevantes, "
                    "riscos, próximos passos e alertas. Responda em português.]"
                )
                msgs[-1]["content"] = analysis_instruction

                reply = call_openai_chat(msgs, temperature=0.15)
                session["messages"].append({"role": "assistant", "content": reply})
                return ChatOut(message=reply, state=state)

            except DataJudError as e:
                reply = f"Não consegui consultar o DataJud agora. Motivo: {str(e)}"
                session["messages"].append({"role": "assistant", "content": reply})
                return ChatOut(message=reply, state=state)

    # ------------------------------------------------------------------
    # 3) Chat jurídico livre — usa OS 6.1 completo
    # ------------------------------------------------------------------
    uploaded_context = "\n\n".join(
        f"[Arquivo: {x.get('filename')}]\n{compact_text(x.get('text') or '', 4000)}"
        for x in session.get("uploaded_contexts", [])[-3:]
        if x.get("text")
    ).strip()

    system_prompt = build_system_prompt(extra_context=uploaded_context)
    msgs = [{"role": "system", "content": system_prompt}]

    for item in session["messages"][-12:]:
        if item["role"] in {"user", "assistant"}:
            msgs.append(item)

    try:
        reply = call_openai_chat(msgs, temperature=0.2)
    except Exception as e:
        reply = f"Erro no servidor: {str(e)}"

    session["messages"].append({"role": "assistant", "content": reply})
    session["messages"] = session["messages"][-20:]
    return ChatOut(message=reply, state=state)


def _parse_natural_search_filters(message: str) -> Optional[DataJudSearchRequest]:
    msg   = message or ""
    lower = msg.lower()

    req = DataJudSearchRequest(size=5)
    m_size = re.search(r"\b(?:size|limite|top)\s*[:=]?\s*(\d{1,2})\b", lower)
    if m_size:
        req.size = max(1, min(50, int(m_size.group(1))))

    m_classe = re.search(r"(?:classe)\s*[:=]\s*([^,;\n]+)", msg, flags=re.I)
    if m_classe:
        req.classe_nome = m_classe.group(1).strip()

    m_assunto = re.search(r"(?:assunto)\s*[:=]\s*([^,;\n]+)", msg, flags=re.I)
    if m_assunto:
        req.assunto_nome = m_assunto.group(1).strip()

    m_trib = re.search(r"(?:tribunal)\s*[:=]\s*([^,;\n]+)", msg, flags=re.I)
    if m_trib:
        req.tribunal = m_trib.group(1).strip()

    m_orgao = re.search(r"(?:órgão|orgao)\s*[:=]\s*([^,;\n]+)", msg, flags=re.I)
    if m_orgao:
        req.orgao_julgador = m_orgao.group(1).strip()

    numbers = detect_process_numbers(msg)
    if numbers:
        req.numero_processo = numbers[0]

    if not any([req.numero_processo, req.classe_nome, req.assunto_nome,
                req.tribunal, req.orgao_julgador]):
        return None
    return req
