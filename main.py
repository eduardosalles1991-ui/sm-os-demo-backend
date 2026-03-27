# -*- coding: utf-8 -*-
import os
import re
import uuid
from io import BytesIO
from typing import Any, Dict, List, Optional

import openai
import requests
from docx import Document as DocxDocument
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel, Field
from pypdf import PdfReader

# =========================
# Config
# =========================
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://correamendes.wpcomstaging.com")
DEMO_KEY = (os.getenv("DEMO_KEY") or "").strip()
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "10"))
MAX_FILES_PER_SESSION = int(os.getenv("MAX_FILES_PER_SESSION", "10"))
MAX_TOTAL_MB_PER_SESSION = int(os.getenv("MAX_TOTAL_MB_PER_SESSION", "30"))
MAX_EXCERPT_CHARS = int(os.getenv("MAX_EXCERPT_CHARS", "12000"))
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "18"))

# DataJud
DATAJUD_ENABLED = os.getenv("DATAJUD_ENABLED", "false").lower() == "true"
DATAJUD_BASE_URL = (os.getenv("DATAJUD_BASE_URL") or "https://api-publica.datajud.cnj.jus.br").rstrip("/")
DATAJUD_API_KEY = (os.getenv("DATAJUD_API_KEY") or "").strip()
DATAJUD_TIMEOUT_S = int(os.getenv("DATAJUD_TIMEOUT_S", "25"))
DATAJUD_DEFAULT_ALIAS = (os.getenv("DATAJUD_DEFAULT_ALIAS") or "").strip()

# =========================
# App
# =========================
app = FastAPI(title="S&M Free Chat + DataJud", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN] if ALLOWED_ORIGIN else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSIONS: Dict[str, Dict[str, Any]] = {}
PROCESS_NUMBER_RE = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")


# =========================
# Models
# =========================
class SessionOut(BaseModel):
    session_id: str
    message: str = ""
    state: Dict[str, Any] = Field(default_factory=dict)


class ChatIn(BaseModel):
    session_id: str
    message: str
    state: Optional[Dict[str, Any]] = None


class ChatOut(BaseModel):
    message: str
    state: Dict[str, Any] = Field(default_factory=dict)


class HealthOut(BaseModel):
    ok: bool
    model: str
    backend: str
    sessions: int
    uploads: int
    live_web_enabled: bool
    datajud_enabled: bool
    datajud_base_url: str
    datajud_default_alias: str


# =========================
# Generic helpers
# =========================
def auth_or_401(x_demo_key: Optional[str]):
    if not DEMO_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: DEMO_KEY not set.")
    if not x_demo_key or x_demo_key != DEMO_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured.")
    return OpenAI(api_key=OPENAI_API_KEY)


def friendly_backend_error(e: Exception) -> HTTPException:
    return HTTPException(status_code=500, detail=f"Backend error: {type(e).__name__}: {str(e)}")


def friendly_openai_error(e: Exception) -> HTTPException:
    if isinstance(e, openai.RateLimitError):
        return HTTPException(status_code=429, detail="OpenAI rate limit/quota.")
    if isinstance(e, openai.AuthenticationError):
        return HTTPException(status_code=401, detail="OPENAI_API_KEY inválida.")
    if isinstance(e, openai.BadRequestError):
        return HTTPException(status_code=400, detail=f"OpenAI bad request: {str(e)}")
    if isinstance(e, openai.APITimeoutError):
        return HTTPException(status_code=504, detail="OpenAI timeout.")
    if isinstance(e, openai.APIConnectionError):
        return HTTPException(status_code=503, detail="Falha de conexão com OpenAI.")
    if isinstance(e, openai.APIStatusError):
        return HTTPException(status_code=502, detail=f"OpenAI API status error: {str(e)}")
    return friendly_backend_error(e)


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def get_session(session_id: str) -> Dict[str, Any]:
    sess = SESSIONS.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess


def create_session() -> Dict[str, Any]:
    sid = str(uuid.uuid4())
    sess = {
        "session_id": sid,
        "history": [],
        "uploads": [],
        "state": {},
    }
    SESSIONS[sid] = sess
    return sess


def trim_history(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if len(messages) <= HISTORY_TURNS * 2:
        return messages
    return messages[-HISTORY_TURNS * 2 :]


def live_web_enabled() -> bool:
    return bool((os.getenv("SEARCH_API_URL") or "").strip())


# =========================
# File extraction
# =========================
def extract_txt_bytes(raw: bytes) -> str:
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return raw.decode(enc, errors="ignore")
        except Exception:
            continue
    return ""


def extract_pdf_bytes(raw: bytes) -> str:
    out = []
    reader = PdfReader(BytesIO(raw))
    for page in reader.pages[:60]:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(out)


def extract_docx_bytes(raw: bytes) -> str:
    doc = DocxDocument(BytesIO(raw))
    return "\n".join(p.text for p in doc.paragraphs)


def extract_upload_excerpt(filename: str, content_type: str, raw: bytes) -> str:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    text = ""
    if name.endswith(".pdf") or "pdf" in ctype:
        text = extract_pdf_bytes(raw)
    elif name.endswith(".docx") or "word" in ctype or "officedocument" in ctype:
        text = extract_docx_bytes(raw)
    elif name.endswith(".txt") or name.endswith(".md") or "text/" in ctype:
        text = extract_txt_bytes(raw)
    else:
        return ""
    return clean_text(text)[:MAX_EXCERPT_CHARS]


def uploads_context(uploads: List[Dict[str, Any]]) -> str:
    if not uploads:
        return ""
    chunks = []
    for idx, item in enumerate(uploads[-6:], start=1):
        excerpt = clean_text(item.get("excerpt", ""))
        if excerpt:
            chunks.append(f"[Arquivo {idx}: {item.get('name','arquivo')}] {excerpt}")
        else:
            chunks.append(f"[Arquivo {idx}: {item.get('name','arquivo')}] Arquivo anexado sem texto extraído.")
    return "\n\n".join(chunks)[:MAX_EXCERPT_CHARS]


# =========================
# DataJud
# =========================
class DataJudError(Exception):
    pass


def normalize_process_number(numero: str) -> str:
    return re.sub(r"\D", "", numero or "")


def alias_variants(alias: str) -> List[str]:
    alias = (alias or "").strip().lower().replace("/", "")
    if not alias:
        return []
    variants = []
    if alias.startswith("api_publica_"):
        variants.append(alias)
    else:
        variants.append(alias)
        variants.append(f"api_publica_{alias}")
    # dedupe preserving order
    out = []
    for x in variants:
        if x and x not in out:
            out.append(x)
    return out


def infer_alias_from_text(message: str) -> Optional[str]:
    msg = (message or "").lower()
    compact = re.sub(r"[^a-z0-9]", "", msg)

    patterns = [
        (r"\btrt\s*([1-9]|1\d|2[0-4])\b", "trt{}"),
        (r"\btrf\s*([1-6])\b", "trf{}"),
        (r"\btj\s*sp\b", "tjsp"),
        (r"\btj\s*rj\b", "tjrj"),
        (r"\btj\s*mg\b", "tjmg"),
        (r"\btj\s*rs\b", "tjrs"),
        (r"\bstj\b", "stj"),
        (r"\bstf\b", "stf"),
        (r"\btse\b", "tse"),
    ]
    for pattern, fmt in patterns:
        m = re.search(pattern, msg)
        if m:
            return fmt.format(m.group(1)) if m.groups() else fmt

    simple_map = {
        "trt2": "trt2",
        "trt3": "trt3",
        "trt15": "trt15",
        "trf1": "trf1",
        "tjsp": "tjsp",
        "tjrj": "tjrj",
        "tjmg": "tjmg",
        "stj": "stj",
        "stf": "stf",
        "tse": "tse",
    }
    for key, value in simple_map.items():
        if key in compact:
            return value
    return None


class DataJudService:
    def __init__(self):
        self.enabled = DATAJUD_ENABLED
        self.base_url = DATAJUD_BASE_URL
        self.api_key = DATAJUD_API_KEY
        self.timeout_s = DATAJUD_TIMEOUT_S
        self.default_alias = DATAJUD_DEFAULT_ALIAS

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
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout_s)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            body = e.response.text if e.response is not None else ""
            raise DataJudError(f"HTTP {getattr(e.response, 'status_code', '?')}: {body[:700]}")
        except requests.RequestException as e:
            raise DataJudError(f"Falha de conexão com DataJud: {str(e)}")
        except ValueError as e:
            raise DataJudError(f"Resposta JSON inválida: {str(e)}")

    def search_process_by_number(self, numero: str, alias: Optional[str] = None, size: int = 1) -> Dict[str, Any]:
        numero_limpo = normalize_process_number(numero)
        if not numero_limpo:
            raise DataJudError("Número do processo inválido.")

        tribunal_alias = (alias or self.default_alias or "").strip()
        aliases = alias_variants(tribunal_alias)
        if not aliases:
            raise DataJudError("Alias do tribunal não informado. Defina DATAJUD_DEFAULT_ALIAS ou informe alias.")

        payload = {
            "size": size,
            "query": {
                "match": {
                    "numeroProcesso": numero_limpo
                }
            }
        }

        last_error: Optional[Exception] = None
        for ali in aliases:
            try:
                return self._post(f"/{ali}/_search", payload)
            except DataJudError as e:
                last_error = e
        raise DataJudError(str(last_error) if last_error else "Falha ao consultar DataJud.")

    def extract_sources(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        hits = (((raw or {}).get("hits") or {}).get("hits") or [])
        out: List[Dict[str, Any]] = []
        for item in hits:
            source = item.get("_source") or {}
            if source:
                out.append(source)
        return out

    def normalize_process(self, source: Dict[str, Any]) -> Dict[str, Any]:
        movimentos = source.get("movimentos") or []
        movimentos_sorted = sorted(
            movimentos,
            key=lambda x: x.get("dataHora") or "",
            reverse=True,
        )
        ultima_mov = movimentos_sorted[0] if movimentos_sorted else None

        assuntos = source.get("assuntos") or []
        classe = source.get("classe") or {}
        orgao = source.get("orgaoJulgador") or {}
        sistema = source.get("sistema") or {}
        formato = source.get("formato") or {}

        return {
            "numero_processo": source.get("numeroProcesso"),
            "tribunal": source.get("tribunal"),
            "grau": source.get("grau"),
            "data_ajuizamento": source.get("dataAjuizamento"),
            "ultima_atualizacao": source.get("dataHoraUltimaAtualizacao"),
            "classe_nome": classe.get("nome"),
            "classe_codigo": classe.get("codigo"),
            "orgao_julgador": orgao.get("nome"),
            "sistema": sistema.get("nome"),
            "formato": formato.get("nome"),
            "assuntos": [a.get("nome") for a in assuntos if a.get("nome")],
            "movimentos_total": len(movimentos),
            "ultima_movimentacao_nome": (ultima_mov or {}).get("nome"),
            "ultima_movimentacao_data": (ultima_mov or {}).get("dataHora"),
            "movimentos": movimentos_sorted[:10],
            "raw": source,
        }


DATAJUD = DataJudService()


def looks_like_process_query(message: str) -> bool:
    msg = (message or "").lower()
    keywords = [
        "processo", "proceso", "cnj", "andamento", "movimentação", "movimentacao",
        "tribunal", "sentença", "sentenca", "acórdão", "acordao", "datajud"
    ]
    return bool(PROCESS_NUMBER_RE.search(message or "")) or any(k in msg for k in keywords)


def format_process_summary(proc: Dict[str, Any]) -> str:
    movimentos_txt = []
    for mov in (proc.get("movimentos") or [])[:5]:
        nome = mov.get("nome", "Movimentação")
        data = mov.get("dataHora", "")
        movimentos_txt.append(f"- {data} | {nome}")

    assuntos = ", ".join(proc.get("assuntos") or []) or "—"
    ult_mov = proc.get("ultima_movimentacao_nome") or "—"
    ult_mov_data = proc.get("ultima_movimentacao_data") or "—"

    msg = (
        f"Encontrei dados do processo {proc.get('numero_processo')}.\n\n"
        f"Tribunal: {proc.get('tribunal') or '—'}\n"
        f"Grau: {proc.get('grau') or '—'}\n"
        f"Classe: {proc.get('classe_nome') or '—'}\n"
        f"Órgão julgador: {proc.get('orgao_julgador') or '—'}\n"
        f"Assuntos: {assuntos}\n"
        f"Data de ajuizamento: {proc.get('data_ajuizamento') or '—'}\n"
        f"Última atualização: {proc.get('ultima_atualizacao') or '—'}\n"
        f"Última movimentação: {ult_mov} ({ult_mov_data})\n"
        f"Movimentos capturados: {proc.get('movimentos_total') or 0}"
    )
    if movimentos_txt:
        msg += "\n\nÚltimas movimentações:\n" + "\n".join(movimentos_txt)
    msg += "\n\nFonte: DataJud/CNJ"
    return msg


# =========================
# Chat model helpers
# =========================
def build_messages(sess: Dict[str, Any], user_message: str) -> List[Dict[str, str]]:
    base_system = (
        "Você é um assistente geral, conversacional e útil. "
        "Converse de forma natural, como um chat livre. "
        "Não conduza a conversa como formulário. "
        "Não faça questionário estruturado a menos que o usuário peça explicitamente um documento, plano ou coleta organizada. "
        "Se houver arquivos anexados, use-os como contexto. "
        "Se o usuário pedir informação em tempo real da internet, seja honesto: nesta implantação você não tem busca web ao vivo, a menos que um conector externo esteja configurado. "
        "Não invente fatos atuais, links, notícias, resultados de processo ou dados em tempo real. "
        "Se o usuário perguntar por andamento processual sem número CNJ ou sem dados suficientes, peça o número do processo e, se necessário, o tribunal. "
        "Responda em português, a menos que o usuário use outro idioma."
    )
    messages: List[Dict[str, str]] = [{"role": "system", "content": base_system}]
    ctx = uploads_context(sess.get("uploads", []))
    if ctx:
        messages.append({
            "role": "system",
            "content": "Contexto extraído de arquivos anexados pelo usuário:\n\n" + ctx,
        })
    history = trim_history(sess.get("history", []))
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    return messages


def call_model(messages: List[Dict[str, str]]) -> str:
    client = get_client()
    completion = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=TEMPERATURE,
    )
    text = (completion.choices[0].message.content or "").strip()
    return text or "Desculpe, não consegui gerar uma resposta útil agora."


# =========================
# Routes
# =========================
@app.get("/ping")
def ping():
    return {"ok": True, "service": "alive"}


@app.get("/health", response_model=HealthOut)
def health():
    uploads_count = sum(len(s.get("uploads", [])) for s in SESSIONS.values())
    return HealthOut(
        ok=True,
        model=MODEL,
        backend="online",
        sessions=len(SESSIONS),
        uploads=uploads_count,
        live_web_enabled=live_web_enabled(),
        datajud_enabled=DATAJUD.enabled,
        datajud_base_url=DATAJUD.base_url,
        datajud_default_alias=DATAJUD.default_alias,
    )


@app.get("/datajud/test-process")
def datajud_test_process(numero: str, alias: Optional[str] = None):
    try:
        raw = DATAJUD.search_process_by_number(numero=numero, alias=alias)
        items = DATAJUD.extract_sources(raw)
        if not items:
            return {"ok": True, "found": False, "message": "Nenhum processo encontrado."}
        proc = DATAJUD.normalize_process(items[0])
        return {"ok": True, "found": True, "process": proc}
    except DataJudError as e:
        return {"ok": False, "error": str(e)}


@app.post("/session/new", response_model=SessionOut)
def session_new(x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sess = create_session()
    return SessionOut(session_id=sess["session_id"], message="", state={})


@app.post("/chat", response_model=ChatOut)
def chat(body: ChatIn, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    try:
        sess = get_session(body.session_id)
        user_message = (body.message or "").strip()
        if not user_message:
            raise HTTPException(status_code=400, detail="Mensagem vazia")

        # DataJud shortcut
        if looks_like_process_query(user_message):
            match = PROCESS_NUMBER_RE.search(user_message or "")
            if match:
                numero = match.group(0)
                alias = infer_alias_from_text(user_message) or DATAJUD.default_alias
                try:
                    raw = DATAJUD.search_process_by_number(numero=numero, alias=alias)
                    items = DATAJUD.extract_sources(raw)
                    if items:
                        proc = DATAJUD.normalize_process(items[0])
                        reply = format_process_summary(proc)
                        sess["history"] = trim_history(sess.get("history", []) + [
                            {"role": "user", "content": user_message},
                            {"role": "assistant", "content": reply},
                        ])
                        return ChatOut(message=reply, state=sess.get("state", {}))
                    reply = f"Não encontrei resultado no DataJud para o processo {numero}."
                    sess["history"] = trim_history(sess.get("history", []) + [
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": reply},
                    ])
                    return ChatOut(message=reply, state=sess.get("state", {}))
                except DataJudError as e:
                    reply = (
                        "Não consegui consultar o DataJud agora. "
                        f"Motivo: {str(e)}\n\n"
                        "Confira o alias do tribunal e a chave da API."
                    )
                    sess["history"] = trim_history(sess.get("history", []) + [
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": reply},
                    ])
                    return ChatOut(message=reply, state=sess.get("state", {}))
            else:
                # Looks like process query but no CNJ number
                reply = (
                    "Para consultar andamento processual no DataJud, me envie o número CNJ completo do processo. "
                    "Se quiser, também pode informar o tribunal, por exemplo: TRT2, TRF1, TJSP."
                )
                sess["history"] = trim_history(sess.get("history", []) + [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": reply},
                ])
                return ChatOut(message=reply, state=sess.get("state", {}))

        # Free chat path
        messages = build_messages(sess, user_message)
        reply = call_model(messages)
        sess["history"] = trim_history(sess.get("history", []) + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": reply},
        ])
        return ChatOut(message=reply, state=sess.get("state", {}))
    except HTTPException:
        raise
    except Exception as e:
        if e.__class__.__module__.startswith("openai"):
            raise friendly_openai_error(e)
        raise friendly_backend_error(e)


@app.post("/upload")
async def upload(
    session_id: str = Form(...),
    files: List[UploadFile] = File(...),
    x_demo_key: Optional[str] = Header(default=None),
):
    auth_or_401(x_demo_key)
    try:
        sess = get_session(session_id)
        existing = sess.get("uploads", [])
        if len(existing) + len(files) > MAX_FILES_PER_SESSION:
            raise HTTPException(status_code=400, detail="Limite de arquivos por sessão excedido.")

        total_existing = sum(int(x.get("size", 0)) for x in existing)
        new_items = []
        total_new = 0

        for f in files:
            raw = await f.read()
            size = len(raw)
            total_new += size
            if size > MAX_FILE_MB * 1024 * 1024:
                raise HTTPException(status_code=400, detail=f"Arquivo muito grande: {f.filename}")
            excerpt = extract_upload_excerpt(f.filename or "arquivo", f.content_type or "", raw)
            new_items.append({
                "name": f.filename or "arquivo",
                "content_type": f.content_type or "application/octet-stream",
                "size": size,
                "excerpt": excerpt,
            })

        if total_existing + total_new > MAX_TOTAL_MB_PER_SESSION * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Limite total de arquivos por sessão excedido.")

        sess["uploads"] = existing + new_items
        return {
            "ok": True,
            "files": [
                {"name": x["name"], "size": x["size"], "has_excerpt": bool(x.get("excerpt"))}
                for x in new_items
            ],
            "count": len(sess["uploads"]),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise friendly_backend_error(e)
