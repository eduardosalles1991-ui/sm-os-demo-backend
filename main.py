
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
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-5.2").strip()
DEMO_KEY = (os.getenv("DEMO_KEY") or "").strip()
ALLOWED_ORIGIN = (os.getenv("ALLOWED_ORIGIN") or "*").strip()

DATAJUD_ENABLED = os.getenv("DATAJUD_ENABLED", "false").lower() == "true"
DATAJUD_BASE_URL = (os.getenv("DATAJUD_BASE_URL") or "https://api-publica.datajud.cnj.jus.br").strip().rstrip("/")
DATAJUD_API_KEY = (os.getenv("DATAJUD_API_KEY") or "").strip()
DATAJUD_TIMEOUT_S = int(os.getenv("DATAJUD_TIMEOUT_S", "25"))
DATAJUD_DEFAULT_ALIAS = (os.getenv("DATAJUD_DEFAULT_ALIAS") or "").strip()
DATAJUD_SORT_FIELD = (os.getenv("DATAJUD_SORT_FIELD") or "dataHoraUltimaAtualizacao").strip()


# -----------------------------
# FastAPI app
# -----------------------------
app = FastAPI(title="SM Chat", version="2.0.0")

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
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_session_state(session_id: str) -> Dict[str, Any]:
    session = SESSIONS.setdefault(
        session_id,
        {
            "messages": [],
            "uploaded_contexts": [],
            "last_datajud_search": None,
        },
    )
    return session


def normalize_process_number(numero: str) -> str:
    return re.sub(r"\D", "", numero or "")


PROCESS_NUMBER_RE = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")


def detect_process_numbers(text: str) -> List[str]:
    if not text:
        return []
    found = PROCESS_NUMBER_RE.findall(text)
    # remove duplicates preserving order
    out = []
    seen = set()
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def infer_datajud_alias_from_number(numero: str) -> Optional[str]:
    """
    CNJ format: NNNNNNN-DD.AAAA.J.TR.OOOO
    J:
      4 = Justiça Federal (TRF)
      5 = Justiça do Trabalho (TRT)
    """
    m = re.match(r"(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})", numero or "")
    if not m:
        return None

    justice = m.group(4)
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
        "resumo do processo",
        "resumen del proceso",
        "resumir processo",
        "resume este proceso",
        "andamento",
        "movimenta",
        "status do processo",
        "situação do processo",
        "resumo desse processo",
    ]
    return any(k in msg for k in keywords)


def looks_like_natural_search_request(message: str) -> bool:
    msg = (message or "").lower()
    search_words = ["busque", "busca", "procure", "pesquise", "pesquisa", "me mostre processos", "encontre processos"]
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
        raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
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
            texts = []
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
        self.enabled = DATAJUD_ENABLED
        self.base_url = DATAJUD_BASE_URL
        self.api_key = DATAJUD_API_KEY
        self.timeout_s = DATAJUD_TIMEOUT_S
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
                url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_s,
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

    def _build_base_payload(
        self,
        query: Dict[str, Any],
        size: int = 10,
        sort: Optional[List[Dict[str, Any]]] = None,
        search_after: Optional[List[Any]] = None,
        source_fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "size": size,
            "query": query,
            "sort": sort or [{self.default_sort_field: "desc"}],
        }
        if search_after:
            payload["search_after"] = search_after
        if source_fields:
            payload["_source"] = source_fields
        return payload

    def search_raw(
        self,
        alias: Optional[str],
        query: Dict[str, Any],
        size: int = 10,
        sort: Optional[List[Dict[str, Any]]] = None,
        search_after: Optional[List[Any]] = None,
        source_fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        tribunal_alias = (alias or self.default_alias or "").strip()
        if not tribunal_alias:
            raise DataJudError("Alias do tribunal não informado.")

        payload = self._build_base_payload(
            query=query,
            size=size,
            sort=sort,
            search_after=search_after,
            source_fields=source_fields,
        )
        return self._post(f"/{tribunal_alias}/_search", payload)

    def search_process_by_number(self, numero: str, alias: Optional[str] = None, size: int = 1) -> Dict[str, Any]:
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

    def search_paginated(
        self,
        alias: Optional[str],
        query: Dict[str, Any],
        size: int = 10,
        cursor: Optional[str] = None,
        sort: Optional[List[Dict[str, Any]]] = None,
        source_fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        search_after = decode_search_after_token(cursor)

        raw = self.search_raw(
            alias=alias,
            query=query,
            size=size,
            sort=sort,
            search_after=search_after,
            source_fields=source_fields,
        )

        hits_block = ((raw or {}).get("hits") or {})
        hits = hits_block.get("hits") or []
        total = hits_block.get("total") or {}
        total_value = total.get("value") if isinstance(total, dict) else total

        items: List[Dict[str, Any]] = []
        next_cursor: Optional[str] = None

        for hit in hits:
            source = hit.get("_source") or {}
            items.append({
                "_id": hit.get("_id"),
                "_score": hit.get("_score"),
                "sort": hit.get("sort") or [],
                "source": source,
            })

        if hits:
            last_sort = hits[-1].get("sort") or []
            next_cursor = encode_search_after_token(last_sort) if last_sort else None

        return {
            "total": total_value,
            "count": len(items),
            "items": items,
            "next_cursor": next_cursor,
            "raw_took_ms": raw.get("took"),
            "timed_out": raw.get("timed_out", False),
        }

    def extract_sources(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        hits = (((raw or {}).get("hits") or {}).get("hits") or [])
        return [item.get("_source") or {} for item in hits if item.get("_source")]

    def normalize_process(self, source: Dict[str, Any]) -> Dict[str, Any]:
        movimentos = source.get("movimentos") or []
        movimentos_sorted = sorted(
            movimentos,
            key=lambda x: x.get("dataHora") or "",
            reverse=True
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


def format_process_summary(proc: Dict[str, Any]) -> str:
    assuntos = ", ".join(proc.get("assuntos") or []) or "não identificados"

    movimentos_txt = []
    for mov in (proc.get("movimentos") or [])[:5]:
        nome = mov.get("nome", "Movimentação")
        data = mov.get("dataHora", "")
        movimentos_txt.append(f"- {data} | {nome}")

    parts = [
        f"Resumo do processo {proc.get('numero_processo')}:",
        "",
        f"É um processo no {proc.get('tribunal') or 'tribunal não identificado'}, em {proc.get('grau') or 'grau não identificado'}.",
        f"A classe processual é {proc.get('classe_nome') or 'não identificada'} e o órgão julgador é {proc.get('orgao_julgador') or 'não identificado'}.",
        f"Os assuntos cadastrados são: {assuntos}.",
    ]

    if proc.get("data_ajuizamento"):
        parts.append(f"A data de ajuizamento registrada é {proc.get('data_ajuizamento')}.")

    if proc.get("ultima_atualizacao"):
        parts.append(f"A última atualização capturada na base é {proc.get('ultima_atualizacao')}.")

    if proc.get("ultima_movimentacao_nome"):
        parts.append(
            f"A movimentação mais recente identificada foi '{proc.get('ultima_movimentacao_nome')}'"
            + (f", em {proc.get('ultima_movimentacao_data')}." if proc.get("ultima_movimentacao_data") else ".")
        )

    if movimentos_txt:
        parts.extend([
            "",
            "Últimas movimentações capturadas:",
            *movimentos_txt,
        ])

    parts.extend([
        "",
        "Se quiser, eu também posso:",
        "1. resumir em linguagem mais simples,",
        "2. destacar riscos e próximos passos,",
        "3. focar apenas nas movimentações recentes.",
    ])

    return "\n".join(parts).strip()


def parse_natural_search_filters(message: str) -> Optional[DataJudSearchRequest]:
    msg = message or ""
    lower = msg.lower()

    if not looks_like_natural_search_request(message):
        return None

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

    if not any([req.numero_processo, req.classe_nome, req.assunto_nome, req.tribunal, req.orgao_julgador]):
        return None
    return req


# -----------------------------
# OpenAI HTTP helper
# -----------------------------
def call_openai_chat(messages: List[Dict[str, str]]) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY não configurado.")

    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_MODEL,
            "messages": messages,
        },
        timeout=60,
    )
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        body = e.response.text if e.response is not None else ""
        raise RuntimeError(f"OpenAI error: {body[:1000]}")
    data = resp.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip() or "Não consegui responder agora."


# -----------------------------
# Routes
# -----------------------------
@app.get("/ping")
def ping():
    return {"ok": True, "service": "alive"}


@app.get("/health")
def health():
    return {
        "ok": True,
        "model": OPENAI_MODEL,
        "datajud": {
            "enabled": DATAJUD_ENABLED,
            "base_url": DATAJUD_BASE_URL,
            "default_alias": DATAJUD_DEFAULT_ALIAS,
        },
    }


@app.post("/session/new", response_model=SessionOut)
def session_new(x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = str(uuid.uuid4())
    SESSIONS[sid] = {
        "messages": [],
        "uploaded_contexts": [],
        "last_datajud_search": None,
    }
    return SessionOut(session_id=sid, state={})


@app.post("/upload")
async def upload(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    x_demo_key: Optional[str] = Header(default=None),
):
    auth_or_401(x_demo_key)
    session = get_session_state(session_id)
    data = await file.read()
    text = extract_text_from_upload(file, data)
    text = compact_text(text, 12000)
    entry = {
        "filename": file.filename,
        "content_type": file.content_type,
        "text": text,
    }
    session["uploaded_contexts"].append(entry)

    if text:
        return {
            "ok": True,
            "message": f"Arquivo anexado com sucesso: {file.filename}. Vou considerar esse conteúdo no contexto.",
            "filename": file.filename,
            "text_preview": text[:800],
        }

    return {
        "ok": True,
        "message": f"Arquivo anexado com sucesso: {file.filename}.",
        "filename": file.filename,
    }


@app.get("/datajud/test-process")
def datajud_test_process(numero: str, alias: Optional[str] = None):
    try:
        raw = DATAJUD.search_process_by_number(numero=numero, alias=alias)
        items = DATAJUD.extract_sources(raw)

        if not items:
            return {
                "ok": True,
                "found": False,
                "message": "Nenhum processo encontrado."
            }

        proc = DATAJUD.normalize_process(items[0])
        return {
            "ok": True,
            "found": True,
            "process": proc
        }
    except DataJudError as e:
        return {
            "ok": False,
            "error": str(e)
        }


@app.post("/datajud/search")
def datajud_search(req: DataJudSearchRequest, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    try:
        query = build_datajud_query(req)

        result = DATAJUD.search_paginated(
            alias=req.alias,
            query=query,
            size=max(1, min(req.size, 50)),
            cursor=req.cursor,
            sort=[{DATAJUD_SORT_FIELD: "desc"}],
            source_fields=[
                "numeroProcesso",
                "tribunal",
                "grau",
                "dataAjuizamento",
                "dataHoraUltimaAtualizacao",
                "classe",
                "assuntos",
                "orgaoJulgador",
                "movimentos",
                "sistema",
                "formato",
            ],
        )

        items = []
        for item in result["items"]:
            source = item.get("source") or {}
            proc = DATAJUD.normalize_process(source)
            items.append({
                "numero_processo": proc["numero_processo"],
                "tribunal": proc["tribunal"],
                "grau": proc["grau"],
                "classe_nome": proc["classe_nome"],
                "orgao_julgador": proc["orgao_julgador"],
                "assuntos": proc["assuntos"],
                "data_ajuizamento": proc["data_ajuizamento"],
                "ultima_atualizacao": proc["ultima_atualizacao"],
                "ultima_movimentacao_nome": proc["ultima_movimentacao_nome"],
                "ultima_movimentacao_data": proc["ultima_movimentacao_data"],
                "movimentos_total": proc["movimentos_total"],
            })

        return {
            "ok": True,
            "total": result["total"],
            "count": result["count"],
            "items": items,
            "next_cursor": result["next_cursor"],
            "timed_out": result["timed_out"],
            "raw_took_ms": result["raw_took_ms"],
        }
    except DataJudError as e:
        return {"ok": False, "error": str(e)}


@app.post("/chat", response_model=ChatOut)
def chat_endpoint(payload: ChatIn, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    session = get_session_state(payload.session_id)
    message = (payload.message or "").strip()
    state = payload.state or {}

    # keep short memory
    session["messages"].append({"role": "user", "content": message})
    session["messages"] = session["messages"][-20:]

    # 1) Natural DataJud search without /pesquisa
    natural_req = parse_natural_search_filters(message)
    if natural_req:
        try:
            result = datajud_search(natural_req, x_demo_key=x_demo_key)
            session["last_datajud_search"] = {
                "request": natural_req.dict(),
                "response": result,
            }
            count = result.get("count", 0)
            total = result.get("total")
            items = result.get("items") or []

            if not items:
                reply = "Não encontrei processos com esses filtros no DataJud."
            else:
                lines = [f"Encontrei {count} resultado(s)" + (f" de um total de {total}" if total is not None else "") + "."]
                for item in items[:5]:
                    lines.append(
                        f"- {item.get('numero_processo')} | {item.get('tribunal')} | {item.get('classe_nome')} | {item.get('orgao_julgador')}"
                    )
                lines.append("")
                lines.append("Se quiser, eu posso resumir qualquer um deles. Basta me mandar o número do processo.")
                reply = "\n".join(lines)

            session["messages"].append({"role": "assistant", "content": reply})
            return ChatOut(message=reply, state=state)
        except Exception as e:
            reply = f"Não consegui concluir a pesquisa natural no DataJud. Motivo: {str(e)}"
            session["messages"].append({"role": "assistant", "content": reply})
            return ChatOut(message=reply, state=state)

    # 2) Process summary path: fully natural, no structured follow-up
    if DATAJUD_ENABLED and looks_like_process_summary_request(message):
        numbers = detect_process_numbers(message)
        if numbers:
            numero = numbers[0]
            try:
                alias = infer_datajud_alias_from_number(numero) or DATAJUD_DEFAULT_ALIAS or None
                raw = DATAJUD.search_process_by_number(numero=numero, alias=alias)
                items = DATAJUD.extract_sources(raw)

                if not items:
                    reply = f"Não encontrei resultado no DataJud para o processo {numero}."
                else:
                    proc = DATAJUD.normalize_process(items[0])
                    reply = format_process_summary(proc)

                session["messages"].append({"role": "assistant", "content": reply})
                return ChatOut(message=reply, state=state)
            except DataJudError as e:
                reply = f"Não consegui consultar o DataJud agora. Motivo: {str(e)}"
                session["messages"].append({"role": "assistant", "content": reply})
                return ChatOut(message=reply, state=state)

    # 3) General free chat
    uploaded_context = "\n\n".join(
        f"[Arquivo: {x.get('filename')}]\n{compact_text(x.get('text') or '', 4000)}"
        for x in session.get("uploaded_contexts", [])[-3:]
        if x.get("text")
    ).strip()

    system_prompt = (
        "Você é um assistente conversacional livre, natural e útil. "
        "Não conduza a conversa como formulário. "
        "Não faça perguntas estruturadas em sequência salvo se o usuário pedir isso explicitamente. "
        "Se o usuário fizer perguntas sobre um processo com número CNJ e houver uma rota especializada, o sistema já tratará isso. "
        "Para o restante, responda de forma natural, clara, direta e útil. "
        "Se houver arquivos anexados com texto, use esse contexto quando relevante. "
        "Evite respostas burocráticas e evite repetir perguntas desnecessárias."
    )

    msgs = [{"role": "system", "content": system_prompt}]
    if uploaded_context:
        msgs.append({
            "role": "system",
            "content": f"Contexto de arquivos anexados pelo usuário:\n\n{uploaded_context}"
        })

    history = session["messages"][-10:]
    for item in history:
        if item["role"] in {"user", "assistant"}:
            msgs.append(item)

    try:
        reply = call_openai_chat(msgs)
    except Exception as e:
        reply = f"Erro no backend/OpenAI: {str(e)}"

    session["messages"].append({"role": "assistant", "content": reply})
    session["messages"] = session["messages"][-20:]
    return ChatOut(message=reply, state=state)
