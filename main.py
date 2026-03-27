# -*- coding: utf-8 -*-
import os
import uuid
import re
from io import BytesIO
from typing import Dict, Any, Optional, List

import openai
from openai import OpenAI
from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pypdf import PdfReader
from docx import Document as DocxDocument

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

app = FastAPI(title="S&M Free Chat Backend", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN] if ALLOWED_ORIGIN else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSIONS: Dict[str, Dict[str, Any]] = {}


class SessionOut(BaseModel):
    session_id: str
    message: str = ""
    state: Dict[str, Any] = {}


class ChatIn(BaseModel):
    session_id: str
    message: str
    state: Optional[Dict[str, Any]] = None


class ChatOut(BaseModel):
    message: str
    state: Dict[str, Any] = {}


class HealthOut(BaseModel):
    ok: bool
    model: str
    backend: str
    sessions: int
    uploads: int
    live_web_enabled: bool


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
        # images and unsupported binaries are stored as metadata only
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


def build_messages(sess: Dict[str, Any], user_message: str) -> List[Dict[str, str]]:
    base_system = (
        "Você é um assistente geral, conversacional e útil. "
        "Converse de forma natural, como um chat livre. "
        "Não conduza a conversa como formulário. "
        "Não faça questionário estruturado a menos que o usuário peça explicitamente um documento, plano ou coleta organizada. "
        "Se houver arquivos anexados, use-os como contexto. "
        "Se o usuário pedir informação em tempo real da internet, seja honesto: diga que nesta implantação você só tem web ao vivo se um conector de busca estiver configurado. "
        "Não invente fatos atuais, links, notícias, resultados de processo ou dados em tempo real. "
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
    )


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
