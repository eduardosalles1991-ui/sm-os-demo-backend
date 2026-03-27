# -*- coding: utf-8 -*-
import os
import uuid
import json
import base64
import re
import requests
import unicodedata
from io import BytesIO
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple

import openai
from openai import OpenAI

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

from pypdf import PdfReader

# =========================================================
# CONFIG (ENV)
# =========================================================
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://correamendes.wpcomstaging.com")
DEMO_KEY = (os.getenv("DEMO_KEY") or "").strip()
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.15"))

MANDATARIA_NOME = os.getenv("MANDATARIA_NOME", "(Nome do Mandatario/a)")
MANDATARIA_OAB = os.getenv("MANDATARIA_OAB", "OAB: (Numero de OAB)")

MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "7"))
MAX_FILES_PER_SESSION = int(os.getenv("MAX_FILES_PER_SESSION", "10"))
MAX_TOTAL_MB_PER_SESSION = int(os.getenv("MAX_TOTAL_MB_PER_SESSION", "25"))
MAX_EXCERPT_CHARS = int(os.getenv("MAX_EXCERPT_CHARS", "9000"))

FEE_MIN_TOTAL = int(os.getenv("FEE_MIN_TOTAL", "1500"))
FEE_MAX_TOTAL = int(os.getenv("FEE_MAX_TOTAL", "250000"))
FEE_VALIDITY_DAYS = int(os.getenv("FEE_VALIDITY_DAYS", "7"))

OS_6_1_PROMPT = (os.getenv("OS_6_1_PROMPT") or "").strip()
PROMPT_LOADED = bool(OS_6_1_PROMPT) and (len(OS_6_1_PROMPT) > 1200)

TIPOS_PECA = [
    "Notificação Extrajudicial",
    "Petição Inicial",
    "Contestação",
    "Réplica",
    "Recurso",
    "Minuta de Acordo",
    "Petição Intermediária (Manifestação)",
]

app = FastAPI(title="S&M OS 6.1 — Demo Backend", version="4.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# sessão em memória
UPLOADS: Dict[str, List[Dict[str, Any]]] = {}
SESSIONS: Dict[str, Dict[str, Any]] = {}


# =========================================================
# DATAJUD
# =========================================================
class DataJudError(Exception):
    pass


def normalize_process_number(numero: str) -> str:
    return re.sub(r"\D", "", numero or "")


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

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "base_url_configured": bool(self.base_url),
            "api_key_configured": bool(self.api_key),
            "default_alias": self.default_alias,
            "sort_field": self.default_sort_field,
        }

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
        numero_limpo = normalize_process_number(numero)
        if not numero_limpo:
            raise DataJudError("Número do processo inválido.")
        query = {"match": {"numeroProcesso": numero_limpo}}
        return self.search_raw(alias=alias, query=query, size=size)

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
PROCESS_NUMBER_RE = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")


def infer_datajud_alias_from_numero(numero: str) -> Optional[str]:
    digits = normalize_process_number(numero)
    if len(digits) != 20:
        return None
    if digits[13:15] == "02":
        return "api_publica_trt2"
    return None


def infer_datajud_alias_from_message(msg: str) -> Optional[str]:
    t = _norm(msg)
    if "trt2" in t or "trt 2" in t or "trt da 2" in t:
        return "api_publica_trt2"
    if "tjsp" in t or "tj sp" in t or "tribunal de justica de sao paulo" in t or "tribunal de justiça de são paulo" in t:
        return "api_publica_tjsp"
    m = PROCESS_NUMBER_RE.search(msg or "")
    if m:
        guessed = infer_datajud_alias_from_numero(m.group(0))
        if guessed:
            return guessed
    return None


def looks_like_process_query(message: str) -> bool:
    msg = _norm(message or "")
    keywords = [
        "processo", "proceso", "cnj", "andamento", "movimentacao", "movimentação",
        "sentenca", "sentença", "acordao", "acórdão", "tribunal", "movimentos"
    ]
    return bool(PROCESS_NUMBER_RE.search(message or "")) or any(k in msg for k in keywords)


def build_datajud_query(req: "DataJudSearchRequest") -> Dict[str, Any]:
    must: List[Dict[str, Any]] = []

    if req.numero_processo:
        numero_limpo = normalize_process_number(req.numero_processo)
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


# =========================================================
# HELPERS BÁSICOS
# =========================================================
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
        return HTTPException(status_code=429, detail="OpenAI rate limit/quota. Verifique Billing/Créditos.")
    if isinstance(e, openai.AuthenticationError):
        return HTTPException(status_code=401, detail="OPENAI_API_KEY inválida.")
    if isinstance(e, openai.BadRequestError):
        return HTTPException(status_code=400, detail=f"OpenAI bad request: {str(e)}")
    if isinstance(e, openai.APITimeoutError):
        return HTTPException(status_code=504, detail="OpenAI timeout. Tente novamente.")
    if isinstance(e, openai.APIConnectionError):
        return HTTPException(status_code=503, detail="Falha de conexão com OpenAI.")
    if isinstance(e, openai.APIStatusError):
        return HTTPException(status_code=502, detail=f"OpenAI API status error: {str(e)}")
    if isinstance(e, Exception) and e.__class__.__module__.startswith("openai"):
        return HTTPException(status_code=500, detail=f"OpenAI error: {type(e).__name__}: {str(e)}")
    return friendly_backend_error(e)


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s+", " ", s)
    return s


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def is_answered(v: Any) -> bool:
    return bool(str(v or "").strip())


def is_negative_reply(s: str) -> bool:
    t = _norm(s)
    return t in {"nao", "não", "n", "negativo", "sem", "nenhum", "nenhuma", "nao quero", "não quero"}


def is_positive_reply(s: str) -> bool:
    t = _norm(s)
    return t in {"sim", "s", "positivo", "ok", "quero", "pode", "pode sim", "claro"}


def fmt_brl(value: int) -> str:
    s = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def docx_to_b64(doc: Document) -> str:
    buf = BytesIO()
    doc.save(buf)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def is_placeholder(s: str) -> bool:
    t = (s or "").strip().lower()
    return (
        not t
        or "[preencher]" in t
        or "seção não preenchida" in t
        or "secao nao preenchida" in t
        or t in ("—", "-", "n/a", "na")
    )


def parse_money_br(v: str) -> float:
    if not v:
        return 0.0
    t = _norm(v)
    t = t.replace("r$", "").replace("reais", "").replace("real", "").strip()
    nums = re.findall(r"[\d\.,]+", t)
    if not nums:
        return 0.0
    raw = nums[0]
    if raw.count(".") > 0 and raw.count(",") > 0:
        raw = raw.replace(".", "").replace(",", ".")
    elif raw.count(".") > 0 and raw.count(",") == 0:
        parts = raw.split(".")
        if len(parts[-1]) == 3:
            raw = "".join(parts)
    elif raw.count(",") > 0 and raw.count(".") == 0:
        parts = raw.split(",")
        if len(parts[-1]) == 3:
            raw = "".join(parts)
        else:
            raw = raw.replace(",", ".")
    try:
        return float(raw)
    except Exception:
        return 0.0


def round_up_100(v: float) -> int:
    x = int(v)
    if x % 100 == 0:
        return x
    return ((x // 100) + 1) * 100


def get_session_state(session_id: str) -> Dict[str, Any]:
    if session_id not in SESSIONS:
        SESSIONS[session_id] = {"_session_id": session_id, "_created_at": datetime.now().isoformat()}
    return SESSIONS[session_id]


def merge_incoming_state(state: Dict[str, Any], incoming: Dict[str, Any]):
    if not incoming:
        return
    for k, v in incoming.items():
        if k.startswith("_"):
            continue
        if not is_answered(state.get(k)) and is_answered(v):
            state[k] = v


LEGAL_BASIS_TOP_K = int(os.getenv("LEGAL_BASIS_TOP_K", "5"))
VADEMECUM_PATH = (os.getenv("VADEMECUM_PATH") or "").strip()


class InlineVadeMecum:
    def __init__(self):
        self.items: List[Dict[str, Any]] = []
        self.loaded = False
        self.path_used: Optional[str] = None
        self.error: Optional[str] = None

    def candidate_paths(self) -> List[str]:
        paths = []
        if VADEMECUM_PATH:
            paths.append(VADEMECUM_PATH)
        paths.extend([
            "./data/vademecum.jsonl",
            "./vademecum.jsonl",
            "/mnt/data/vademecum_priority.jsonl",
            "/mnt/data/vademecum.jsonl",
        ])
        out = []
        for p in paths:
            if p and p not in out:
                out.append(p)
        return out

    def load(self):
        if self.loaded:
            return
        self.loaded = True
        for path in self.candidate_paths():
            try:
                if not os.path.exists(path):
                    continue
                items = []
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            items.append(json.loads(line))
                        except Exception:
                            continue
                self.items = items
                self.path_used = path
                self.error = None
                return
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
        if not self.path_used and not self.error:
            self.error = "vademecum.jsonl não encontrado"

    def status(self) -> Dict[str, Any]:
        self.load()
        return {
            "available": bool(self.items),
            "items": len(self.items),
            "path": self.path_used,
            "error": self.error,
        }

    def search(self, query: str, area: Optional[str] = None, top_k: int = 5) -> List[Dict[str, Any]]:
        self.load()
        if not self.items:
            return []
        q = _norm(query)
        q_tokens = set(re.findall(r"[a-z0-9]{3,}", q))
        area_n = _norm(area) if area else ""
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for item in self.items:
            if item.get("revogado"):
                continue
            item_area = _norm(str(item.get("area") or ""))
            if area_n and item_area and area_n != item_area:
                if not (area_n in item_area or item_area in area_n):
                    continue
            haystack = " ".join([
                _norm(str(item.get("texto_limpo") or "")),
                _norm(str(item.get("texto") or "")),
                " ".join(_norm(str(x)) for x in (item.get("temas") or [])),
                " ".join(_norm(str(x)) for x in (item.get("palavras_chave") or [])),
                _norm(str(item.get("fonte") or "")),
                _norm(str(item.get("diploma") or "")),
            ]).strip()
            if not haystack:
                continue
            h_tokens = set(re.findall(r"[a-z0-9]{3,}", haystack))
            overlap = len(q_tokens & h_tokens)
            if overlap <= 0:
                continue
            score = float(overlap)
            fonte = _norm(str(item.get("fonte") or ""))
            if "trabalhista" in q_tokens and fonte == "clt":
                score += 2.0
            if "consumidor" in q_tokens and fonte == "cdc":
                score += 2.0
            if "civil" in q_tokens and fonte in {"cc", "cpc"}:
                score += 1.5
            if item.get("vigencia_status") == "vigente":
                score += 0.35
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for score, item in scored[:top_k]:
            out.append({
                "score": round(score, 4),
                "fonte": item.get("fonte"),
                "diploma": item.get("diploma"),
                "artigo": item.get("artigo"),
                "texto": item.get("texto"),
                "area": item.get("area"),
                "temas": item.get("temas", []),
                "palavras_chave": item.get("palavras_chave", []),
                "pagina_inicial_pdf": item.get("pagina_inicial_pdf"),
            })
        return out


VADEMECUM = InlineVadeMecum()


def infer_normative_area(state: Dict[str, Any]) -> Optional[str]:
    area = _norm(str(state.get("area_subarea") or ""))
    if not area:
        return None
    if any(x in area for x in ["trabalh", "emprego", "clt"]):
        return "trabalhista"
    if any(x in area for x in ["consum", "cdc"]):
        return "consumidor"
    if any(x in area for x in ["penal", "crime"]):
        return "penal"
    if any(x in area for x in ["processual civil", "cpc"]):
        return "processual_civil"
    if any(x in area for x in ["civil", "contrato", "indenizacao", "indenização", "locacao", "locação", "familia", "família"]):
        return "civil"
    return None


def build_legal_basis(state: Dict[str, Any], top_k: int = 5) -> List[Dict[str, Any]]:
    facts = " ".join([
        str(state.get("fatos_cronologia") or ""),
        str(state.get("objetivo_cliente") or ""),
        str(state.get("provas_existentes") or ""),
        str(state.get("tipo_peca") or ""),
        str(state.get("notas_adicionais") or ""),
        str(state.get("area_subarea") or ""),
    ]).strip()
    if not facts:
        return []
    area = infer_normative_area(state)
    return VADEMECUM.search(facts, area=area, top_k=top_k)


def legal_basis_text(norms: List[Dict[str, Any]]) -> str:
    if not norms:
        return "Base normativa ainda não localizada no Vade Mecum."
    chunks = []
    for n in norms:
        fonte = n.get("fonte") or n.get("diploma") or "Norma"
        art = n.get("artigo")
        head = f"{fonte}, art. {art}" if art else str(fonte)
        texto = clean_text(str(n.get("texto") or ""))
        if len(texto) > 320:
            texto = texto[:317].rstrip() + "..."
        chunks.append(f"- {head}: {texto}")
    return "\n".join(chunks)


TIPO_PECA_ALIASES = {
    "notificacao extrajudicial": "Notificação Extrajudicial",
    "peticao inicial": "Petição Inicial",
    "inicial": "Petição Inicial",
    "contestacao": "Contestação",
    "replica": "Réplica",
    "recurso": "Recurso",
    "acordo": "Minuta de Acordo",
    "manifestacao": "Petição Intermediária (Manifestação)",
    "peticao intermediaria": "Petição Intermediária (Manifestação)",
}

def map_tipo_peca(user_text: str) -> Optional[str]:
    t = _norm(user_text)
    if t.isdigit():
        idx = int(t) - 1
        if 0 <= idx < len(TIPOS_PECA):
            return TIPOS_PECA[idx]
    return TIPO_PECA_ALIASES.get(t)


def detect_tipo_peca_in_text(user_text: str) -> Optional[str]:
    t = _norm(user_text)
    exact = map_tipo_peca(t)
    if exact:
        return exact

    ordered_aliases = sorted(TIPO_PECA_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True)
    for alias, mapped in ordered_aliases:
        if alias in t:
            return mapped

    if "notificacao" in t and "extrajudicial" in t:
        return "Notificação Extrajudicial"
    if "peticao" in t and "inicial" in t:
        return "Petição Inicial"
    if "peticao" in t and "intermediaria" in t:
        return "Petição Intermediária (Manifestação)"
    if "contestacao" in t:
        return "Contestação"
    if "replica" in t:
        return "Réplica"
    if "recurso" in t:
        return "Recurso"
    if "acordo" in t:
        return "Minuta de Acordo"
    return None


LABEL_RE = re.compile(r"^\s*([A-Za-zÀ-ÿ\/ _]+)\s*[:\-]\s*(.+?)\s*$")
LABEL_TO_KEY = {
    "area": "area_subarea",
    "área": "area_subarea",
    "areasubarea": "area_subarea",
    "area/subarea": "area_subarea",
    "fase": "fase",
    "objetivo": "objetivo_cliente",
    "objetivocliente": "objetivo_cliente",
    "partes": "partes",
    "contratante": "contratante_nome",
    "contratante/recebedor": "contratante_nome",
    "peca": "tipo_peca",
    "peça": "tipo_peca",
    "tipopeca": "tipo_peca",
    "fatos": "fatos_cronologia",
    "provas": "provas_existentes",
    "urgencia": "urgencia_prazo",
    "urgência": "urgencia_prazo",
    "valor": "valor_envovido",
    "valorimpacto": "valor_envovido",
    "info": "notas_adicionais",
    "partecontraria": "parte_contraria_nome",
    "cidadeuf": "cidade_uf",
    "anofato": "ano_fato",
    "advogado": "advogado_assinatura",
}


def parse_labeled_answer(msg: str) -> Optional[Tuple[str, str]]:
    m = LABEL_RE.match(msg or "")
    if not m:
        return None
    label = _norm(m.group(1)).replace(" ", "").replace("_", "")
    key = LABEL_TO_KEY.get(label)
    if not key:
        return None
    return key, clean_text(m.group(2))


def set_value(state: Dict[str, Any], key: str, value: str):
    value = clean_text(value)
    if key == "tipo_peca":
        mapped = map_tipo_peca(value)
        if mapped:
            state["tipo_peca"] = mapped
        return
    if key in ("valor_envovido", "valor_envolvido"):
        state["valor_envovido"] = value
        state["valor_envolvido"] = value
        return
    state[key] = value


def captured_view(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "Área/Subárea": state.get("area_subarea") or "—",
        "Fase": state.get("fase") or "—",
        "Objetivo": state.get("objetivo_cliente") or "—",
        "Partes": state.get("partes") or "—",
        "Contratante/Recebedor": state.get("contratante_nome") or "—",
        "Tipo de peça": state.get("tipo_peca") or "—",
        "Fatos (cronologia)": state.get("fatos_cronologia") or "—",
        "Provas existentes": state.get("provas_existentes") or "—",
        "Urgência/Prazo": state.get("urgencia_prazo") or "—",
        "Valor/Impacto": state.get("valor_envolvido") or state.get("valor_envovido") or "—",
        "Notas adicionais": state.get("notas_adicionais") or "—",
        "Parte contrária": state.get("parte_contraria_nome") or "—",
        "Cidade/UF": state.get("cidade_uf") or "—",
        "Ano do fato": state.get("ano_fato") or "—",
        "Advogado(a)/OAB": state.get("advogado_assinatura") or "—",
        "Uploads": [f.get("filename") for f in UPLOADS.get(state.get("_session_id", ""), [])],
    }

# =========================================================
# CONVERSATIONAL INTAKE HELPERS
# =========================================================
CONVERSATIONAL_FIELDS = [
    "area_subarea",
    "fase",
    "objetivo_cliente",
    "partes",
    "contratante_nome",
    "tipo_peca",
    "fatos_cronologia",
    "provas_existentes",
    "urgencia_prazo",
    "valor_envovido",
    "notas_adicionais",
    "parte_contraria_nome",
    "cidade_uf",
    "ano_fato",
    "advogado_assinatura",
]

CRITICAL_FIELDS = [
    "area_subarea",
    "objetivo_cliente",
    "partes",
    "tipo_peca",
    "fatos_cronologia",
    "provas_existentes",
]

SECONDARY_FIELDS = [
    "fase",
    "contratante_nome",
    "urgencia_prazo",
    "valor_envovido",
    "notas_adicionais",
    "parte_contraria_nome",
    "cidade_uf",
    "ano_fato",
    "advogado_assinatura",
]


def conversational_missing(state: Dict[str, Any], fields: List[str]) -> List[str]:
    out = []
    for k in fields:
        if k in ("valor_envovido", "valor_envolvido"):
            if not is_answered(state.get("valor_envovido")) and not is_answered(state.get("valor_envolvido")):
                out.append("valor_envovido")
        elif not is_answered(state.get(k)):
            out.append(k)
    return out


FASE_ALIASES = {
    "consultivo": "consultivo",
    "consultiva": "consultivo",
    "consultoria": "consultivo",
    "pre contencioso": "pré-contencioso",
    "pré contencioso": "pré-contencioso",
    "pre-contencioso": "pré-contencioso",
    "pré-contencioso": "pré-contencioso",
    "extrajudicial": "pré-contencioso",
    "processo": "processo",
    "processual": "processo",
    "judicial": "processo",
    "recurso": "recurso",
    "recursal": "recurso",
    "execucao": "execução",
    "execução": "execução",
    "cumprimento": "execução",
}

FOLLOWUP_PRIORITY = [
    "objetivo_cliente",
    "partes",
    "provas_existentes",
    "area_subarea",
    "fase",
    "urgencia_prazo",
    "tipo_peca",
    "advogado_assinatura",
    "contratante_nome",
    "parte_contraria_nome",
    "cidade_uf",
    "valor_envovido",
    "ano_fato",
    "notas_adicionais",
]

def normalize_fase(value: str) -> str:
    t = _norm(value)
    return FASE_ALIASES.get(t, clean_text(value))


def maybe_extract_lawyer_name(message: str) -> Optional[str]:
    raw = clean_text(message)
    t = _norm(raw)

    if "oab" in t:
        return raw

    if any(k in t for k in ["advog", "nome do adv", "nome/oab", "nome do dvgdo", "assinatura"]):
        part = raw
        m = re.search(r"(?:advogad[oa]?|adv|oab|assinatura|nome do advogado|nome/oab)[^A-Za-zÀ-ÿ0-9]*[:\-]?\s*(.+)$", raw, flags=re.I)
        if m:
            part = m.group(1).strip()
        elif " e " in raw.lower():
            part = raw.split(" e ")[-1].strip()

        part = re.sub(r"^(do|da|de|del)\s+", "", part, flags=re.I).strip(" ,.-")
        if len(part.split()) >= 2 and len(part) <= 120:
            return part

    if len(raw.split()) in (2, 3) and all(x.isalpha() or x.replace(".", "").isalpha() for x in raw.replace("-", " ").split()):
        return raw

    return None


def quick_extract_from_short_reply(message: str, state: Dict[str, Any]) -> Dict[str, Any]:
    raw = clean_text(message)
    t = _norm(raw)
    out: Dict[str, Any] = {}

    if len(raw) <= 40:
        if t in FASE_ALIASES:
            out["fase"] = normalize_fase(raw)

    if not out.get("fase"):
        for alias, mapped in FASE_ALIASES.items():
            if re.search(rf"\b{re.escape(alias)}\b", t):
                out["fase"] = mapped
                break

    if any(k in t for k in ["urg", "prazo", "48h", "24h", "amanha", "amanhã", "hoje", "imediat", "critico", "crítico"]):
        if len(raw) <= 160:
            out["urgencia_prazo"] = raw

    lawyer = maybe_extract_lawyer_name(raw)
    if lawyer and not is_answered(state.get("advogado_assinatura")):
        out["advogado_assinatura"] = lawyer

    if any(k in t for k in ["sem urg", "sem prazo", "sem urgencia", "sem urgência"]):
        out["urgencia_prazo"] = raw

    return out


def build_short_case_summary(state: Dict[str, Any]) -> str:
    parts = []
    if is_answered(state.get("area_subarea")):
        parts.append(f"área: {state.get('area_subarea')}")
    if is_answered(state.get("objetivo_cliente")):
        parts.append(f"objetivo: {state.get('objetivo_cliente')}")
    if is_answered(state.get("tipo_peca")):
        parts.append(f"documento-base: {state.get('tipo_peca')}")
    if not parts:
        return "Já captei o núcleo do caso."
    return "Resumo até aqui: " + " | ".join(parts) + "."


OUTPUT_REQUIRED_FIELDS = {
    "piece": ["area_subarea", "objetivo_cliente", "partes", "tipo_peca", "fatos_cronologia", "provas_existentes"],
    "report": ["area_subarea", "objetivo_cliente", "partes", "fatos_cronologia", "provas_existentes"],
    "proposal": ["area_subarea", "objetivo_cliente", "partes", "tipo_peca"],
}

OUTPUT_MISSING_PRIORITY = [
    "tipo_peca",
    "objetivo_cliente",
    "partes",
    "fatos_cronologia",
    "provas_existentes",
    "area_subarea",
    "fase",
    "urgencia_prazo",
    "contratante_nome",
    "advogado_assinatura",
    "parte_contraria_nome",
    "cidade_uf",
    "valor_envovido",
    "ano_fato",
    "notas_adicionais",
]


def required_missing_for_outputs(state: Dict[str, Any], outputs: List[str]) -> List[str]:
    req = []
    for out in outputs or []:
        req.extend(OUTPUT_REQUIRED_FIELDS.get(out, []))
    missing = conversational_missing(state, list(dict.fromkeys(req)))
    ordered = [f for f in OUTPUT_MISSING_PRIORITY if f in missing]
    return ordered + [f for f in missing if f not in ordered]


def state_expected_field(state: Dict[str, Any]) -> Optional[str]:
    val = state.get("_expected_field")
    return val if isinstance(val, str) and val else None


def set_expected_field(state: Dict[str, Any], field: Optional[str]):
    if field:
        state["_expected_field"] = field
    else:
        state.pop("_expected_field", None)


def pending_outputs(state: Dict[str, Any]) -> List[str]:
    raw = state.get("_pending_outputs") or []
    if isinstance(raw, list):
        return [x for x in raw if x in {"piece", "proposal", "report"}]
    return []


def set_pending_outputs(state: Dict[str, Any], outputs: List[str]):
    cleaned = []
    for item in outputs or []:
        if item in {"piece", "proposal", "report"} and item not in cleaned:
            cleaned.append(item)
    if cleaned:
        state["_pending_outputs"] = cleaned
    else:
        state.pop("_pending_outputs", None)


def clear_pending_outputs(state: Dict[str, Any]):
    state.pop("_pending_outputs", None)


def should_cancel_pending(message: str) -> bool:
    t = _norm(message)
    phrases = [
        "nao gerar", "não gerar", "sem gerar", "nao quero gerar", "não quero gerar",
        "gera depois", "gerar depois", "deixa para depois", "deixe para depois",
        "aguarde para gerar", "espera para gerar", "espere para gerar"
    ]
    return any(x in t for x in phrases)


def apply_expected_field_answer(state: Dict[str, Any], message: str) -> bool:
    field = state_expected_field(state)
    if not field:
        return False

    raw = clean_text(message)
    t = _norm(raw)
    if not raw:
        return False

    explicit_piece = detect_tipo_peca_in_text(raw)
    if explicit_piece and field not in {"tipo_peca", "objetivo_cliente"}:
        return False

    def done():
        set_expected_field(state, None)
        return True

    if field == "tipo_peca":
        mapped = detect_tipo_peca_in_text(raw) or map_tipo_peca(raw)
        if mapped:
            state["tipo_peca"] = mapped
            return done()
        return False

    if field == "fase":
        norm = normalize_fase(raw)
        phase_markers = ["consultiv", "pré", "pre", "contenc", "process", "recurs", "execu", "judicial", "extrajudicial"]
        if t in FASE_ALIASES or any(marker in t for marker in phase_markers):
            state["fase"] = norm
            return done()
        return False

    if field == "urgencia_prazo":
        if is_negative_reply(raw):
            state["urgencia_prazo"] = "sem urgência informada"
            return done()
        state["urgencia_prazo"] = raw
        return done()

    if field == "advogado_assinatura":
        if is_negative_reply(raw):
            state["advogado_assinatura"] = "não informado"
            return done()
        lawyer = maybe_extract_lawyer_name(raw) or raw
        if len(clean_text(lawyer)) >= 3:
            state["advogado_assinatura"] = clean_text(lawyer)
            return done()
        return False

    if field == "ano_fato":
        years = re.findall(r"(19\d{2}|20\d{2})", raw)
        if years:
            state["ano_fato"] = ", ".join(dict.fromkeys(years))
            return done()
        if len(raw) <= 20:
            state["ano_fato"] = raw
            return done()
        return False

    if field in {"valor_envovido", "valor_envolvido"}:
        if is_negative_reply(raw):
            state["valor_envovido"] = "não informado"
            state["valor_envolvido"] = "não informado"
            return done()
        if parse_money_br(raw) > 0 or len(raw) <= 120:
            state["valor_envovido"] = raw
            state["valor_envolvido"] = raw
            return done()
        return False

    if field in {"contratante_nome", "parte_contraria_nome", "cidade_uf", "objetivo_cliente", "partes", "provas_existentes", "fatos_cronologia", "notas_adicionais", "area_subarea"}:
        if is_negative_reply(raw) and field in {"cidade_uf", "contratante_nome", "parte_contraria_nome", "notas_adicionais"}:
            state[field] = "não informado"
            return done()
        if field == "objetivo_cliente" and explicit_piece and len(raw) <= 40:
            state[field] = raw
            return done()
        if len(raw) >= 2:
            state[field] = raw
            return done()
        return False

    return False


def next_missing_field(state: Dict[str, Any]) -> Optional[str]:
    crit_missing = conversational_missing(state, CRITICAL_FIELDS)
    sec_missing = conversational_missing(state, SECONDARY_FIELDS)
    missing = crit_missing + [x for x in sec_missing if x not in crit_missing]
    for field in FOLLOWUP_PRIORITY:
        if field in missing:
            return field
    return missing[0] if missing else None


def question_for_field(field: str, state: Dict[str, Any]) -> str:
    if field == "tipo_peca":
        return "Qual documento você quer primeiro? Ex.: petição inicial, notificação extrajudicial, contestação, réplica, recurso, manifestação ou proposta de honorários."
    if field == "objetivo_cliente":
        return "Qual é o objetivo do cliente, em termos práticos? Ex.: cobrar verbas, rescindir, indenização, defesa, acordo, reverter penalidade."
    if field == "partes":
        return "Quem são as partes e qual é a relação entre elas?"
    if field == "provas_existentes":
        return "Quais provas já existem? Ex.: mensagens, contrato, holerites, áudios, testemunhas, fotos, laudos."
    if field == "fase":
        return "Em que fase isso está? Ex.: consultivo, pré-contencioso, processo, recurso ou execução."
    if field == "urgencia_prazo":
        return "Há urgência ou prazo crítico? Se sim, qual prazo?"
    if field == "advogado_assinatura":
        return "Quer preencher agora o nome/OAB do advogado(a) que vai assinar?"
    if field == "contratante_nome":
        return "Qual é o nome completo do contratante/recebedor?"
    if field == "parte_contraria_nome":
        return "Qual é o nome da empresa ou parte contrária?"
    if field == "cidade_uf":
        return "Qual é a cidade/UF do caso?"
    if field == "valor_envovido":
        return "Há valor envolvido ou estimativa econômica do caso?"
    if field == "ano_fato":
        return "Qual é o ano principal dos fatos?"
    return "Me confirme o próximo dado que falta para eu fechar a base do caso."


def build_conversational_followup(state: Dict[str, Any]) -> str:
    summary = build_short_case_summary(state)
    pending = pending_outputs(state)

    if pending:
        missing = required_missing_for_outputs(state, pending)
        next_field = missing[0] if missing else None
        if next_field:
            set_expected_field(state, next_field)
            labels = {
                "piece": state.get("tipo_peca") or "a peça",
                "proposal": "a proposta de honorários",
                "report": "o relatório estratégico",
            }
            requested_label = ", ".join(labels[o] for o in pending if o in labels)
            return summary + f"\n\nPara eu liberar {requested_label} com segurança, falta confirmar este ponto:\n" + question_for_field(next_field, state)
        set_expected_field(state, None)
        return summary + "\n\nBase pronta. Vou seguir com a geração solicitada."

    next_field = next_missing_field(state)
    if next_field:
        set_expected_field(state, next_field)
        return summary + "\n\n" + question_for_field(next_field, state)

    set_expected_field(state, None)
    tipo = state.get("tipo_peca") or "documento"
    return summary + f"\n\nBase suficiente. Quando quiser, eu já posso gerar a {tipo}, a proposta de honorários ou o relatório estratégico."

def extract_fields_from_free_text(client: OpenAI, state: Dict[str, Any], message: str) -> Dict[str, Any]:
    extraction_prompt = """
Você vai extrair informações jurídicas de uma mensagem livre do usuário.
Retorne APENAS JSON.

Regras:
- Extraia somente o que estiver explícito ou fortemente implícito.
- Não invente nomes, datas, cidades, valores, parte contrária ou tipo de peça.
- Se o usuário não falou algo, deixe null.
- Se houver um campo esperado no payload, priorize extrair esse campo da mensagem curta.
- 'fatos_cronologia' deve ser sempre UMA STRING curta e organizada, nunca lista, nunca JSON, nunca array.
- Se a mensagem trouxer uma narrativa longa, resuma os fatos em 1 parágrafo objetivo, preferencialmente até ~900 caracteres.
- Se houver menção clara do objetivo do cliente (ex.: cobrar verbas, rescindir contrato, indenização, defesa, recurso), preencha 'objetivo_cliente'.
- Só preencha 'tipo_peca' quando o usuário pedir expressamente um documento ou quando isso estiver muito claro. Não deduza "Minuta de Acordo" apenas porque há interesse financeiro ou tentativa de solução.
- Se o usuário mencionar uma peça, normalize para um destes valores exatos:
  "Notificação Extrajudicial",
  "Petição Inicial",
  "Contestação",
  "Réplica",
  "Recurso",
  "Minuta de Acordo",
  "Petição Intermediária (Manifestação)"
- Não devolva arrays em nenhum campo textual.

JSON:
{
  "area_subarea": null,
  "fase": null,
  "objetivo_cliente": null,
  "partes": null,
  "contratante_nome": null,
  "tipo_peca": null,
  "fatos_cronologia": null,
  "provas_existentes": null,
  "urgencia_prazo": null,
  "valor_envovido": null,
  "notas_adicionais": null,
  "parte_contraria_nome": null,
  "cidade_uf": null,
  "ano_fato": null,
  "advogado_assinatura": null
}
"""
    payload = {
        "state_atual": captured_view(state),
        "campo_esperado": state_expected_field(state),
        "pedidos_pendentes": pending_outputs(state),
        "mensagem_usuario": message,
    }

    r = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": extraction_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )

    data = json.loads(r.choices[0].message.content)
    cleaned: Dict[str, Any] = {}

    for k in CONVERSATIONAL_FIELDS:
        v = data.get(k)
        if v is None:
            continue
        txt = clean_text(str(v))
        if not txt or txt.lower() == "null":
            continue

        if k == "tipo_peca":
            if state_expected_field(state) == "objetivo_cliente" and not data.get("objetivo_cliente"):
                cleaned["objetivo_cliente"] = txt
            else:
                mapped = map_tipo_peca(txt) or detect_tipo_peca_in_text(txt) or txt
                if mapped in TIPOS_PECA:
                    cleaned[k] = mapped
        elif k == "fase":
            cleaned[k] = normalize_fase(txt)
        elif k in ("valor_envovido", "valor_envolvido"):
            cleaned["valor_envovido"] = txt
            cleaned["valor_envolvido"] = txt
        else:
            cleaned[k] = txt

    return cleaned


def merge_conversational_update(state: Dict[str, Any], new_data: Dict[str, Any]):
    for k, v in (new_data or {}).items():
        if not is_answered(v):
            continue

        if k in ("valor_envovido", "valor_envolvido"):
            state["valor_envovido"] = v
            state["valor_envolvido"] = v
            continue

        if k == "fase":
            v = normalize_fase(str(v))

        current = state.get(k)
        if not is_answered(current) or _norm(str(current)) in {"nao informado", "não informado", "nao definida", "não definida", "sem urgencia informada", "sem urgência informada"}:
            state[k] = v
        else:
            if k in {"fatos_cronologia", "provas_existentes", "notas_adicionais"}:
                cur = clean_text(str(current))
                nv = clean_text(str(v))
                if nv and nv not in cur:
                    state[k] = f"{cur} | {nv}"


GENERATION_TRIGGER_TERMS = [
    "gera", "gerar", "gere", "criar", "crie", "elaborar", "elabore", "redigir",
    "redija", "fazer", "faça", "montar", "monte", "minuta", "peticao", "petição",
    "contestacao", "contestação", "replica", "réplica", "recurso", "notificacao",
    "notificação", "proposta", "honorarios", "honorários", "relatorio", "relatório",
    "diagnostico", "diagnóstico", "documento", "documentos"
]

ALL_DOC_TERMS = [
    "tudo", "todos", "os 3", "3 doc", "tres doc", "três doc",
    "pacote completo", "completo", "todos os documentos", "todos os docx"
]


def wants_generation(message: str) -> bool:
    m = _norm(message)
    return any(term in m for term in GENERATION_TRIGGER_TERMS)


def wants_all_documents(message: str) -> bool:
    m = _norm(message)
    return any(term in m for term in ALL_DOC_TERMS)


def detect_requested_outputs(message: str, state: Dict[str, Any]) -> List[str]:
    m = _norm(message)
    outputs: List[str] = []
    expected = state_expected_field(state)

    if expected and expected != "tipo_peca" and not wants_generation(message) and len(clean_text(message).split()) <= 6:
        return []

    if wants_all_documents(message):
        return ["report", "proposal", "piece"]

    if any(term in m for term in [
        "proposta de honorarios", "proposta honorarios", "proposta de honorários",
        "honorarios", "honorários", "fee proposal", "propuesta de honorarios"
    ]):
        outputs.append("proposal")

    if any(term in m for term in [
        "relatorio", "relatório", "diagnostico", "diagnóstico",
        "analise estrategica", "análise estratégica", "parecer", "informe"
    ]):
        outputs.append("report")

    explicit_piece_type = detect_tipo_peca_in_text(message)
    explicit_piece_language = any(term in m for term in [
        "peticao", "petição", "contestacao", "contestação", "replica", "réplica",
        "recurso", "notificacao", "notificação", "manifestacao", "manifestação",
        "acordo", "minuta", "peca", "peça"
    ])

    if explicit_piece_type or explicit_piece_language:
        outputs.append("piece")

    if not outputs and wants_generation(message):
        if state.get("tipo_peca"):
            outputs.append("piece")
        else:
            outputs.append("report")

    deduped: List[str] = []
    for item in outputs:
        if item not in deduped:
            deduped.append(item)
    return deduped


def should_generate_now(state: Dict[str, Any], message: str) -> bool:
    outputs = detect_requested_outputs(message, state) or pending_outputs(state)
    if not outputs:
        return False
    return len(required_missing_for_outputs(state, outputs)) == 0


def preview_text(text: str, limit: int = 2200) -> str:
    txt = (text or "").strip()
    if len(txt) <= limit:
        return txt
    return txt[: limit - 3].rstrip() + "..."


def build_report_preview(report: Dict[str, Any]) -> str:
    sec = report.get("secoes") or {}
    parts = [
        f"Força da tese: {report.get('forca_tese', '—')}",
        f"Risco de improcedência: {report.get('risco_improcedencia', '—')}",
        "",
        str(sec.get("2_SINTESE", "") or ""),
        "",
        "Ações prioritárias:",
        str(sec.get("15_ACOES_PRIORITARIAS", "") or ""),
    ]
    return preview_text("\n".join([p for p in parts if p is not None and str(p).strip()]))


def build_proposal_preview(state: Dict[str, Any], fee: Dict[str, Any]) -> str:
    total_num = int(fee.get("total") or 0)
    entrada_num = int(fee.get("entrada") or 0)
    parcelas = int(fee.get("parcelas") or 0)
    saldo_num = max(0, total_num - entrada_num)
    parcela_num = round(saldo_num / parcelas) if parcelas else saldo_num

    parts = [
        f"Contratante: {state.get('contratante_nome') or '[PREENCHER]'}",
        f"Objeto: {state.get('tipo_peca') or 'Atuação jurídica no caso informado'}",
        f"Entrada: {fmt_brl(entrada_num) if entrada_num else '—'}",
        f"Saldo: {fmt_brl(saldo_num) if saldo_num else '—'}",
        f"Parcelamento: {parcelas}x de {fmt_brl(parcela_num) if parcela_num else '—'}" if parcelas else "Parcelamento: —",
        f"Total: {fmt_brl(total_num) if total_num else '—'}",
        "",
        str(fee.get("justificativa_curta", "") or ""),
    ]
    return preview_text("\n".join(parts))


def build_piece_preview(report: Dict[str, Any]) -> str:
    return preview_text(str(report.get("minuta_peca", "") or ""))


def build_generation_success_message(state: Dict[str, Any], outputs: List[str]) -> str:
    labels = {
        "piece": state.get("tipo_peca") or "minuta da peça",
        "proposal": "a proposta de honorários",
        "report": "o relatório estratégico",
    }
    generated = [labels[o] for o in outputs if o in labels]

    if len(generated) == 1:
        head = f"✅ Ya preparé {generated[0]}."
    elif len(generated) == 2:
        head = f"✅ Ya preparé {generated[0]} e {generated[1]}."
    else:
        head = "✅ Ya preparé o pacote documental solicitado."

    if "piece" in outputs and "proposal" not in outputs:
        tail = "\n\nPróximo passo recomendado: quer que eu faça agora a proposta de honorários?"
    elif "proposal" in outputs and "piece" not in outputs and state.get("tipo_peca"):
        tail = f"\n\nPróximo passo recomendado: quer que eu gere agora a {state.get('tipo_peca')}?"
    elif "report" in outputs and "piece" not in outputs and state.get("tipo_peca"):
        tail = f"\n\nPróximo passo recomendado: quer que eu gere agora a {state.get('tipo_peca')}?"
    elif "report" not in outputs:
        tail = "\n\nSe quiser, depois eu também posso montar o relatório estratégico."
    else:
        tail = "\n\nSe quiser, eu também posso ajustar os documentos, mudar a estratégia ou gerar a próxima peça."

    return head + tail


def build_generation_blocked_message(state: Dict[str, Any], requested_outputs: List[str]) -> str:
    summary = build_short_case_summary(state)
    labels = {
        "piece": state.get("tipo_peca") or "a peça",
        "proposal": "a proposta de honorários",
        "report": "o relatório estratégico",
    }
    requested_label = ", ".join(labels[o] for o in requested_outputs if o in labels) or "o documento"

    pretty = {
        "area_subarea": "área/subárea jurídica",
        "objetivo_cliente": "objetivo do cliente",
        "partes": "partes envolvidas",
        "tipo_peca": "tipo de peça",
        "fatos_cronologia": "fatos em ordem cronológica",
        "provas_existentes": "provas existentes",
        "fase": "fase atual",
        "contratante_nome": "nome do contratante/recebedor",
        "urgencia_prazo": "urgência ou prazo",
        "valor_envovido": "valor envolvido",
        "advogado_assinatura": "nome/OAB do advogado(a)",
    }

    missing = required_missing_for_outputs(state, requested_outputs)
    next_field = missing[0] if missing else None
    if not next_field:
        return summary + f"\n\nAinda não consegui liberar {requested_label}. Me envie mais detalhes do caso para eu fechar a base."

    set_expected_field(state, next_field)
    ask = question_for_field(next_field, state)
    return summary + f"\n\nPara eu gerar {requested_label} com segurança, ainda falta este ponto: {pretty.get(next_field, next_field)}.\n{ask}"

def normalize_points(raw: Any) -> List[str]:
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw if str(x).strip()]
    elif isinstance(raw, str):
        items = [x.strip() for x in raw.splitlines() if x.strip()]
    else:
        items = []
    cleaned = []
    for it in items:
        it2 = re.sub(r"^\s*\d+\s*[\)\.\-:]\s*", "", it).strip()
        it2 = re.sub(r"^\s*[\-\•]\s*", "", it2).strip()
        if it2:
            cleaned.append(it2)
    return cleaned


def validate_18(items: List[str]) -> bool:
    if len(items) != 18:
        return False
    if sum(1 for x in items if _norm(x).startswith("condicional")) > 4:
        return False
    if sum(1 for x in items if len(x) < 25) > 3:
        return False
    return True


def likely_attorney_as_author(minuta: str) -> bool:
    t = _norm(minuta[:900])
    bad = ("[seu nome], advogado" in t and "vem" in t and "propor" in t)
    good = ("por seu advogado" in t or "por seu(sua) advogado(a)" in t)
    return bad and not good


def detect_trabalho_context(state: Dict[str, Any]) -> bool:
    blob = _norm(" ".join([
        state.get("area_subarea", ""),
        state.get("objetivo_cliente", ""),
        state.get("fatos_cronologia", ""),
        state.get("partes", ""),
        state.get("tipo_peca", ""),
    ]))
    flags = [
        "trabalho", "empregador", "empregado", "demit", "acidente de trabalho",
        "sem justa causa", "cat", "inss", "vara do trabalho", "indenizatoria"
    ]
    return any(f in blob for f in flags)


def validate_minuta(state: Dict[str, Any], minuta: str) -> List[str]:
    issues = []
    m = (minuta or "").strip()
    min_len = 1200 if _norm(state.get("tipo_peca", "")) == "notificacao extrajudicial" else 900
    if len(m) < min_len:
        issues.append("minuta_curta")
        return issues

    if not _norm(m).startswith("copie e cole no timbrado"):
        issues.append("minuta_sem_instrucao_timbrado")

    if likely_attorney_as_author(m):
        issues.append("minuta_advogado_como_autor")

    if detect_trabalho_context(state):
        t = _norm(m[:500])
        if "trabalho" not in t and "justica do trabalho" not in t and "vara do trabalho" not in t:
            issues.append("minuta_forum_inadequado_trabalho")

    return issues


def extract_text_from_pdf(raw: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(raw))
        parts = []
        for p in reader.pages[:10]:
            t = p.extract_text() or ""
            if t.strip():
                parts.append(t.strip())
        return "\n\n".join(parts)[:MAX_EXCERPT_CHARS]
    except Exception:
        return ""


def extract_text_from_docx(raw: bytes) -> str:
    try:
        d = Document(BytesIO(raw))
        txt = "\n".join([p.text for p in d.paragraphs if p.text.strip()])
        return txt[:MAX_EXCERPT_CHARS]
    except Exception:
        return ""


def extract_text_from_txt(raw: bytes) -> str:
    try:
        return raw.decode("utf-8", errors="ignore")[:MAX_EXCERPT_CHARS]
    except Exception:
        return ""


def extract_text_from_upload(filename: str, mime: str, raw: bytes) -> str:
    low = (filename or "").lower()
    mime = mime or ""
    if low.endswith(".pdf") or mime == "application/pdf":
        return extract_text_from_pdf(raw)
    if low.endswith(".docx") or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return extract_text_from_docx(raw)
    if low.endswith(".txt") or mime.startswith("text/"):
        return extract_text_from_txt(raw)
    return ""

# =========================================================
# AI PROMPTS
# =========================================================
OUTPUT_SCHEMA_PROMPT = r"""
RETORNE APENAS JSON.

REGRAS DURAS:
- Não inventar fatos, datas, valores ou nomes.
- Se faltar dado, use [HIP] ou [PREENCHER].
- "forca_tese" é avaliação técnica comparativa, nunca promessa de resultado.
- Estratégia: EXATAMENTE 18 itens.
- Cada item da estratégia deve ser uma STRING (não objeto), no formato:
  "AÇÃO — POR QUÊ — COMO/PROVA".
- No máximo 4 itens podem começar com "CONDICIONAL:".
- "secoes" deve ser um objeto com estas chaves:
  "1_CLASSIFICACAO_DO_CASO",
  "2_SINTESE",
  "3_QUESTAO_JURIDICA",
  "4_ANALISE_TECNICA",
  "5_FORCA_DA_TESE",
  "6_CONFIABILIDADE",
  "7_PROVAS",
  "8_RISCOS",
  "9_CENARIOS",
  "10_ANALISE_ECONOMICA",
  "11_RENTABILIDADE",
  "12_SCORES",
  "13_RED_TEAM",
  "14_ESTRATEGIA",
  "15_ACOES_PRIORITARIAS",
  "16_PENDENCIAS",
  "17_ALERTAS",
  "18_REFLEXAO_FINAL"

MINUTA:
- Sem markdown.
- Completa, utilizável e estruturada.
- Deve iniciar com: "Copie e cole no timbrado do seu escritório antes de finalizar."
- O autor/cliente NUNCA é o advogado.
- Se o contexto for trabalhista, usar linguagem compatível com Vara do Trabalho / Justiça do Trabalho.
- Se faltar nome da empresa/cidade/OAB, usar [PREENCHER] sem inventar.

SAÍDA:
{
  "forca_tese": "Muito forte|Forte|Moderada|Fraca|Muito fraca",
  "risco_improcedencia":"Baixo|Médio|Alto",
  "confiabilidade_analise":"Alta|Média|Baixa",
  "suficiencia_dados":"suficiente|parcialmente_suficiente|insuficiente",
  "status":"COMPLETA|ANÁLISE PRELIMINAR",
  "estrategia_18_pontos":["...", "..."],
  "secoes": {
    "1_CLASSIFICACAO_DO_CASO":"...",
    "2_SINTESE":"...",
    "3_QUESTAO_JURIDICA":"...",
    "4_ANALISE_TECNICA":"...",
    "5_FORCA_DA_TESE":"...",
    "6_CONFIABILIDADE":"...",
    "7_PROVAS":"...",
    "8_RISCOS":"...",
    "9_CENARIOS":"...",
    "10_ANALISE_ECONOMICA":"...",
    "11_RENTABILIDADE":"...",
    "12_SCORES":"...",
    "13_RED_TEAM":"...",
    "14_ESTRATEGIA":"...",
    "15_ACOES_PRIORITARIAS":"...",
    "16_PENDENCIAS":"...",
    "17_ALERTAS":"...",
    "18_REFLEXAO_FINAL":"..."
  },
  "minuta_peca":"..."
}
"""

REPAIR_PROMPT = r"""
Você vai REFAZER a saída.
Corrigir:
- estratégia com exatamente 18 STRINGS válidas
- minuta mais completa e utilizável
- sem inventar fatos/datas/valores
- sem colocar o advogado como autor
- se o contexto for trabalhista, explicitar adequação à Justiça do Trabalho
Retorne APENAS JSON.
"""

PIECE_ONLY_PROMPT = r"""
RETORNE APENAS JSON.

Você vai redigir APENAS a minuta jurídica pedida, sem relatório estratégico completo.

REGRAS DURAS:
- Não inventar fatos, datas, valores, nomes, tribunal, processo ou documentos.
- Se faltar dado, usar [PREENCHER] ou [HIP] quando estritamente necessário.
- A minuta deve ser completa, utilizável e estruturada.
- Deve iniciar exatamente com: "Copie e cole no timbrado do seu escritório antes de finalizar."
- O advogado nunca é o autor dos fatos; atua como representante.
- Se o caso for trabalhista, usar linguagem compatível com Justiça do Trabalho / Vara do Trabalho.
- Use a base normativa recebida quando pertinente, sem citar norma inexistente.
- Não use markdown.

SAÍDA:
{
  "minuta_peca": "..."
}
"""

def call_json(client: OpenAI, system: str, payload: Dict[str, Any], temperature: float = 0.15) -> Dict[str, Any]:
    r = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    return json.loads(r.choices[0].message.content)



def build_payload(state: Dict[str, Any]) -> Dict[str, Any]:
    sid = state.get("_session_id", "")
    uploads_short = [
        {
            "filename": f["filename"],
            "mime": f["mime"],
            "text_excerpt": (f.get("text_excerpt") or "")[:1800],
        }
        for f in UPLOADS.get(sid, [])
    ]
    norms = build_legal_basis(state, top_k=LEGAL_BASIS_TOP_K)
    return {
        "intake": state,
        "captured_view": captured_view(state),
        "uploads": uploads_short,
        "legal_basis": norms,
        "legal_basis_text": legal_basis_text(norms),
        "vademecum_status": VADEMECUM.status(),
    }


def build_fallback_minuta(state: Dict[str, Any], report: Dict[str, Any]) -> str:
    tipo = state.get("tipo_peca", "")
    cidade = state.get("cidade_uf") or "[CIDADE/UF]"
    contraria = state.get("parte_contraria_nome") or "[PARTE CONTRÁRIA / EMPRESA]"
    advogado = state.get("advogado_assinatura") or f"{MANDATARIA_NOME} — {MANDATARIA_OAB}"
    autor = state.get("contratante_nome", "[NOME DO CLIENTE]")
    fatos = state.get("fatos_cronologia", "[DESCREVER FATOS]")
    objetivo = state.get("objetivo_cliente", "[OBJETIVO]")
    provas = state.get("provas_existentes", "[PROVAS]")
    urg = state.get("urgencia_prazo", "[SEM URGÊNCIA INFORMADA]")

    if tipo == "Notificação Extrajudicial":
        return f"""Copie e cole no timbrado do seu escritório antes de finalizar.

NOTIFICAÇÃO EXTRAJUDICIAL

Notificante: {autor}
Notificada: {contraria}

Assunto: caso de natureza {state.get('area_subarea','[PREENCHER]')} com objetivo de {objetivo}.

Pela presente, o(a) notificante, por intermédio de seu advogado, vem formalizar NOTIFICAÇÃO EXTRAJUDICIAL em razão dos fatos a seguir resumidos.

1. Síntese dos fatos
Conforme informado, {fatos}.
Há indicação de urgência/prazo: {urg}.
As provas atualmente mencionadas são: {provas}.

2. Finalidade desta notificação
Esta notificação tem por finalidade:
a) registrar formalmente os fatos narrados;
b) solicitar a preservação de documentos, registros internos e eventuais imagens relacionadas ao caso;
c) viabilizar solução extrajudicial, quando possível;
d) evitar alegações futuras de desconhecimento dos fatos narrados.

3. Requerimentos preliminares
Solicita-se que a parte notificada:
a) preserve documentos, comunicações internas, controles, relatórios e gravações relacionados aos fatos;
b) se manifeste formalmente sobre os fatos narrados;
c) informe, se aplicável, dados contratuais, funcionais e/ou administrativos pertinentes;
d) apresente proposta de composição, caso haja interesse.

4. Observações jurídicas preliminares
A presente comunicação possui caráter preventivo e de organização probatória, sem importar em renúncia de direitos e sem prejuízo da adoção das medidas judiciais cabíveis.
Tratando-se de contexto potencialmente trabalhista/indenizatório, a avaliação jurídica final deverá observar a documentação completa, a dinâmica do evento, eventual nexo causal, extensão dos danos e demais circunstâncias relevantes.

5. Prazo
Concede-se o prazo de 5 (cinco) dias úteis para resposta formal, contados do recebimento desta notificação, salvo prazo diverso mais adequado ao caso concreto.

Sem mais, fica a presente encaminhada para os fins de direito.

{cidade}, [DATA].

____________________________________
{advogado}
"""

    return f"""Copie e cole no timbrado do seu escritório antes de finalizar.

MINUTA DE {tipo.upper()}

Cliente/Contratante: {autor}
Parte contrária: {contraria}
Cidade/UF: {cidade}

1. Síntese fática
{fatos}

2. Objetivo do cliente
{objetivo}

3. Provas indicadas
{provas}

4. Observações preliminares
A presente minuta foi estruturada de forma assistiva, com base nas informações fornecidas até o momento, devendo ser revisada antes de uso externo.

5. Encaminhamento
Ajustar os pedidos finais conforme estratégia validada e documentos disponíveis.

{cidade}, [DATA].

____________________________________
{advogado}
"""


def validate_report_json(state: Dict[str, Any], data: Dict[str, Any]) -> List[str]:
    issues = []
    for k in ["forca_tese", "risco_improcedencia", "confiabilidade_analise", "suficiencia_dados", "status"]:
        if is_placeholder(str(data.get(k, ""))):
            issues.append(f"{k}_placeholder")

    pts = normalize_points(data.get("estrategia_18_pontos"))
    if not validate_18(pts):
        issues.append("estrategia_18_invalida")
    data["estrategia_18_pontos"] = pts

    minuta = str(data.get("minuta_peca", "")).strip()
    if not _norm(minuta).startswith("copie e cole no timbrado"):
        minuta = "Copie e cole no timbrado do seu escritório antes de finalizar.\n\n" + minuta
    data["minuta_peca"] = minuta

    issues.extend(validate_minuta(state, minuta))
    return issues


def generate_report_strict(state: Dict[str, Any]) -> Dict[str, Any]:
    if not PROMPT_LOADED:
        raise HTTPException(status_code=500, detail="OS_6_1_PROMPT não carregado (Render env).")

    client = get_client()
    payload = build_payload(state)
    system = (OS_6_1_PROMPT + "\n\n" + OUTPUT_SCHEMA_PROMPT).strip()

    try:
        data = call_json(client, system, payload, temperature=TEMPERATURE)
        issues = validate_report_json(state, data)
        if not issues:
            data["_warnings"] = []
            return data

        data2 = call_json(
            client,
            REPAIR_PROMPT + "\n\n" + OUTPUT_SCHEMA_PROMPT,
            {**payload, "issues": issues, "previous": data},
            temperature=0.10,
        )
        issues2 = validate_report_json(state, data2)

        if issues2:
            data2["minuta_peca"] = build_fallback_minuta(state, data2)
            issues2 = [x for x in issues2 if not x.startswith("minuta_")]

        data2["_warnings"] = issues2
        return data2

    except HTTPException:
        raise
    except Exception as e:
        raise friendly_openai_error(e)

def generate_piece_only_strict(state: Dict[str, Any]) -> str:
    if not PROMPT_LOADED:
        raise HTTPException(status_code=500, detail="OS_6_1_PROMPT não carregado (Render env).")

    client = get_client()
    payload = build_payload(state)
    payload["requested_output"] = "piece_only"
    payload["instructions"] = "Gerar somente a minuta da peça pedida, sem relatório completo."
    system = (OS_6_1_PROMPT + "\n\n" + PIECE_ONLY_PROMPT).strip()

    try:
        data = call_json(client, system, payload, temperature=min(TEMPERATURE, 0.12))
        minuta = clean_text(str(data.get("minuta_peca") or ""))
        if not minuta:
            raise HTTPException(status_code=502, detail="Falha ao gerar a minuta da peça.")
        if not _norm(minuta).startswith("copie e cole no timbrado"):
            minuta = "Copie e cole no timbrado do seu escritório antes de finalizar.\n\n" + minuta
        issues = validate_minuta(state, minuta)
        if issues:
            return build_fallback_minuta(state, {"minuta_peca": minuta})
        return minuta
    except HTTPException:
        raise
    except Exception as e:
        raise friendly_openai_error(e)


def build_piece_preview_from_text(minuta: str) -> str:
    return preview_text(minuta)

# =========================================================
# PRICING
# =========================================================
def estimate_proof_count(state: Dict[str, Any]) -> int:
    txt = state.get("provas_existentes", "") or ""
    if not txt.strip():
        base = 0
    else:
        items = [x.strip() for x in re.split(r"[,\n;]+", txt) if x.strip()]
        base = len(items)
    uploads = len(UPLOADS.get(state.get("_session_id", ""), []))
    return base + uploads


def complexity_score(state: Dict[str, Any], report: Optional[Dict[str, Any]] = None) -> int:
    score = 0
    fase = _norm(state.get("fase", ""))
    tipo = _norm(state.get("tipo_peca", ""))
    valor = parse_money_br(state.get("valor_envolvido") or state.get("valor_envovido") or "")
    proofs = estimate_proof_count(state)
    partes = _norm(state.get("partes", ""))
    notas = _norm(state.get("notas_adicionais", ""))

    if fase in {"processo", "recurso", "execucao", "execução"}:
        score += 2
    elif fase in {"pre-contencioso", "pré-contencioso"}:
        score += 1

    if tipo in {"recurso", "contestacao", "contestação"}:
        score += 2
    elif tipo in {"peticao inicial", "petição inicial"}:
        score += 1

    if detect_trabalho_context(state):
        score += 1

    if valor >= 50000:
        score += 2
    elif valor >= 20000:
        score += 1

    if proofs >= 4:
        score += 1

    if (" e " in partes) or ("," in partes):
        score += 1

    if notas and notas not in {"nao", "não", "nao sei", "não sei"}:
        score += 1

    if report:
        risk = _norm(report.get("risco_improcedencia", ""))
        suf = _norm(report.get("suficiencia_dados", ""))
        if risk == "alto":
            score += 1
        if "insuficiente" in suf or "parcialmente" in suf:
            score += 1

    return score


def pricing_engine(state: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    tipo = state.get("tipo_peca", "")
    fase = _norm(state.get("fase", ""))
    valor = parse_money_br(state.get("valor_envolvido") or state.get("valor_envovido") or "")
    proofs = estimate_proof_count(state)
    work_case = detect_trabalho_context(state)
    comp = complexity_score(state, report)

    base_map = {
        "Notificação Extrajudicial": 1800,
        "Petição Inicial": 4200,
        "Contestação": 3600,
        "Réplica": 2500,
        "Recurso": 5200,
        "Minuta de Acordo": 2200,
        "Petição Intermediária (Manifestação)": 2600,
    }
    base = base_map.get(tipo, 2500)

    mult = 1.0
    phase_map = {
        "consultivo": 1.00,
        "pre-contencioso": 1.08,
        "pré-contencioso": 1.08,
        "processo": 1.18,
        "recurso": 1.28,
        "execucao": 1.24,
        "execução": 1.24,
    }
    mult *= phase_map.get(fase, 1.0)

    if work_case:
        mult += 0.12

    urg = _norm(state.get("urgencia_prazo", ""))
    if any(x in urg for x in ["urg", "critico", "crítico", "48h", "24h", "prazo"]):
        mult += 0.15

    if valor >= 200000:
        mult += 0.28
    elif valor >= 100000:
        mult += 0.22
    elif valor >= 50000:
        mult += 0.15
    elif valor >= 30000:
        mult += 0.10
    elif valor >= 10000:
        mult += 0.05

    if proofs == 0:
        mult += 0.10
    elif proofs <= 2:
        mult += 0.03
    else:
        mult += 0.08

    mult += min(comp * 0.04, 0.24)

    total = round_up_100(base * mult)
    total = max(FEE_MIN_TOTAL, min(FEE_MAX_TOTAL, total))

    entrada_pct = 0.35
    if fase in {"recurso", "execucao", "execução"}:
        entrada_pct = 0.40
    elif total <= 3000:
        entrada_pct = 0.30

    entrada = round_up_100(total * entrada_pct)
    if entrada >= total:
        entrada = max(500, total // 2)

    saldo = max(0, total - entrada)
    if total <= 2500:
        parcelas = 1
    elif total <= 4500:
        parcelas = 3
    elif total <= 8000:
        parcelas = 6
    elif total <= 15000:
        parcelas = 8
    else:
        parcelas = 10

    fatores = []
    fatores.append(f"tipo de peça: {tipo or '—'}")
    fatores.append(f"fase: {state.get('fase', '—')}")
    if valor > 0:
        fatores.append(f"valor/impacto informado: {fmt_brl(int(valor))}")
    if work_case:
        fatores.append("contexto trabalhista/indenizatório")
    if proofs > 0:
        fatores.append(f"{proofs} evidências/documentos considerados")
    if urg:
        fatores.append("urgência/prazo crítico")
    fatores.append(f"complexidade estimada: {comp}/8")

    justificativa = (
        "Honorários sugeridos com base em: "
        + "; ".join(fatores)
        + ". Valor calibrado por complexidade, risco operacional, urgência, "
          "carga de análise documental e potencial econômico do caso, sem promessa de êxito."
    )

    return {
        "total": total,
        "entrada": entrada,
        "parcelas": parcelas,
        "justificativa_curta": justificativa[:1400],
        "complexidade_score": comp,
    }

# =========================================================
# DOCX BUILDERS
# =========================================================
def add_h(doc: Document, text: str, size=14):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(size)
    p.space_after = Pt(6)


def add_p(doc: Document, text: str):
    doc.add_paragraph(text)


def add_list_numbered(doc: Document, items: List[str]):
    for it in items:
        doc.add_paragraph(str(it), style="List Number")


def report_mode(state: Dict[str, Any], report: Dict[str, Any]) -> str:
    return "COMPLETO" if complexity_score(state, report) >= 3 else "RESUMIDO"


def build_report_strategy_docx(report: Dict[str, Any], state: Dict[str, Any]) -> Document:
    doc = Document()
    title = doc.add_paragraph("RELATÓRIO — DIAGNÓSTICO JURÍDICO INTELIGENTE (S&M OS 6.1)")
    title.runs[0].bold = True
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    modo = report_mode(state, report)

    doc.add_paragraph("")
    add_p(doc, f"Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    add_p(doc, f"Modo do relatório: {modo}")
    add_p(doc, f"Área/Subárea: {state.get('area_subarea','—')}")
    add_p(doc, f"Fase: {state.get('fase','—')}")
    add_p(doc, f"Partes: {state.get('partes','—')}")
    add_p(doc, f"Contratante/Recebedor: {state.get('contratante_nome','—')}")
    add_p(doc, f"Tipo de peça: {state.get('tipo_peca','—')}")
    add_p(doc, f"Valor/Impacto: {state.get('valor_envolvido') or state.get('valor_envovido') or '—'}")

    doc.add_paragraph("")
    add_h(doc, "Classificações técnicas", 13)
    add_p(doc, f"Força da tese: {report.get('forca_tese','—')}")
    add_p(doc, f"Confiabilidade da análise: {report.get('confiabilidade_analise','—')}")
    add_p(doc, f"Risco de improcedência: {report.get('risco_improcedencia','—')}")
    add_p(doc, f"Suficiência de dados: {report.get('suficiencia_dados','—')}")
    add_p(doc, f"Status: {report.get('status','—')}")

    secoes = report.get("secoes") or {}

    doc.add_paragraph("")
    add_h(doc, "Estratégia (18 pontos)", 13)
    add_list_numbered(doc, report.get("estrategia_18_pontos", []))

    doc.add_paragraph("")
    if modo == "RESUMIDO":
        add_h(doc, "Síntese executiva", 13)
        add_p(doc, str(secoes.get("2_SINTESE", "—")))
        add_h(doc, "Questão jurídica", 13)
        add_p(doc, str(secoes.get("3_QUESTAO_JURIDICA", "—")))
        add_h(doc, "Provas e riscos", 13)
        add_p(doc, "Provas: " + str(secoes.get("7_PROVAS", "—")))
        add_p(doc, "Riscos: " + str(secoes.get("8_RISCOS", "—")))
        add_h(doc, "Ações prioritárias", 13)
        add_p(doc, str(secoes.get("15_ACOES_PRIORITARIAS", "—")))
        add_h(doc, "Pendências", 13)
        add_p(doc, str(secoes.get("16_PENDENCIAS", "—")))
    else:
        add_h(doc, "Seções OS 6.1 (completo)", 13)
        ordered_keys = [
            "1_CLASSIFICACAO_DO_CASO",
            "2_SINTESE",
            "3_QUESTAO_JURIDICA",
            "4_ANALISE_TECNICA",
            "5_FORCA_DA_TESE",
            "6_CONFIABILIDADE",
            "7_PROVAS",
            "8_RISCOS",
            "9_CENARIOS",
            "10_ANALISE_ECONOMICA",
            "11_RENTABILIDADE",
            "12_SCORES",
            "13_RED_TEAM",
            "14_ESTRATEGIA",
            "15_ACOES_PRIORITARIAS",
            "16_PENDENCIAS",
            "17_ALERTAS",
            "18_REFLEXAO_FINAL",
        ]
        for k in ordered_keys:
            add_h(doc, k.replace("_", " "), 12)
            add_p(doc, str(secoes.get(k, "—")))

    warnings = report.get("_warnings") or []
    if warnings:
        doc.add_paragraph("")
        add_h(doc, "Avisos de validação (backend)", 12)
        add_p(doc, "A saída foi aproveitada, mas com alertas automáticos que recomendam revisão humana reforçada:")
        for w in warnings[:20]:
            add_p(doc, f"- {w}")

    doc.add_paragraph("")
    add_h(doc, "Nota de compliance", 12)
    add_p(doc, "Saída assistiva. Revisão humana obrigatória em decisões críticas. Sem promessa de êxito.")
    return doc


def build_proposal_docx(state: Dict[str, Any], fee: Dict[str, Any]) -> Document:
    doc = Document()
    p = doc.add_paragraph("ORÇAMENTO / PROPOSTA DE HONORÁRIOS")
    p.runs[0].bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph("")

    contratante = state.get("contratante_nome") or "(PREENCHER)"
    total = int(fee["total"])
    entrada = int(fee["entrada"])
    parcelas = int(fee["parcelas"])
    saldo = max(0, total - entrada)
    parcela_val = int(saldo / max(parcelas, 1)) if parcelas > 0 else saldo

    t1 = doc.add_table(rows=6, cols=2)
    t1.style = "Table Grid"
    t1.cell(0, 0).text = "Contratante / Recebedor"
    t1.cell(0, 1).text = str(contratante)
    t1.cell(1, 0).text = "Mandatário(a)"
    t1.cell(1, 1).text = f"{MANDATARIA_NOME} — {MANDATARIA_OAB}"
    t1.cell(2, 0).text = "Objeto"
    t1.cell(2, 1).text = f"Atuação no caso informado (Área: {state.get('area_subarea','—')} · Peça: {state.get('tipo_peca','—')})."
    t1.cell(3, 0).text = "Data"
    t1.cell(3, 1).text = datetime.now().strftime("%d/%m/%Y")
    t1.cell(4, 0).text = "Validade da proposta"
    t1.cell(4, 1).text = f"{FEE_VALIDITY_DAYS} dias"
    t1.cell(5, 0).text = "Observação"
    t1.cell(5, 1).text = "Obrigação de meio. Sem promessa de êxito."

    doc.add_paragraph("")
    add_h(doc, "Honorários sugeridos por caso", 13)
    t2 = doc.add_table(rows=4, cols=2)
    t2.style = "Table Grid"
    t2.cell(0, 0).text = "Entrada (no ato)"
    t2.cell(0, 1).text = fmt_brl(entrada)
    t2.cell(1, 0).text = "Saldo"
    t2.cell(1, 1).text = fmt_brl(saldo)
    t2.cell(2, 0).text = f"Parcelamento ({parcelas}x)"
    t2.cell(2, 1).text = f"{parcelas} parcelas de {fmt_brl(parcela_val)}"
    t2.cell(3, 0).text = "Total"
    t2.cell(3, 1).text = fmt_brl(total)

    doc.add_paragraph("")
    add_h(doc, "Critérios considerados", 13)
    add_p(doc, fee.get("justificativa_curta", "—") or "—")

    doc.add_paragraph("")
    add_h(doc, "Orientação", 13)
    add_p(doc, "Copie e cole esta proposta no timbrado do seu escritório antes de enviar ao cliente.")
    return doc


def build_piece_docx(report: Dict[str, Any], state: Dict[str, Any]) -> Document:
    doc = Document()
    tipo = state.get("tipo_peca", "Peça")

    p = doc.add_paragraph(f"MINUTA — {tipo.upper()} (S&M OS 6.1)")
    p.runs[0].bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph("")
    warn = doc.add_paragraph("IMPORTANTE: Copie e cole no timbrado do seu escritório antes de finalizar. Revise dados, anexos e competência.")
    warn.runs[0].bold = True

    doc.add_paragraph("")
    add_h(doc, "Minuta", 13)
    doc.add_paragraph(str(report.get("minuta_peca", "—")))

    doc.add_paragraph("")
    foot = doc.add_paragraph("Nota: minuta assistiva. Ajuste [PREENCHER] antes de assinar/protocolar.")
    foot.runs[0].italic = True
    return doc

# =========================================================
# API MODELS
# =========================================================
class SessionOut(BaseModel):
    session_id: str
    message: str
    state: Dict[str, Any]
    expected_field: Optional[str] = None
    captured: Optional[Dict[str, Any]] = None


class ChatIn(BaseModel):
    session_id: str
    message: str
    state: Dict[str, Any] = {}


class ChatOut(BaseModel):
    message: str
    state: Dict[str, Any]
    expected_field: Optional[str] = None
    captured: Optional[Dict[str, Any]] = None
    report_docx_b64: Optional[str] = None
    report_docx_filename: Optional[str] = None
    report_preview: Optional[str] = None
    proposal_docx_b64: Optional[str] = None
    proposal_docx_filename: Optional[str] = None
    proposal_preview: Optional[str] = None
    piece_docx_b64: Optional[str] = None
    piece_docx_filename: Optional[str] = None
    piece_preview: Optional[str] = None


class DataJudSearchRequest(BaseModel):
    alias: Optional[str] = None
    numero_processo: Optional[str] = None
    classe_nome: Optional[str] = None
    assunto_nome: Optional[str] = None
    tribunal: Optional[str] = None
    orgao_julgador: Optional[str] = None
    size: int = 10
    cursor: Optional[str] = None


class UploadIn(BaseModel):
    session_id: str
    filename: str
    mime: str
    b64: str


class UploadOut(BaseModel):
    file_id: str
    filename: str
    size_bytes: int
    text_extracted: bool

# =========================================================
# ROUTES
# =========================================================
@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "sm-os-demo",
        "version": "4.1.0",
        "has_openai_key": bool(OPENAI_API_KEY),
        "allowed_origin": ALLOWED_ORIGIN,
        "model": MODEL,
        "prompt_loaded": PROMPT_LOADED,
        "mandataria_default": f"{MANDATARIA_NOME} — {MANDATARIA_OAB}",
        "sessions": len(SESSIONS),
        "vademecum": VADEMECUM.status(),
    }


@app.get("/healt")
def healt():
    return health()


@app.get("/ping")
def ping():
    return {"ok": True, "service": "alive"}


@app.get("/datajud/test-process")
def datajud_test_process(numero: str, alias: Optional[str] = None):
    try:
        final_alias = alias or infer_datajud_alias_from_numero(numero) or DATAJUD.default_alias
        raw = DATAJUD.search_process_by_number(numero=numero, alias=final_alias)
        items = DATAJUD.extract_sources(raw)

        if not items:
            return {
                "ok": True,
                "found": False,
                "alias_used": final_alias,
                "message": "Nenhum processo encontrado."
            }

        proc = DATAJUD.normalize_process(items[0])
        return {
            "ok": True,
            "found": True,
            "alias_used": final_alias,
            "process": proc
        }
    except DataJudError as e:
        return {"ok": False, "error": str(e)}


@app.post("/datajud/search")
def datajud_search(req: DataJudSearchRequest, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    try:
        final_alias = req.alias or infer_datajud_alias_from_numero(req.numero_processo or "") or DATAJUD.default_alias
        query = build_datajud_query(req)
        result = DATAJUD.search_paginated(
            alias=final_alias,
            query=query,
            size=max(1, min(req.size, 50)),
            cursor=req.cursor,
            sort=[{DATAJUD.default_sort_field: "desc"}],
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

        items: List[Dict[str, Any]] = []
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
            "alias_used": final_alias,
            "total": result["total"],
            "count": result["count"],
            "items": items,
            "next_cursor": result["next_cursor"],
            "timed_out": result["timed_out"],
            "raw_took_ms": result["raw_took_ms"],
        }
    except DataJudError as e:
        return {"ok": False, "error": str(e)}


@app.post("/session/new", response_model=SessionOut)
def session_new(x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = str(uuid.uuid4())
    UPLOADS[sid] = []
    state = get_session_state(sid)

    msg = ""

    return SessionOut(
        session_id=sid,
        message=msg,
        state=state,
        expected_field=None,
        captured=captured_view(state),
    )


@app.post("/upload", response_model=UploadOut)
def upload_file(inp: UploadIn, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = inp.session_id
    state = get_session_state(sid)
    UPLOADS.setdefault(sid, [])
    files = UPLOADS[sid]

    if len(files) >= MAX_FILES_PER_SESSION:
        raise HTTPException(status_code=400, detail="Limite de arquivos por sessão atingido.")

    raw = base64.b64decode(inp.b64)
    size = len(raw)

    if size > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Arquivo muito grande. Máximo {MAX_FILE_MB}MB.")

    total = sum(f.get("size_bytes", 0) for f in files) + size
    if total > MAX_TOTAL_MB_PER_SESSION * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Limite total por sessão: {MAX_TOTAL_MB_PER_SESSION}MB.")

    file_id = str(uuid.uuid4())
    text_excerpt = extract_text_from_upload(inp.filename, inp.mime, raw)

    files.append(
        {
            "file_id": file_id,
            "filename": inp.filename,
            "mime": inp.mime,
            "size_bytes": size,
            "text_excerpt": text_excerpt[:MAX_EXCERPT_CHARS],
        }
    )

    return UploadOut(
        file_id=file_id,
        filename=inp.filename,
        size_bytes=size,
        text_extracted=bool(text_excerpt.strip())
    )


@app.post("/chat", response_model=ChatOut)
def chat(inp: ChatIn, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)

    sid = (inp.session_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session_id is required.")

    state = get_session_state(sid)
    merge_incoming_state(state, inp.state or {})
    state["_session_id"] = sid

    msg = clean_text(inp.message or "")
    if not msg:
        followup = build_conversational_followup(state)
        return ChatOut(
            message=followup,
            state=state,
            expected_field=state_expected_field(state),
            captured=captured_view(state),
        )

    try:
        if looks_like_process_query(msg):
            match = PROCESS_NUMBER_RE.search(msg or "")
            if match:
                numero = match.group(0)
                try:
                    alias = infer_datajud_alias_from_message(msg) or infer_datajud_alias_from_numero(numero) or DATAJUD.default_alias
                    raw = DATAJUD.search_process_by_number(numero=numero, alias=alias)
                    items = DATAJUD.extract_sources(raw)
                    if not items:
                        return ChatOut(
                            message=f"Não encontrei resultado no DataJud para o processo {numero}.",
                            state=state,
                            expected_field=None,
                            captured=captured_view(state),
                        )

                    proc = DATAJUD.normalize_process(items[0])
                    movimentos_txt = []
                    for mov in proc["movimentos"][:5]:
                        nome = mov.get("nome", "Movimentação")
                        data = mov.get("dataHora", "")
                        movimentos_txt.append(f"- {data} | {nome}")

                    info = [
                        f"Processo: {proc.get('numero_processo')}",
                        f"Tribunal: {proc.get('tribunal')}",
                        f"Grau: {proc.get('grau')}",
                        f"Classe: {proc.get('classe_nome')}",
                        f"Órgão julgador: {proc.get('orgao_julgador')}",
                    ]
                    assuntos = proc.get("assuntos") or []
                    if assuntos:
                        info.append(f"Assuntos: {', '.join(assuntos)}")
                    if proc.get("data_ajuizamento"):
                        info.append(f"Data de ajuizamento: {proc.get('data_ajuizamento')}")
                    if proc.get("ultima_atualizacao"):
                        info.append(f"Última atualização: {proc.get('ultima_atualizacao')}")
                    info.append(f"Movimentos capturados: {proc.get('movimentos_total')}")
                    if movimentos_txt:
                        info.append("")
                        info.append("Últimas movimentações:")
                        info.extend(movimentos_txt)

                    return ChatOut(
                        message="\n".join(info),
                        state=state,
                        expected_field=None,
                        captured=captured_view(state),
                    )
                except DataJudError as e:
                    return ChatOut(
                        message=f"Não consegui consultar o DataJud agora. Motivo: {str(e)}",
                        state=state,
                        expected_field=None,
                        captured=captured_view(state),
                    )

        if should_cancel_pending(msg):
            clear_pending_outputs(state)
            set_expected_field(state, None)
            return ChatOut(
                message="Perfeito. Não vou gerar nada agora. Pode continuar me passando o contexto ou, quando quiser, pedir o documento específico.",
                state=state,
                expected_field=None,
                captured=captured_view(state),
            )

        consumed_expected = apply_expected_field_answer(state, msg)

        parsed = parse_labeled_answer(msg)
        if parsed:
            key, val = parsed
            set_value(state, key, val)
            set_expected_field(state, None)
        elif not consumed_expected:
            heur = quick_extract_from_short_reply(msg, state)
            if heur:
                merge_conversational_update(state, heur)

            explicit_piece = detect_tipo_peca_in_text(msg)
            if explicit_piece:
                state["tipo_peca"] = explicit_piece

            should_call_extractor = True
            norm_msg = _norm(msg)
            if len(msg) <= 4 and norm_msg in {"ok", "sim", "não", "nao"}:
                should_call_extractor = False

            if should_call_extractor:
                client = get_client()
                extracted = extract_fields_from_free_text(client, state, msg)
                merge_conversational_update(state, extracted)

                if explicit_piece and not is_answered(state.get("tipo_peca")):
                    state["tipo_peca"] = explicit_piece

        requested_outputs = detect_requested_outputs(msg, state)
        if requested_outputs:
            set_pending_outputs(state, requested_outputs)

        pending = pending_outputs(state)

        if pending and len(required_missing_for_outputs(state, pending)) == 0:
            need_report = "report" in pending
            need_piece = "piece" in pending
            need_proposal = "proposal" in pending

            report = None
            fee = None
            piece_text = None

            if need_report:
                report = generate_report_strict(state)
            elif need_piece:
                piece_text = generate_piece_only_strict(state)

            if need_proposal:
                fee = pricing_engine(state, report or {})

            ts = datetime.now().strftime("%Y%m%d-%H%M")
            tipo_safe = (state.get("tipo_peca", "Peca")).replace(" ", "_").replace("/", "_")

            out_kwargs: Dict[str, Any] = {
                "message": build_generation_success_message(state, pending),
                "state": state,
                "expected_field": None,
                "captured": captured_view(state),
            }

            if need_report and report is not None:
                doc_report = build_report_strategy_docx(report, state)
                out_kwargs["report_docx_b64"] = docx_to_b64(doc_report)
                out_kwargs["report_docx_filename"] = f"Relatorio_SM_OS_{ts}.docx"
                out_kwargs["report_preview"] = build_report_preview(report)

            if need_proposal and fee is not None:
                doc_prop = build_proposal_docx(state, fee)
                out_kwargs["proposal_docx_b64"] = docx_to_b64(doc_prop)
                out_kwargs["proposal_docx_filename"] = f"Proposta_Honorarios_SM_{ts}.docx"
                out_kwargs["proposal_preview"] = build_proposal_preview(state, fee)

            if need_piece:
                if report is not None:
                    doc_piece = build_piece_docx(report, state)
                    out_kwargs["piece_preview"] = build_piece_preview(report)
                else:
                    piece_payload = {"minuta_peca": piece_text or build_fallback_minuta(state, {})}
                    doc_piece = build_piece_docx(piece_payload, state)
                    out_kwargs["piece_preview"] = build_piece_preview_from_text(piece_payload["minuta_peca"])
                out_kwargs["piece_docx_b64"] = docx_to_b64(doc_piece)
                out_kwargs["piece_docx_filename"] = f"Minuta_{tipo_safe}_{ts}.docx"

            clear_pending_outputs(state)
            set_expected_field(state, None)
            return ChatOut(**out_kwargs)

        if pending:
            message = build_generation_blocked_message(state, pending)
            return ChatOut(
                message=message,
                state=state,
                expected_field=state_expected_field(state),
                captured=captured_view(state),
            )

        followup = build_conversational_followup(state)
        return ChatOut(
            message=followup,
            state=state,
            expected_field=state_expected_field(state),
            captured=captured_view(state),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise friendly_backend_error(e)



@app.get("/widget", response_class=HTMLResponse)
def widget():
    return HTMLResponse("<h3>OK</h3><p>Use o frontend no WordPress/Elementor.</p>")
