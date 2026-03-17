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

from fastapi import FastAPI, Header, HTTPException, Request, UploadFile, File, Form
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
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))

# Placeholders (como pediste)
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

# Canonical keys (corrigido: valor_envolvido)
FIELDS_ORDER: List[Tuple[str, str]] = [
    ("area_subarea", "Qual a área/subárea? (ex.: cível/consumidor/indenizatória)"),
    ("fase", "Qual a fase? (consultivo / pré-contencioso / processo / recurso / execução)"),
    ("objetivo_cliente", "Qual o objetivo do cliente? (o que ele quer obter)"),
    ("partes", "Quem são as partes? (autor/réu e relação entre eles)"),
    ("contratante_nome", "Qual o nome completo do Contratante/Recebedor para a Proposta de Honorários?"),
    ("tipo_peca", "Qual peça você precisa gerar? (digite o número ou o nome)"),
    ("fatos_cronologia", "Conte os fatos em ordem (inclua: demissão/afastamento/CAT/INSS se houver)."),
    ("provas_existentes", "Quais provas/documentos você já tem? (liste) — Você também pode subir arquivos agora."),
    ("urgencia_prazo", "Há urgência ou prazo crítico? (qual?)"),
    ("valor_envolvido", "Qual o valor envolvido/impacto? (se não souber, estimativa)"),
    ("notas_adicionais", "Alguma informação adicional relevante? (detalhes que não cabiam antes)"),
]
REQUIRED_FIELDS = [k for k, _ in FIELDS_ORDER]

# Compat (si tu frontend antiguo usa el typo)
ALIASES = {
    "valor_envolvido": ["valor_envovido", "valor_envolvido"],
}

app = FastAPI(title="S&M OS 6.1 — Demo Backend", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================================
# SESSION STORAGE (FIX DEL LOOP)
# =========================================================
SESSIONS: Dict[str, Dict[str, Any]] = {}          # session_id -> state dict
UPLOADS: Dict[str, List[Dict[str, Any]]] = {}     # session_id -> list files

# =========================================================
# Helpers
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

def pecas_list_text() -> str:
    return "Escolha uma opção:\n" + "\n".join([f"{i+1}) {p}" for i, p in enumerate(TIPOS_PECA)])

def map_tipo_peca(user_text: str) -> Optional[str]:
    t = _norm(user_text)
    if t.isdigit():
        idx = int(t) - 1
        if 0 <= idx < len(TIPOS_PECA):
            return TIPOS_PECA[idx]
    aliases = {
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
    return aliases.get(t)

def get_state(sid: str) -> Dict[str, Any]:
    if sid not in SESSIONS:
        SESSIONS[sid] = {"_session_id": sid, "_created_at": datetime.now().isoformat(), "_expected_key": None}
    # migrate typo key if present
    st = SESSIONS[sid]
    if "valor_envovido" in st and "valor_envolvido" not in st:
        st["valor_envolvido"] = st.get("valor_envovido")
    return st

def set_expected_key(st: Dict[str, Any], key: Optional[str]):
    st["_expected_key"] = key

def next_missing_key(st: Dict[str, Any]) -> Optional[str]:
    for key, _q in FIELDS_ORDER:
        # alias check
        if key in ALIASES:
            present = any(bool(st.get(k2)) for k2 in ALIASES[key])
            if not present:
                return key
        else:
            if not st.get(key):
                return key
    return None

def next_missing_question(st: Dict[str, Any]) -> str:
    for key, question in FIELDS_ORDER:
        missing = False
        if key in ALIASES:
            missing = not any(bool(st.get(k2)) for k2 in ALIASES[key])
        else:
            missing = not bool(st.get(key))

        if missing:
            set_expected_key(st, key)
            if key == "tipo_peca":
                return question + "\n\n" + pecas_list_text() + "\n\nDica: digite o número (ex.: 2) ou o nome."
            if key == "provas_existentes":
                return question + "\n\nDica: você pode subir PDF/DOCX/TXT (e imagens, se quiser)."
            return question

    set_expected_key(st, None)
    return ""

def is_sufficient(st: Dict[str, Any]) -> bool:
    for k in REQUIRED_FIELDS:
        if k in ALIASES:
            if not any(bool(st.get(k2)) for k2 in ALIASES[k]):
                return False
        else:
            if not bool(st.get(k)):
                return False
    return True

def docx_to_b64(doc: Document) -> str:
    buf = BytesIO()
    doc.save(buf)
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def fmt_brl(value: int) -> str:
    s = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def is_placeholder(s: str) -> bool:
    t = (s or "").strip().lower()
    if not t:
        return True
    if "[preencher]" in t:
        return True
    if "seção não preenchida" in t or "secao nao preenchida" in t:
        return True
    if t in ("—", "-", "n/a", "na"):
        return True
    return False

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
    # max 4 CONDICIONAL:
    if sum(1 for x in items if _norm(x).startswith("condicional")) > 4:
        return False
    # no demasiados items cortos
    if sum(1 for x in items if len(x) < 25) > 3:
        return False
    return True

def likely_attorney_as_author(minuta: str) -> bool:
    t = _norm(minuta[:900])
    bad = ("[seu nome], advogado" in t and "vem" in t and "propor" in t)
    good = ("por seu advogado" in t or "por seu(sua) advogado(a)" in t)
    return bad and not good

def detect_trabalho_context(st: Dict[str, Any]) -> bool:
    blob = _norm(" ".join([
        st.get("area_subarea",""),
        st.get("objetivo_cliente",""),
        st.get("fatos_cronologia",""),
        st.get("partes",""),
    ]))
    return any(x in blob for x in ["trabalho","empregador","empregado","acidente de trabalho","demitiu","demissao","vara do trabalho","justica do trabalho"])

def validate_minuta(st: Dict[str, Any], minuta: str) -> List[str]:
    issues = []
    m = (minuta or "").strip()
    if len(m) < 900:
        issues.append("minuta_curta")
        return issues

    if not _norm(m).startswith("copie e cole no timbrado"):
        issues.append("minuta_sem_instrucao_timbrado")

    if likely_attorney_as_author(m):
        issues.append("minuta_advogado_como_autor")

    if detect_trabalho_context(st):
        t = _norm(m[:400])
        if "juiz do trabalho" not in t and "vara do trabalho" not in t:
            issues.append("minuta_forum_inadequado_trabalho")

    return issues

# =========================================================
# Upload extract (PDF/DOCX/TXT)
# =========================================================
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
# IA prompts (quality gates)
# =========================================================
OUTPUT_SCHEMA_PROMPT = r"""
RETORNE APENAS JSON.

REGRAS DURAS:
- Não inventar fatos, datas, valores. Se não existir no intake/uploads, marque como [HIP] ou CONDICIONAL.
- Estratégia: EXACTAMENTE 18 itens. No máximo 4 podem começar com "CONDICIONAL:".
- Cada item deve conter: ação + por quê + como/prova (1–2 linhas).

MINUTA:
- Deve iniciar com: "Copie e cole no timbrado do seu escritório antes de finalizar."
- AUTOR é o cliente. Estrutura obrigatória:
  "[NOME DO AUTOR], ... por seu advogado [SEU NOME], ... vem propor ..."
- Proibido: "[SEU NOME], advogado, vem propor..." (advogado como autor).
- Se o contexto for TRABALHO, usar "Juiz do Trabalho" / "Vara do Trabalho" (ou placeholders equivalentes).

SAÍDA:
{
  "forca_tese": "Muito forte|Forte|Moderada|Fraca|Muito fraca",
  "risco_improcedencia":"Baixo|Médio|Alto",
  "confiabilidade_analise":"Alta|Média|Baixa",
  "suficiencia_dados":"suficiente|parcialmente_suficiente|insuficiente",
  "status":"COMPLETA|ANÁLISE PRELIMINAR",
  "estrategia_18_pontos":[...18...],
  "secoes": { "1_CLASSIFICACAO_DO_CASO":"...", "2_SINTESE":"...", "3_QUESTAO_JURIDICA":"...", "4_ANALISE_TECNICA":"...", "5_FORCA_DA_TESE":"...", "6_CONFIABILIDADE":"...", "7_PROVAS":"...", "8_RISCOS":"...", "9_CENARIOS":"...", "10_ANALISE_ECONOMICA":"...", "11_RENTABILIDADE":"...", "12_SCORES":"...", "13_RED_TEAM":"...", "14_ESTRATEGIA":"...", "15_ACOES_PRIORITARIAS":"...", "16_PENDENCIAS":"...", "17_ALERTAS":"...", "18_REFLEXAO_FINAL":"..." },
  "minuta_peca":"..."
}
"""

REPAIR_PROMPT = r"""
Você vai REFAZER a saída.
Corrigir:
- estratégia 18 pontos: 18 itens úteis, max 4 CONDICIONAL, cada item com ação+porquê+prova.
- minuta: autor é o cliente por seu advogado; usar Vara do Trabalho se contexto de trabalho.
- sem inventar fatos/datas/valores.
Retorne APENAS JSON.
"""

def call_json(client: OpenAI, system: str, payload: Dict[str, Any], temperature: float = 0.2) -> Dict[str, Any]:
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

def build_payload(st: Dict[str, Any]) -> Dict[str, Any]:
    sid = st.get("_session_id", "")
    uploads_short = [
        {
            "filename": f["filename"],
            "mime": f["mime"],
            "text_excerpt": (f.get("text_excerpt") or "")[:1800],
        }
        for f in UPLOADS.get(sid, [])
    ]
    return {"intake": st, "uploads": uploads_short}

def validate_report_json(st: Dict[str, Any], data: Dict[str, Any]) -> List[str]:
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
    issues.extend(validate_minuta(st, minuta))

    return issues

def generate_report_strict(st: Dict[str, Any]) -> Dict[str, Any]:
    if not PROMPT_LOADED:
        raise HTTPException(status_code=500, detail="OS_6_1_PROMPT não carregado (Render env).")

    client = get_client()
    payload = build_payload(st)
    system = (OS_6_1_PROMPT + "\n\n" + OUTPUT_SCHEMA_PROMPT).strip()

    try:
        data = call_json(client, system, payload, temperature=TEMPERATURE)
        issues = validate_report_json(st, data)
        if not issues:
            data["_warnings"] = []
            return data

        # 2ª tentativa (máximo 2 para evitar timeout)
        data2 = call_json(client, REPAIR_PROMPT + "\n\n" + OUTPUT_SCHEMA_PROMPT, {**payload, "issues": issues, "previous": data}, temperature=0.2)
        issues2 = validate_report_json(st, data2)
        data2["_warnings"] = issues2
        return data2

    except HTTPException:
        raise
    except Exception as e:
        raise friendly_openai_error(e)

def generate_fee_json(client: OpenAI, st: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    sys = f"""
Retorne APENAS JSON com:
total(int), entrada(int), parcelas(int), justificativa_curta(str).
Regras:
- total entre {FEE_MIN_TOTAL} e {FEE_MAX_TOTAL}.
- Usar: valor_envolvido, urgencia, fase, tipo_peca, quantidade de provas, suficiência de dados, risco.
- Se valor_envolvido for alto (>= 50000), total deve ajustar proporcionalmente (evitar sempre o mesmo número).
- Não prometer êxito.
"""
    payload = {
        "fase": st.get("fase"),
        "tipo_peca": st.get("tipo_peca"),
        "area_subarea": st.get("area_subarea"),
        "valor_envolvido": st.get("valor_envolvido") or st.get("valor_envovido"),
        "urgencia_prazo": st.get("urgencia_prazo"),
        "provas_existentes": st.get("provas_existentes"),
        "n_uploads": len(UPLOADS.get(st.get("_session_id",""), [])),
        "forca_tese": report.get("forca_tese"),
        "risco_improcedencia": report.get("risco_improcedencia"),
        "confiabilidade_analise": report.get("confiabilidade_analise"),
        "suficiencia_dados": report.get("suficiencia_dados"),
    }
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    d = json.loads(r.choices[0].message.content)

    total = int(max(FEE_MIN_TOTAL, min(FEE_MAX_TOTAL, int(float(d.get("total", 6000))))))
    entrada = int(float(d.get("entrada", int(total * 0.3))))
    entrada = max(int(total * 0.2), min(int(total * 0.4), entrada))
    parcelas = int(d.get("parcelas", 6))
    parcelas = max(1, min(12, parcelas))

    return {
        "total": total,
        "entrada": entrada,
        "parcelas": parcelas,
        "justificativa_curta": str(d.get("justificativa_curta", "")).strip()[:1200],
    }

# =========================================================
# DOCX builders
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

def build_report_strategy_docx(report: Dict[str, Any], st: Dict[str, Any]) -> Document:
    doc = Document()
    title = doc.add_paragraph("RELATÓRIO — DIAGNÓSTICO JURÍDICO INTELIGENTE (S&M OS 6.1)")
    title.runs[0].bold = True
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph("")
    add_p(doc, f"Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    add_p(doc, f"Área/Subárea: {st.get('area_subarea','—')}")
    add_p(doc, f"Fase: {st.get('fase','—')}")
    add_p(doc, f"Partes: {st.get('partes','—')}")
    add_p(doc, f"Contratante/Recebedor: {st.get('contratante_nome','—')}")
    add_p(doc, f"Tipo de peça: {st.get('tipo_peca','—')}")
    add_p(doc, f"Valor/Impacto: {st.get('valor_envolvido') or st.get('valor_envovido') or '—'}")

    doc.add_paragraph("")
    add_h(doc, "Classificações técnicas", 13)
    add_p(doc, f"Força da tese: {report.get('forca_tese','—')}")
    add_p(doc, f"Confiabilidade da análise: {report.get('confiabilidade_analise','—')}")
    add_p(doc, f"Risco de improcedência: {report.get('risco_improcedencia','—')}")
    add_p(doc, f"Suficiência de dados: {report.get('suficiencia_dados','—')}")
    add_p(doc, f"Status: {report.get('status','—')}")

    doc.add_paragraph("")
    add_h(doc, "Estratégia (18 pontos)", 13)
    add_list_numbered(doc, report.get("estrategia_18_pontos", []))

    # Seções (si vienen)
    secoes = report.get("secoes") or {}
    if isinstance(secoes, dict) and secoes:
        doc.add_paragraph("")
        add_h(doc, "Seções OS 6.1 (resumo)", 13)
        for k, v in secoes.items():
            add_h(doc, str(k).replace("_", " "), 12)
            add_p(doc, str(v or "—"))

    warnings = report.get("_warnings") or []
    if warnings:
        doc.add_paragraph("")
        add_h(doc, "Avisos de validação (backend)", 12)
        add_p(doc, "A IA retornou inconsistências. Recomenda-se revisão humana reforçada.")
        for w in warnings[:20]:
            add_p(doc, f"- {w}")

    doc.add_paragraph("")
    add_h(doc, "Nota de compliance", 12)
    add_p(doc, "Saída assistiva. Revisão humana obrigatória em decisões críticas. Sem promessa de êxito.")
    return doc

def build_proposal_docx(st: Dict[str, Any], fee: Dict[str, Any]) -> Document:
    doc = Document()
    p = doc.add_paragraph("ORÇAMENTO / PROPOSTA DE HONORÁRIOS")
    p.runs[0].bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph("")

    contratante = st.get("contratante_nome") or "(PREENCHER)"
    total = int(fee["total"])
    entrada = int(fee["entrada"])
    parcelas = int(fee["parcelas"])
    saldo = max(0, total - entrada)
    parcela_val = int(saldo / max(parcelas, 1))

    t1 = doc.add_table(rows=6, cols=2)
    t1.style = "Table Grid"
    t1.cell(0, 0).text = "Contratante / Recebedor"
    t1.cell(0, 1).text = str(contratante)
    t1.cell(1, 0).text = "Mandatário(a)"
    t1.cell(1, 1).text = f"{MANDATARIA_NOME} — {MANDATARIA_OAB}"
    t1.cell(2, 0).text = "Objeto"
    t1.cell(2, 1).text = f"Atuação no caso informado (Área: {st.get('area_subarea','—')})."
    t1.cell(3, 0).text = "Data"
    t1.cell(3, 1).text = datetime.now().strftime("%d/%m/%Y")
    t1.cell(4, 0).text = "Validade da proposta"
    t1.cell(4, 1).text = f"{FEE_VALIDITY_DAYS} dias"
    t1.cell(5, 0).text = "Observação"
    t1.cell(5, 1).text = "Obrigação de meio. Sem promessa de êxito."

    doc.add_paragraph("")
    add_h(doc, "Honorários (sugestão por caso)", 13)
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
    add_h(doc, "Justificativa (curta)", 13)
    add_p(doc, fee.get("justificativa_curta", "—") or "—")

    doc.add_paragraph("")
    add_h(doc, "Orientação", 13)
    add_p(doc, "Copie e cole esta proposta no timbrado do seu escritório antes de enviar ao cliente.")
    return doc

def build_piece_docx(report: Dict[str, Any], st: Dict[str, Any]) -> Document:
    doc = Document()
    tipo = st.get("tipo_peca", "Peça")
    p = doc.add_paragraph(f"MINUTA — {tipo.upper()} (S&M OS 6.1)")
    p.runs[0].bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph("")
    warn = doc.add_paragraph("IMPORTANTE: Copie e cole no timbrado do seu escritório antes de finalizar. Revise dados e anexos.")
    warn.runs[0].bold = True

    doc.add_paragraph("")
    add_h(doc, "Minuta", 13)
    doc.add_paragraph(str(report.get("minuta_peca", "—")))

    doc.add_paragraph("")
    foot = doc.add_paragraph("Nota: minuta assistiva. Ajuste [PREENCHER] antes de assinar/protocolar.")
    foot.runs[0].italic = True
    return doc

# =========================================================
# Parsing “etiquetado” (para no romper el flujo)
# =========================================================
LABEL_TO_KEY = {
    "area": "area_subarea",
    "área": "area_subarea",
    "area/subarea": "area_subarea",
    "fase": "fase",
    "objetivo": "objetivo_cliente",
    "objetivo_cliente": "objetivo_cliente",
    "partes": "partes",
    "contratante": "contratante_nome",
    "contratante/recebedor": "contratante_nome",
    "peca": "tipo_peca",
    "peça": "tipo_peca",
    "tipo_peca": "tipo_peca",
    "fatos": "fatos_cronologia",
    "provas": "provas_existentes",
    "urgencia": "urgencia_prazo",
    "urgência": "urgencia_prazo",
    "valor": "valor_envolvido",
    "valor/impacto": "valor_envolvido",
    "info": "notas_adicionais",
}

LABEL_RE = re.compile(r"^\s*([A-Za-zÀ-ÿ\/ _]+)\s*[:\-]\s*(.+?)\s*$")

def parse_labeled_answer(msg: str) -> Optional[Tuple[str, str]]:
    m = LABEL_RE.match(msg or "")
    if not m:
        return None
    label = _norm(m.group(1)).replace(" ", "")
    val = (m.group(2) or "").strip()
    # normalize label variants
    if label in ("areasubarea", "area/subarea"):
        key = "area_subarea"
    elif label in ("tipodepeca", "tipodepeça", "tipodepeca"):
        key = "tipo_peca"
    else:
        # try direct map
        key = LABEL_TO_KEY.get(label) or LABEL_TO_KEY.get(label.replace("_", "")) or LABEL_TO_KEY.get(label.replace("/", ""))
    if not key:
        return None
    return key, val

def captured_view(st: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "Área/Subárea": st.get("area_subarea") or "—",
        "Fase": st.get("fase") or "—",
        "Objetivo": st.get("objetivo_cliente") or "—",
        "Partes": st.get("partes") or "—",
        "Contratante/Recebedor": st.get("contratante_nome") or "—",
        "Tipo de peça": st.get("tipo_peca") or "—",
        "Fatos (cronologia)": st.get("fatos_cronologia") or "—",
        "Provas existentes": st.get("provas_existentes") or "—",
        "Urgência/Prazo": st.get("urgencia_prazo") or "—",
        "Valor/Impacto": st.get("valor_envolvido") or st.get("valor_envovido") or "—",
        "Notas adicionais": st.get("notas_adicionais") or "—",
        "Uploads": [f.get("filename") for f in UPLOADS.get(st.get("_session_id",""), [])],
    }

# =========================================================
# API Models
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
    reply: str
    state: Dict[str, Any]
    expected_field: Optional[str] = None
    captured: Optional[Dict[str, Any]] = None

    report_docx_b64: Optional[str] = None
    report_docx_filename: Optional[str] = None
    proposal_docx_b64: Optional[str] = None
    proposal_docx_filename: Optional[str] = None
    piece_docx_b64: Optional[str] = None
    piece_docx_filename: Optional[str] = None

class UploadOut(BaseModel):
    file_id: str
    filename: str
    size_bytes: int
    text_extracted: bool

# =========================================================
# Routes
# =========================================================
@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "sm-os-demo",
        "version": "2.0.0",
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
    SESSIONS[sid] = {"_session_id": sid, "_created_at": datetime.now().isoformat(), "_expected_key": None}
    UPLOADS[sid] = []
    st = get_state(sid)
    q = next_missing_question(st)
    msg = "Vamos iniciar o diagnóstico.\n\n" + q
    return SessionOut(
        session_id=sid,
        message=msg,
        state=st,
        expected_field=st.get("_expected_key"),
        captured=captured_view(st),
    )

# /upload: soporta JSON base64 Y multipart/form-data
@app.post("/upload", response_model=UploadOut)
async def upload_file(request: Request, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    ct = (request.headers.get("content-type") or "").lower()

    if ct.startswith("multipart/form-data"):
        form = await request.form()
        sid = (form.get("session_id") or "").strip()
        if not sid:
            raise HTTPException(status_code=400, detail="session_id is required (form).")
        st = get_state(sid)
        UPLOADS.setdefault(sid, [])
        files_list = UPLOADS[sid]

        incoming_files = form.getlist("files") or []
        if not incoming_files:
            raise HTTPException(status_code=400, detail="No files provided.")

        # procesa 1 por 1 (devuelve el último)
        last_out = None
        for uf in incoming_files:
            if len(files_list) >= MAX_FILES_PER_SESSION:
                raise HTTPException(status_code=400, detail="Limite de arquivos por sessão atingido.")

            raw = await uf.read()
            size = len(raw)
            if size > MAX_FILE_MB * 1024 * 1024:
                raise HTTPException(status_code=400, detail=f"Arquivo muito grande. Máximo {MAX_FILE_MB}MB.")

            total = sum(f.get("size_bytes", 0) for f in files_list) + size
            if total > MAX_TOTAL_MB_PER_SESSION * 1024 * 1024:
                raise HTTPException(status_code=400, detail=f"Limite total por sessão: {MAX_TOTAL_MB_PER_SESSION}MB.")

            file_id = str(uuid.uuid4())
            text_excerpt = extract_text_from_upload(uf.filename, uf.content_type or "", raw)

            files_list.append(
                {
                    "file_id": file_id,
                    "filename": uf.filename,
                    "mime": uf.content_type or "",
                    "size_bytes": size,
                    "text_excerpt": text_excerpt[:MAX_EXCERPT_CHARS],
                }
            )

            last_out = UploadOut(file_id=file_id, filename=uf.filename, size_bytes=size, text_extracted=bool(text_excerpt.strip()))

        return last_out  # type: ignore

    # JSON base64 (compat)
    body = await request.json()
    sid = (body.get("session_id") or "").strip()
    filename = (body.get("filename") or "").strip()
    mime = (body.get("mime") or "").strip()
    b64 = (body.get("b64") or "").strip()

    if not sid or not filename or not b64:
        raise HTTPException(status_code=400, detail="session_id, filename, b64 are required (json).")

    st = get_state(sid)
    UPLOADS.setdefault(sid, [])
    files = UPLOADS[sid]
    if len(files) >= MAX_FILES_PER_SESSION:
        raise HTTPException(status_code=400, detail="Limite de arquivos por sessão atingido.")

    raw = base64.b64decode(b64)
    size = len(raw)
    if size > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Arquivo muito grande. Máximo {MAX_FILE_MB}MB.")

    total = sum(f.get("size_bytes", 0) for f in files) + size
    if total > MAX_TOTAL_MB_PER_SESSION * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Limite total por sessão: {MAX_TOTAL_MB_PER_SESSION}MB.")

    file_id = str(uuid.uuid4())
    text_excerpt = extract_text_from_upload(filename, mime, raw)

    files.append(
        {
            "file_id": file_id,
            "filename": filename,
            "mime": mime,
            "size_bytes": size,
            "text_excerpt": text_excerpt[:MAX_EXCERPT_CHARS],
        }
    )

    return UploadOut(file_id=file_id, filename=filename, size_bytes=size, text_extracted=bool(text_excerpt.strip()))

@app.post("/chat", response_model=ChatOut)
def chat(inp: ChatIn, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)

    sid = (inp.session_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session_id is required.")

    st = get_state(sid)

    msg = (inp.message or "").strip()
    if not msg:
        q = next_missing_question(st)
        return ChatOut(message=q, reply=q, state=st, expected_field=st.get("_expected_key"), captured=captured_view(st))

    # start signal (frontend puede mandar __start__)
    if msg in ("__start__", "start", "/start"):
        q = next_missing_question(st)
        out = "Vamos iniciar o diagnóstico.\n\n" + q
        return ChatOut(message=out, reply=out, state=st, expected_field=st.get("_expected_key"), captured=captured_view(st))

    # 1) si viene etiquetado, respeta la etiqueta
    parsed = parse_labeled_answer(msg)
    target_key = None
    val = None
    if parsed:
        target_key, val = parsed
    else:
        # 2) usa el expected_key de la sesión (FIX loop)
        target_key = st.get("_expected_key") or next_missing_key(st)
        val = msg

    if not target_key:
        target_key = next_missing_key(st)
        val = msg

    # Normaliza valores vacíos
    val = (val or "").strip()
    if not val:
        q = next_missing_question(st)
        return ChatOut(message=q, reply=q, state=st, expected_field=st.get("_expected_key"), captured=captured_view(st))

    # Guarda la respuesta en el campo correcto
    if target_key == "tipo_peca":
        mapped = map_tipo_peca(val)
        if not mapped:
            out = "❗Tipo de peça inválido.\n\n" + pecas_list_text() + "\n\nDica: digite o número (ex.: 2) ou o nome."
            # no avance el expected_key
            set_expected_key(st, "tipo_peca")
            return ChatOut(message=out, reply=out, state=st, expected_field=st.get("_expected_key"), captured=captured_view(st))
        st["tipo_peca"] = mapped

    elif target_key == "valor_envolvido":
        st["valor_envolvido"] = val
        # compat key antiguo
        st["valor_envovido"] = val

    else:
        st[target_key] = val

    # Pregunta siguiente o genera docs
    if not is_sufficient(st):
        q = next_missing_question(st)
        return ChatOut(message=q, reply=q, state=st, expected_field=st.get("_expected_key"), captured=captured_view(st))

    # =====================================================
    # Generación final (3 DOCX)
    # =====================================================
    try:
        report = generate_report_strict(st)
        client = get_client()
        fee = generate_fee_json(client, st, report)

        doc_report = build_report_strategy_docx(report, st)
        doc_prop = build_proposal_docx(st, fee)
        doc_piece = build_piece_docx(report, st)

        ts = datetime.now().strftime("%Y%m%d-%H%M")
        tipo_safe = (st.get("tipo_peca", "Peca")).replace(" ", "_").replace("/", "_")

        out = "✅ Pronto. Baixe os 3 DOCX: Relatório+Estratégia(18), Proposta (valor por caso) e Minuta da Peça."
        set_expected_key(st, None)

        return ChatOut(
            message=out,
            reply=out,
            state=st,
            expected_field=None,
            captured=captured_view(st),
            report_docx_b64=docx_to_b64(doc_report),
            report_docx_filename=f"Relatorio_SM_OS_{ts}.docx",
            proposal_docx_b64=docx_to_b64(doc_prop),
            proposal_docx_filename=f"Proposta_Honorarios_SM_{ts}.docx",
            piece_docx_b64=docx_to_b64(doc_piece),
            piece_docx_filename=f"Minuta_{tipo_safe}_{ts}.docx",
        )

    except HTTPException:
        raise
    except Exception as e:
        raise friendly_openai_error(e)

@app.get("/widget", response_class=HTMLResponse)
def widget():
    return HTMLResponse("<h3>OK</h3><p>Use o frontend no WordPress/Elementor.</p>")
