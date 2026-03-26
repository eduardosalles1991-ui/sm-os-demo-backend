# -*- coding: utf-8 -*-
import os
import uuid
import json
import base64
import re
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

app = FastAPI(title="S&M OS 6.1 — Demo Backend", version="3.0.0")
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


def friendly_openai_error(e: Exception) -> HTTPException:
    if isinstance(e, openai.RateLimitError):
        return HTTPException(status_code=429, detail="Rate limit/quota. Verifique Billing/Créditos.")
    if isinstance(e, openai.AuthenticationError):
        return HTTPException(status_code=401, detail="OPENAI_API_KEY inválida.")
    return HTTPException(status_code=500, detail=f"OpenAI error: {type(e).__name__}: {str(e)}")


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s+", " ", s)
    return s


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def is_answered(v: Any) -> bool:
    return bool(str(v or "").strip())


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


def build_short_case_summary(state: Dict[str, Any]) -> str:
    parts = []
    if is_answered(state.get("area_subarea")):
        parts.append(f"na área {state.get('area_subarea')}")
    if is_answered(state.get("objetivo_cliente")):
        parts.append(f"com objetivo de {state.get('objetivo_cliente')}")
    if is_answered(state.get("tipo_peca")):
        parts.append(f"e foco inicial em {state.get('tipo_peca')}")
    if is_answered(state.get("fatos_cronologia")):
        facts = clean_text(str(state.get("fatos_cronologia")))
        if len(facts) > 160:
            facts = facts[:157] + "..."
        parts.append(f"fatos centrais: {facts}")
    if not parts:
        return "Até aqui eu entendi o núcleo do caso, mas ainda preciso estruturar melhor os dados."
    return "Até aqui eu entendi: " + "; ".join(parts) + "."


def build_conversational_followup(state: Dict[str, Any]) -> str:
    summary = build_short_case_summary(state)
    critical_missing = conversational_missing(state, CRITICAL_FIELDS)
    secondary_missing = conversational_missing(state, SECONDARY_FIELDS)

    if critical_missing:
        if "tipo_peca" in critical_missing:
            return (
                summary
                + "\n\nPara eu te devolver algo realmente útil, me diga qual peça você quer gerar primeiro: "
                "notificação extrajudicial, petição inicial, contestação, réplica, recurso, acordo ou manifestação."
            )

        ask_map = {
            "area_subarea": "qual é a área/subárea jurídica mais adequada",
            "objetivo_cliente": "o que exatamente o cliente quer obter",
            "partes": "quem são as partes e a relação entre elas",
            "fatos_cronologia": "os fatos em ordem cronológica",
            "provas_existentes": "quais provas ou documentos já existem",
        }
        asks = [ask_map[k] for k in critical_missing[:3] if k in ask_map]
        if len(asks) == 1:
            return summary + f"\n\nAgora preciso confirmar {asks[0]}."
        if len(asks) == 2:
            return summary + f"\n\nPara fechar melhor a análise, preciso confirmar {asks[0]} e {asks[1]}."
        return summary + f"\n\nPara eu estruturar isso direito, preciso confirmar {asks[0]}, {asks[1]} e {asks[2]}."

    if secondary_missing:
        pretty = {
            "fase": "em que fase isso está",
            "contratante_nome": "o nome completo do contratante/recebedor",
            "urgencia_prazo": "se há urgência ou prazo crítico",
            "valor_envovido": "o valor envolvido ou estimado",
            "notas_adicionais": "qualquer detalhe adicional relevante",
            "parte_contraria_nome": "o nome da empresa ou parte contrária",
            "cidade_uf": "a cidade/UF do caso",
            "ano_fato": "o ano do fato",
            "advogado_assinatura": "o nome/OAB do advogado(a), se quiser preencher agora",
        }
        items = [pretty[k] for k in secondary_missing[:3] if k in pretty]
        if len(items) == 1:
            return summary + f"\n\nÓtimo. Antes de gerar, só preciso confirmar {items[0]}."
        if len(items) == 2:
            return summary + f"\n\nPerfeito. Antes de gerar, me confirma {items[0]} e {items[1]}."
        return summary + f"\n\nEstamos quase lá. Só preciso confirmar {items[0]}, {items[1]} e {items[2]}."

    return summary + "\n\nPerfeito. Já tenho base suficiente para gerar os documentos."


def extract_fields_from_free_text(client: OpenAI, state: Dict[str, Any], message: str) -> Dict[str, Any]:
    extraction_prompt = """
Você vai extrair informações jurídicas de uma mensagem livre do usuário.
Retorne APENAS JSON.

Regras:
- Extraia somente o que estiver explícito ou fortemente implícito.
- Não invente nomes, datas, cidades ou valores.
- Se o usuário não falou algo, deixe null.
- Se o usuário mencionar uma peça, normalize para um destes valores exatos:
  "Notificação Extrajudicial",
  "Petição Inicial",
  "Contestação",
  "Réplica",
  "Recurso",
  "Minuta de Acordo",
  "Petição Intermediária (Manifestação)"

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
    cleaned = {}

    for k in CONVERSATIONAL_FIELDS:
        v = data.get(k)
        if v is None:
            continue
        txt = clean_text(str(v))
        if not txt or txt.lower() == "null":
            continue

        if k == "tipo_peca":
            mapped = map_tipo_peca(txt) or txt
            if mapped in TIPOS_PECA:
                cleaned[k] = mapped
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

        current = state.get(k)
        if not is_answered(current):
            state[k] = v
        else:
            if k in {"fatos_cronologia", "provas_existentes", "notas_adicionais"}:
                cur = clean_text(str(current))
                nv = clean_text(str(v))
                if nv and nv not in cur:
                    state[k] = f"{cur} | {nv}"


def should_generate_now(state: Dict[str, Any], message: str) -> bool:
    crit_missing = conversational_missing(state, CRITICAL_FIELDS)
    if crit_missing:
        return False

    m = _norm(message)
    if any(x in m for x in [
        "gera", "pode gerar", "pode seguir", "pronto", "isso mesmo",
        "e isso", "é isso", "ja pode gerar", "já pode gerar"
    ]):
        return True

    secondary_missing = conversational_missing(
        state,
        ["fase", "contratante_nome", "urgencia_prazo", "valor_envovido"]
    )
    return len(secondary_missing) <= 1

# =========================================================
# VALIDAÇÕES / CONTEXTO
# =========================================================
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
    return {
        "intake": state,
        "captured_view": captured_view(state),
        "uploads": uploads_short,
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
    proposal_docx_b64: Optional[str] = None
    proposal_docx_filename: Optional[str] = None
    piece_docx_b64: Optional[str] = None
    piece_docx_filename: Optional[str] = None


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
        "version": "3.0.0",
        "has_openai_key": bool(OPENAI_API_KEY),
        "allowed_origin": ALLOWED_ORIGIN,
        "model": MODEL,
        "prompt_loaded": PROMPT_LOADED,
        "mandataria_default": f"{MANDATARIA_NOME} — {MANDATARIA_OAB}",
        "sessions": len(SESSIONS),
    }


@app.get("/healt")
def healt():
    return health()


@app.post("/session/new", response_model=SessionOut)
def session_new(x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = str(uuid.uuid4())
    UPLOADS[sid] = []
    state = get_session_state(sid)

    msg = (
        "Olá. Pode me contar o caso do seu jeito.\n\n"
        "Exemplo: 'Sofri uma queda no trabalho, fui demitido depois, tenho vídeo e atestado "
        "e quero uma indenização com notificação extrajudicial.'"
    )

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
        return ChatOut(
            message="Pode me contar o caso com liberdade. Eu vou organizar as informações e só perguntar o que faltar.",
            state=state,
            expected_field=None,
            captured=captured_view(state),
        )

    try:
        client = get_client()

        parsed = parse_labeled_answer(msg)
        if parsed:
            key, val = parsed
            set_value(state, key, val)
        else:
            mapped_piece = map_tipo_peca(msg)
            if mapped_piece and not is_answered(state.get("tipo_peca")):
                state["tipo_peca"] = mapped_piece
            else:
                extracted = extract_fields_from_free_text(client, state, msg)
                merge_conversational_update(state, extracted)

        if should_generate_now(state, msg):
            report = generate_report_strict(state)
            fee = pricing_engine(state, report)

            doc_report = build_report_strategy_docx(report, state)
            doc_prop = build_proposal_docx(state, fee)
            doc_piece = build_piece_docx(report, state)

            ts = datetime.now().strftime("%Y%m%d-%H%M")
            tipo_safe = (state.get("tipo_peca", "Peca")).replace(" ", "_").replace("/", "_")

            return ChatOut(
                message="✅ Perfeito. Estruturei o caso e gerei os 3 DOCX: relatório, proposta e minuta da peça.",
                state=state,
                expected_field=None,
                captured=captured_view(state),
                report_docx_b64=docx_to_b64(doc_report),
                report_docx_filename=f"Relatorio_SM_OS_{ts}.docx",
                proposal_docx_b64=docx_to_b64(doc_prop),
                proposal_docx_filename=f"Proposta_Honorarios_SM_{ts}.docx",
                piece_docx_b64=docx_to_b64(doc_piece),
                piece_docx_filename=f"Minuta_{tipo_safe}_{ts}.docx",
            )

        followup = build_conversational_followup(state)
        return ChatOut(
            message=followup,
            state=state,
            expected_field=None,
            captured=captured_view(state),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise friendly_openai_error(e)


@app.get("/widget", response_class=HTMLResponse)
def widget():
    return HTMLResponse("<h3>OK</h3><p>Use o frontend no WordPress/Elementor.</p>")
