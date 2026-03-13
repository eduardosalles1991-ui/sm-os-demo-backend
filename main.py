# -*- coding: utf-8 -*-
import os
import uuid
import json
import base64
import re
import unicodedata
from io import BytesIO
from datetime import datetime
from typing import Dict, Any, Optional, List

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
# CONFIG
# =========================================================
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", MODEL)

ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://correamendes.wpcomstaging.com")
DEMO_KEY = (os.getenv("DEMO_KEY") or "").strip()
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))

# ⚠️ pedido: sacar "Ester Cristina" del presupuesto
MANDATARIA_NOME = os.getenv("MANDATARIA_NOME", "(PREENCHER)")
MANDATARIA_OAB = os.getenv("MANDATARIA_OAB", "OAB/SP 105.488")

# Honorarios dinámicos (límites)
FEE_MIN_TOTAL = int(os.getenv("FEE_MIN_TOTAL", "1500"))
FEE_MAX_TOTAL = int(os.getenv("FEE_MAX_TOTAL", "250000"))
FEE_VALIDITY_DAYS = int(os.getenv("FEE_VALIDITY_DAYS", "7"))

# Upload limits
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "7"))
MAX_FILES_PER_SESSION = int(os.getenv("MAX_FILES_PER_SESSION", "10"))
MAX_TOTAL_MB_PER_SESSION = int(os.getenv("MAX_TOTAL_MB_PER_SESSION", "20"))
MAX_IMAGES_PER_SESSION = int(os.getenv("MAX_IMAGES_PER_SESSION", "6"))
MAX_EXCERPT_CHARS = int(os.getenv("MAX_EXCERPT_CHARS", "8000"))

TIPOS_PECA = [
    "Notificação Extrajudicial",
    "Petição Inicial",
    "Contestação",
    "Réplica",
    "Recurso",
    "Minuta de Acordo",
    "Petição Intermediária (Manifestação)",
]

FIELDS_ORDER = [
    ("area_subarea", "Qual a área/subárea? (ex.: cível/consumidor/indenizatória)"),
    ("fase", "Qual a fase? (consultivo / pré-contencioso / processo / recurso / execução)"),
    ("objetivo_cliente", "Qual o objetivo do cliente? (o que ele quer obter)"),
    ("partes", "Quem são as partes? (autor/réu e relação entre eles)"),
    ("contratante_nome", "Qual o nome completo do Contratante/Recebedor para a Proposta de Honorários?"),
    ("tipo_peca", "Qual peça você precisa gerar? (digite o número ou o nome)"),
    ("fatos_cronologia", "Conte os fatos em ordem (inclua: demissão/afastamento/CAT/INSS se houver)."),
    ("provas_existentes", "Quais provas/documentos você já tem? (liste) — Você também pode subir arquivos agora."),
    ("urgencia_prazo", "Há urgência ou prazo crítico? (qual?)"),
    ("valor_envovido", "Qual o valor envolvido/impacto? (se não souber, estimativa)"),
    ("notas_adicionais", "Alguma informação adicional relevante? (detalhes que não cabiam antes)"),
]
REQUIRED_FIELDS = [k for k, _ in FIELDS_ORDER]

# OS prompt via ENV
OS_6_1_PROMPT = (os.getenv("OS_6_1_PROMPT") or "").strip()
PROMPT_LOADED = bool(OS_6_1_PROMPT) and ("COLE AQUI" not in OS_6_1_PROMPT.upper()) and (len(OS_6_1_PROMPT) > 1500)

OUTPUT_CONTRACT = r"""
CONTRATO DE SAÍDA (OBRIGATÓRIO)
- Retorne APENAS JSON (sem markdown).
- Não invente fatos: se não estiver no intake/uploads, use [PREENCHER] e/ou "CONDICIONAL:".
- estrategia_18_pontos: lista com EXATAMENTE 18 itens (conteúdo útil; não pode ser 18x "CONDICIONAL:" vazio).
- tipo_peca deve ecoar exatamente o escolhido.
- minuta_peca deve iniciar com "Copie e cole no timbrado do seu escritório antes de finalizar."
- secoes: objeto com 18 chaves (1_CLASSIFICACAO ... 18_REFLEXAO_FINAL) e conteúdo útil (não devolver tudo como "—").
- Campos obrigatórios: forca_tese, risco_improcedencia, confiabilidade_analise, suficiencia_dados, status.
"""

SYSTEM_OS_JSON = (OS_6_1_PROMPT + "\n\n" + OUTPUT_CONTRACT).strip()


# =========================================================
# APP
# =========================================================
app = FastAPI(title="S&M OS 6.1 — Demo Backend", version="1.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOADS: Dict[str, List[Dict[str, Any]]] = {}  # in-memory demo


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
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured (Render env).")
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
        "notificacion extrajudicial": "Notificação Extrajudicial",
        "peticao inicial": "Petição Inicial",
        "peticion inicial": "Petição Inicial",
        "inicial": "Petição Inicial",
        "contestacao": "Contestação",
        "replica": "Réplica",
        "recurso": "Recurso",
        "acordo": "Minuta de Acordo",
        "minuta de acordo": "Minuta de Acordo",
        "manifestacao": "Petição Intermediária (Manifestação)",
        "peticao intermediaria": "Petição Intermediária (Manifestação)",
    }
    if t in aliases:
        return aliases[t]
    for opt in TIPOS_PECA:
        if _norm(opt) == t:
            return opt
    return None

def next_missing(state: Dict[str, Any]) -> str:
    for key, question in FIELDS_ORDER:
        if not state.get(key):
            if key == "tipo_peca":
                return question + "\n\n" + pecas_list_text() + "\n\nDica: digite o número (ex.: 2) ou o nome."
            if key == "provas_existentes":
                return question + "\n\nDica: você pode subir PDF/DOCX/TXT e imagens como prova."
            return question
    return ""

def is_sufficient(state: Dict[str, Any]) -> bool:
    return all(bool(state.get(k)) for k in REQUIRED_FIELDS)

def docx_to_b64(doc: Document) -> str:
    buf = BytesIO()
    doc.save(buf)
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def fmt_brl(value: int) -> str:
    s = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def add_h(doc: Document, text: str, size=14):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(size)
    p.space_after = Pt(6)

def add_p(doc: Document, text: str):
    doc.add_paragraph(text)

def add_list_numbered(doc: Document, items: List[str]):
    try:
        for it in items:
            doc.add_paragraph(str(it), style="List Number")
    except Exception:
        for i, it in enumerate(items, start=1):
            doc.add_paragraph(f"{i}. {it}")

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

def force_18(items: List[str]) -> List[str]:
    items = [x.strip() for x in items if x.strip()]
    if len(items) > 18:
        items = items[:18]
    while len(items) < 18:
        i = len(items) + 1
        items.append(f"CONDICIONAL: Completar o ponto {i} após validar pendências críticas (prova mínima, prazo, objeto e narrativa adversa).")
    return items

def looks_bad_18(items: List[str]) -> bool:
    if len(items) != 18:
        return True
    cond = sum(1 for x in items if _norm(x).startswith("condicional"))
    short = sum(1 for x in items if len(x) < 12)
    return cond >= 10 or short >= 12

def validate_notificacao(minuta: str) -> bool:
    m = (minuta or "").lower()
    must = ["notificação", "extrajudicial", "dos fatos", "do direito", "dos pedidos"]
    return all(x in m for x in must) and len(m) > 900

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

def extract_text_from_upload(filename: str, mime: str, b64: str) -> str:
    raw = base64.b64decode(b64)
    low = (filename or "").lower()
    mime = mime or ""
    if low.endswith(".pdf") or mime == "application/pdf":
        return extract_text_from_pdf(raw)
    if low.endswith(".docx") or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return extract_text_from_docx(raw)
    if low.endswith(".txt") or mime.startswith("text/"):
        return extract_text_from_txt(raw)
    return ""  # imágenes: opcional (Vision) si lo querés luego


# =========================================================
# IA: reparación / honorarios / reporte
# =========================================================
def repair_18_points_with_model(client: OpenAI, state: Dict[str, Any], items: List[str]) -> List[str]:
    sys = (
        "Retorne APENAS JSON: {'estrategia_18_pontos':[...]} com EXATAMENTE 18 itens.\n"
        "Baseie-se no intake. Não invente fatos. Se faltar dado, use 'CONDICIONAL:' mas com conteúdo útil.\n"
    )
    payload = {"intake": state, "estrategia_18_pontos_atual": items}
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role":"system","content":sys},{"role":"user","content":json.dumps(payload, ensure_ascii=False)}],
        temperature=0.2,
        response_format={"type":"json_object"},
    )
    data = json.loads(resp.choices[0].message.content)
    return force_18(normalize_points(data.get("estrategia_18_pontos")))

def repair_report_with_model(client: OpenAI, state: Dict[str, Any], uploads_short: List[Dict[str, Any]]) -> Dict[str, Any]:
    sys = (
        "Retorne APENAS JSON com estas chaves:\n"
        "- forca_tese\n- risco_improcedencia\n- confiabilidade_analise\n- suficiencia_dados\n- status\n"
        "- estrategia_18_pontos (18 itens)\n- minuta_peca\n- secoes (18 chaves: 1_CLASSIFICACAO ... 18_REFLEXAO_FINAL)\n"
        "Não invente fatos. Use [PREENCHER] e CONDICIONAL se faltar dados.\n"
        "minuta_peca deve começar com: 'Copie e cole no timbrado do seu escritório antes de finalizar.'\n"
    )
    payload = {"intake": state, "uploads": uploads_short}
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role":"system","content":sys},{"role":"user","content":json.dumps(payload, ensure_ascii=False)}],
        temperature=0.2,
        response_format={"type":"json_object"},
    )
    return json.loads(resp.choices[0].message.content)

def generate_fee_json(client: OpenAI, state: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    prompt = f"""
Retorne APENAS JSON com:
total(int), entrada(int), saldo(int), parcelas(int), justificativa_curta(str).
Sem promessa de êxito. Obrigação de meio.
Limites total: {FEE_MIN_TOTAL}–{FEE_MAX_TOTAL}.
Critérios: fase, complexidade, urgência, valor em disputa, volume de prova, risco, esforço.
Base:
fase={state.get('fase')}
tipo={state.get('tipo_peca')}
area={state.get('area_subarea')}
valor={state.get('valor_envovido')}
urgencia={state.get('urgencia_prazo')}
provas={state.get('provas_existentes')}
forca={report.get('forca_tese')}
risco={report.get('risco_improcedencia')}
confiabilidade={report.get('confiabilidade_analise')}
"""
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role":"system","content":prompt},{"role":"user","content":"Gerar honorários por caso."}],
        temperature=0.2,
        response_format={"type":"json_object"},
    )
    d = json.loads(r.choices[0].message.content)
    total = int(max(FEE_MIN_TOTAL, min(FEE_MAX_TOTAL, int(float(d.get("total", 6000))))))
    entrada = int(float(d.get("entrada", int(total*0.3))))
    entrada = max(int(total*0.2), min(int(total*0.4), entrada))
    saldo = total - entrada
    parcelas = int(d.get("parcelas", 6))
    parcelas = max(1, min(12, parcelas))
    return {
        "total": total, "entrada": entrada, "saldo": saldo, "parcelas": parcelas,
        "justificativa_curta": str(d.get("justificativa_curta","")).strip()[:1000]
    }

def generate_report_json(state: Dict[str, Any]) -> Dict[str, Any]:
    if not PROMPT_LOADED:
        raise HTTPException(status_code=500, detail="OS_6_1_PROMPT não carregado (Render env).")

    client = get_client()
    sid = state.get("_session_id","")

    uploads_short = [{"name": f["filename"], "mime": f["mime"], "text_excerpt": (f.get("text_excerpt","") or "")[:1200]}
                     for f in UPLOADS.get(sid, [])]
    payload = {"intake": state, "uploads": uploads_short}

    try:
        # 1) intento principal: OS completo
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role":"system","content":SYSTEM_OS_JSON},
                      {"role":"user","content":json.dumps(payload, ensure_ascii=False)}],
            temperature=TEMPERATURE,
            response_format={"type":"json_object"},
        )
        data = json.loads(resp.choices[0].message.content)

        # mínimos
        data.setdefault("forca_tese", "CONDICIONAL: insuficiência de dados para classificar com firmeza.")
        data.setdefault("risco_improcedencia", "CONDICIONAL: depende de prova mínima e narrativa adversa.")
        data.setdefault("confiabilidade_analise", "média")
        data.setdefault("suficiencia_dados", "parcialmente_suficiente")
        data.setdefault("status", "ANÁLISE PRELIMINAR")
        data.setdefault("secoes", {})

        pts = force_18(normalize_points(data.get("estrategia_18_pontos")))
        if looks_bad_18(pts):
            pts = repair_18_points_with_model(client, state, pts)
        data["estrategia_18_pontos"] = pts

        minuta = str(data.get("minuta_peca","")).strip()
        if not minuta.lower().startswith("copie e cole no timbrado"):
            minuta = "Copie e cole no timbrado do seu escritório antes de finalizar.\n\n" + minuta

        # Si notificação: valida y repara si necesario
        if state.get("tipo_peca") == "Notificação Extrajudicial" and not validate_notificacao(minuta):
            repaired = repair_report_with_model(client, state, uploads_short)
            minuta2 = str(repaired.get("minuta_peca","")).strip()
            if minuta2:
                minuta = minuta2 if minuta2.lower().startswith("copie e cole no timbrado") else ("Copie e cole no timbrado do seu escritório antes de finalizar.\n\n"+minuta2)
                data["minuta_peca"] = minuta

        data["minuta_peca"] = minuta

        # Si el reporte vino muy vacío → repair total
        if (str(data.get("forca_tese","")).strip() in ("", "—")) or (not isinstance(data.get("secoes"), dict)) or (len(str(data.get("minuta_peca",""))) < 200):
            data = repair_report_with_model(client, state, uploads_short)

            pts = force_18(normalize_points(data.get("estrategia_18_pontos")))
            if looks_bad_18(pts):
                pts = repair_18_points_with_model(client, state, pts)
            data["estrategia_18_pontos"] = pts

            minuta = str(data.get("minuta_peca","")).strip()
            if not minuta.lower().startswith("copie e cole no timbrado"):
                minuta = "Copie e cole no timbrado do seu escritório antes de finalizar.\n\n" + minuta
            data["minuta_peca"] = minuta

        return data

    except HTTPException:
        raise
    except Exception as e:
        raise friendly_openai_error(e)


# =========================================================
# DOCX builders (3 documentos)
# =========================================================
def build_report_strategy_docx(report: Dict[str, Any], state: Dict[str, Any]) -> Document:
    doc = Document()
    title = doc.add_paragraph("RELATÓRIO — DIAGNÓSTICO JURÍDICO INTELIGENTE (S&M OS 6.1)")
    title.runs[0].bold = True
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph("")
    add_p(doc, f"Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    add_p(doc, f"Área/Subárea: {state.get('area_subarea','—')}")
    add_p(doc, f"Fase: {state.get('fase','—')}")
    add_p(doc, f"Partes: {state.get('partes','—')}")
    add_p(doc, f"Contratante/Recebedor: {state.get('contratante_nome','—')}")
    add_p(doc, f"Tipo de peça: {state.get('tipo_peca','—')}")

    sid = state.get("_session_id","")
    files = [f["filename"] for f in UPLOADS.get(sid, [])]
    if files:
        add_p(doc, "Provas (arquivos anexados): " + ", ".join(files))

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

    doc.add_paragraph("")
    add_h(doc, "Relatório estruturado (18 seções OS)", 13)
    secoes = report.get("secoes", {}) if isinstance(report.get("secoes", {}), dict) else {}
    order = [
        ("1. CLASSIFICAÇÃO DO CASO", "1_CLASSIFICACAO"),
        ("2. SÍNTESE", "2_SINTESE"),
        ("3. QUESTÃO JURÍDICA", "3_QUESTAO_JURIDICA"),
        ("4. ANÁLISE TÉCNICA", "4_ANALISE_TECNICA"),
        ("5. FORÇA DA TESE", "5_FORCA_DA_TESE"),
        ("6. CONFIABILIDADE", "6_CONFIABILIDADE"),
        ("7. PROVAS", "7_PROVAS"),
        ("8. RISCOS", "8_RISCOS"),
        ("9. CENÁRIOS", "9_CENARIOS"),
        ("10. ANÁLISE ECONÔMICA", "10_ANALISE_ECONOMICA"),
        ("11. RENTABILIDADE", "11_RENTABILIDADE"),
        ("12. SCORES", "12_SCORES"),
        ("13. RED TEAM", "13_RED_TEAM"),
        ("14. ESTRATÉGIA", "14_ESTRATEGIA"),
        ("15. AÇÕES PRIORITÁRIAS", "15_ACOES_PRIORITARIAS"),
        ("16. PENDÊNCIAS", "16_PENDENCIAS"),
        ("17. ALERTAS", "17_ALERTAS"),
        ("18. REFLEXÃO FINAL", "18_REFLEXAO_FINAL"),
    ]
    for t, k in order:
        add_h(doc, t, 12)
        add_p(doc, str(secoes.get(k, "CONDICIONAL: seção não preenchida — rever intake.")))

    doc.add_paragraph("")
    foot = doc.add_paragraph(
        "Nota: saída assistiva. Revisão humana obrigatória em decisões críticas. "
        "Sem promessa de resultado. Proibido inventar fatos/provas/jurisprudência."
    )
    foot.runs[0].italic = True
    return doc

def build_proposal_docx(state: Dict[str, Any], fee: Dict[str, Any]) -> Document:
    doc = Document()
    p = doc.add_paragraph("ORÇAMENTO / PROPOSTA DE HONORÁRIOS")
    p.runs[0].bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph("")

    contratante = state.get("contratante_nome") or "(PREENCHER)"

    total = int(fee["total"]); entrada = int(fee["entrada"]); saldo = int(fee["saldo"]); parcelas = int(fee["parcelas"])
    parcela_val = int(saldo / max(parcelas, 1))

    t1 = doc.add_table(rows=6, cols=2)
    t1.style = "Table Grid"
    t1.cell(0, 0).text = "Contratante / Recebedor"
    t1.cell(0, 1).text = str(contratante)
    t1.cell(1, 0).text = "Mandatária"
    t1.cell(1, 1).text = f"{MANDATARIA_NOME} — {MANDATARIA_OAB}"
    t1.cell(2, 0).text = "Objeto"
    t1.cell(2, 1).text = f"Atuação no caso informado (Área: {state.get('area_subarea','—')})."
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
    add_p(doc, fee.get("justificativa_curta","—") or "—")
    return doc

def build_piece_docx(report: Dict[str, Any], state: Dict[str, Any]) -> Document:
    doc = Document()
    tipo = state.get("tipo_peca", "Peça")
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
# API Models
# =========================================================
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
# Routes
# =========================================================
@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "sm-os-demo",
        "version": "1.3.0",
        "has_openai_key": bool(OPENAI_API_KEY),
        "allowed_origin": ALLOWED_ORIGIN,
        "model": MODEL,
        "prompt_loaded": PROMPT_LOADED,
    }

@app.get("/healt")
def healt():
    return health()

@app.post("/session/new", response_model=SessionOut)
def session_new(x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = str(uuid.uuid4())
    UPLOADS[sid] = []
    return SessionOut(
        session_id=sid,
        message="Vamos iniciar o diagnóstico.\n\n" + next_missing({"_session_id": sid}),
        state={"_session_id": sid}
    )

@app.post("/upload", response_model=UploadOut)
def upload_file(inp: UploadIn, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = inp.session_id
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
    text_excerpt = extract_text_from_upload(inp.filename, inp.mime, inp.b64)

    files.append({
        "file_id": file_id,
        "filename": inp.filename,
        "mime": inp.mime,
        "size_bytes": size,
        "text_excerpt": text_excerpt[:MAX_EXCERPT_CHARS],
    })

    return UploadOut(file_id=file_id, filename=inp.filename, size_bytes=size, text_extracted=bool(text_excerpt.strip()))

@app.post("/chat", response_model=ChatOut)
def chat(inp: ChatIn, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    state = inp.state or {}
    sid = state.get("_session_id") or inp.session_id
    state["_session_id"] = sid

    # Fill next missing field
    for key, _question in FIELDS_ORDER:
        if not state.get(key):
            val = (inp.message or "").strip()
            if key == "tipo_peca":
                mapped = map_tipo_peca(val)
                if not mapped:
                    return ChatOut(message="❗Tipo de peça inválido.\n\n" + pecas_list_text(), state=state)
                state[key] = mapped
            else:
                state[key] = val
            break

    if not is_sufficient(state):
        return ChatOut(message=next_missing(state), state=state)

    report = generate_report_json(state)
    client = get_client()
    fee = generate_fee_json(client, state, report)

    doc_report = build_report_strategy_docx(report, state)
    doc_prop = build_proposal_docx(state, fee)
    doc_piece = build_piece_docx(report, state)

    ts = datetime.now().strftime("%Y%m%d-%H%M")
    tipo_safe = (state.get("tipo_peca", "Peca")).replace(" ", "_").replace("/", "_")

    return ChatOut(
        message="✅ Pronto. Baixe os 3 DOCX: Relatório+Estratégia(18), Proposta (valor por caso) e Minuta da Peça.",
        state=state,
        report_docx_b64=docx_to_b64(doc_report),
        report_docx_filename=f"Relatorio_SM_OS_6_1_{ts}.docx",
        proposal_docx_b64=docx_to_b64(doc_prop),
        proposal_docx_filename=f"Proposta_Honorarios_SM_{ts}.docx",
        piece_docx_b64=docx_to_b64(doc_piece),
        piece_docx_filename=f"Minuta_{tipo_safe}_{ts}.docx",
    )


# =========================================================
# Widget: loader + Enter envia + input fijo
# =========================================================
WIDGET_HTML = f"""
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>S&M OS 6.1 — Widget</title>
<style>
  :root {{
    --panel: rgba(10, 12, 18, .55);
    --panel2: rgba(10, 12, 18, .40);
    --line: rgba(255,255,255,.14);
    --text: rgba(245,248,255,.92);
    --muted: rgba(245,248,255,.70);
    --gold: #f5c451;
  }}
  *{{ box-sizing:border-box; }}
  html, body {{ height:100%; margin:0; background:transparent; overflow:hidden; }}
  body {{ color: var(--text);
    font-family: system-ui, -apple-system, Segoe UI, Inter, Arial; }}

  .overlay {{
    position: fixed; inset: 0; display: none;
    align-items: center; justify-content: center;
    background: rgba(0,0,0,.45); z-index: 9999; padding: 20px;
  }}
  .overlayBox {{
    width: min(520px, 92vw);
    border-radius: 16px;
    border: 1px solid rgba(255,255,255,.16);
    background: rgba(10,12,18,.72);
    backdrop-filter: blur(12px);
    padding: 18px;
  }}
  .progress {{
    width: 100%; height: 10px; border-radius: 999px; overflow: hidden;
    background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.10);
  }}
  .bar {{
    width: 35%; height: 100%;
    background: rgba(245,196,81,.75);
    animation: indet 1.2s infinite;
  }}
  @keyframes indet {{
    0% {{ transform: translateX(-120%); }}
    100% {{ transform: translateX(320%); }}
  }}

  .app {{
    height: 100%;
    display:flex;
    flex-direction:column;
    gap:10px;
    padding: 12px;
    overflow:hidden;
  }}

  .top {{
    flex: 0 0 auto;
    display:flex;
    gap:10px;
    align-items:center;
    justify-content:space-between;
    padding: 12px 14px;
    border-radius: 16px;
    border: 1px solid var(--line);
    background: var(--panel);
    backdrop-filter: blur(10px);
  }}

  .brand {{ display:flex; align-items:center; gap:10px; min-width:0; }}
  .logo {{
    width:34px;height:34px;border-radius:12px; display:grid;place-items:center;
    font-weight:900; color: rgba(245,196,81,.95);
    border:1px solid rgba(245,196,81,.25);
    background: rgba(245,196,81,.12);
  }}
  .t1 {{ font-weight:900; font-size:14px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .t2 {{ font-size:12px; color: var(--muted); }}

  .actions {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; justify-content:flex-end; }}
  input, button {{
    border-radius: 12px;
    border: 1px solid rgba(255,255,255,.18);
    background: rgba(0,0,0,.25);
    color: var(--text);
    padding: 10px 12px;
    outline:none;
  }}
  .key {{ width: 320px; max-width: 60vw; }}
  .btn {{
    border-color: rgba(245,196,81,.35);
    background: rgba(245,196,81,.14);
    font-weight:900;
    cursor:pointer;
  }}
  .btn:hover {{ background: rgba(245,196,81,.20); }}
  .btn2 {{ cursor:pointer; background: rgba(255,255,255,.06); }}

  .mid {{
    flex: 1 1 auto;
    min-height: 0;
    display:flex;
    flex-direction:column;
    gap:10px;
    overflow:hidden;
  }}

  .uploadRow {{
    flex: 0 0 auto;
    display:flex; gap:10px; align-items:center; flex-wrap:wrap;
    padding: 10px 12px;
    border-radius: 16px;
    border: 1px solid var(--line);
    background: var(--panel2);
    backdrop-filter: blur(10px);
  }}
  .hint {{ font-size:12px; color: var(--muted); }}

  .log {{
    flex: 1 1 auto;
    min-height: 0;
    overflow:auto;
    padding: 14px;
    border-radius: 16px;
    border: 1px solid var(--line);
    background: rgba(0,0,0,.20);
    backdrop-filter: blur(8px);
    white-space: pre-wrap;
    line-height: 1.45;
  }}

  .bottom {{
    flex: 0 0 auto;
    display:flex; gap:10px; align-items:center;
    padding: 10px 12px;
    border-radius: 16px;
    border: 1px solid var(--line);
    background: var(--panel);
    backdrop-filter: blur(10px);
  }}
  .msg {{ flex:1; min-width: 160px; }}

  @media (max-width: 780px) {{
    .key {{ width: 100%; max-width: 100%; }}
    .actions {{ width: 100%; justify-content:flex-start; }}
    .top {{ flex-direction:column; align-items:flex-start; }}
  }}
</style>
</head>
<body>
  <div class="overlay" id="overlay">
    <div class="overlayBox">
      <div style="font-weight:900;margin-bottom:6px">Gerando seus arquivos…</div>
      <div style="opacity:.85;margin-bottom:12px;font-size:13px">Aguarde. Montando Relatório + Proposta + Peça (DOCX).</div>
      <div class="progress"><div class="bar"></div></div>
    </div>
  </div>

  <div class="app">
    <div class="top">
      <div class="brand">
        <div class="logo">S&M</div>
        <div>
          <div class="t1">Diagnóstico Jurídico Inteligente</div>
          <div class="t2">S&M OS 6.1 • Chat guiado • 3 DOCX (Relatório + Proposta + Peça)</div>
        </div>
      </div>

      <div class="actions">
        <input id="key" class="key" placeholder="DEMO_KEY" />
        <button id="start" class="btn">Ativar</button>
        <button id="reset" class="btn2">Reiniciar</button>
      </div>
    </div>

    <div class="mid">
      <div class="uploadRow">
        <input type="file" id="files" multiple accept=".pdf,.docx,.txt,application/pdf,text/*" />
        <button id="upload" class="btn2">Subir provas</button>
        <div class="hint">PDF/DOCX/TXT: extração local. (Imagens/vision opcional)</div>
      </div>

      <div id="log" class="log"></div>

      <div class="bottom">
        <input id="msg" class="msg" placeholder="Digite aqui… (Enter envia)" />
        <button id="send" class="btn">Enviar</button>
        <button id="dl1" class="btn2" disabled>Baixar Relatório</button>
        <button id="dl2" class="btn2" disabled>Baixar Proposta</button>
        <button id="dl3" class="btn2" disabled>Baixar Peça</button>
      </div>
    </div>
  </div>

<script>
let DEMO_KEY="";
let sessionId=null;
let state={{}};
let b1=null,n1=null,b2=null,n2=null,b3=null,n3=null;

const overlay=document.getElementById("overlay");
const log=document.getElementById("log");
const key=document.getElementById("key");
const start=document.getElementById("start");
const reset=document.getElementById("reset");
const msg=document.getElementById("msg");
const send=document.getElementById("send");
const files=document.getElementById("files");
const upload=document.getElementById("upload");
const dl1=document.getElementById("dl1");
const dl2=document.getElementById("dl2");
const dl3=document.getElementById("dl3");

function add(t){{ log.textContent += t + "\\n"; log.scrollTop=log.scrollHeight; }}
function setBusy(on) {{
  overlay.style.display = on ? "flex" : "none";
  start.disabled = on; reset.disabled = on; send.disabled = on; upload.disabled = on;
  msg.disabled = on;
}}
async function fetchJson(url,opt) {{
  const r=await fetch(url,opt);
  let d={{}}; try{{ d=await r.json(); }}catch(e){{}}
  if(!r.ok) throw new Error(d.detail||("HTTP "+r.status));
  return d;
}}
function enableDl(x){{ dl1.disabled=dl2.disabled=dl3.disabled=!x; }}
function downloadDocx(b64,name){{
  const bin=atob(b64);
  const bytes=new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++) bytes[i]=bin.charCodeAt(i);
  const blob=new Blob([bytes],{{type:"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}});
  const url=URL.createObjectURL(blob);
  const a=document.createElement("a"); a.href=url; a.download=name||"arquivo.docx";
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}}
async function fileToB64(f) {{
  return new Promise((res,rej)=>{{
    const r=new FileReader();
    r.onload=()=>res(String(r.result).split(",")[1]);
    r.onerror=rej;
    r.readAsDataURL(f);
  }});
}}
async function doStart() {{
  DEMO_KEY=key.value.trim();
  if(!DEMO_KEY) {{ add("Cole o DEMO_KEY e clique em Ativar."); return; }}
  try {{
    setBusy(true);
    add("Iniciando...");
    const d=await fetchJson("/session/new",{{method:"POST",headers:{{"x-demo-key":DEMO_KEY}}}});
    sessionId=d.session_id;
    state=d.state||{{}};
    enableDl(false); b1=b2=b3=null;
    add(d.message);
  }} catch(e) {{
    add("⚠️ Erro: " + e.message);
  }} finally {{
    setBusy(false);
  }}
}}
start.onclick = doStart;
reset.onclick = doStart;

upload.onclick = async ()=>{{
  if(!sessionId){{ add("Ative antes de subir."); return; }}
  const list=[...files.files];
  if(!list.length) return;
  try {{
    setBusy(true);
    for(const f of list){{
      add("Subindo: "+f.name);
      const b64=await fileToB64(f);
      const payload={{session_id:sessionId, filename:f.name, mime:f.type||"application/octet-stream", b64:b64}};
      const out=await fetchJson("/upload",{{method:"POST",headers:{{"Content-Type":"application/json","x-demo-key":DEMO_KEY}},body:JSON.stringify(payload)}});
      add("OK: "+out.filename+" | text_extracted="+out.text_extracted);
    }}
    add("Uploads finalizados.");
  }} catch(e) {{
    add("⚠️ Erro upload: " + e.message);
  }} finally {{
    setBusy(false);
  }}
}};

async function doSend() {{
  if(!sessionId){{ add("Ative primeiro."); return; }}
  const text=msg.value.trim();
  if(!text) return;
  msg.value="";
  add("Você: "+text);

  try {{
    setBusy(true);
    const payload={{session_id:sessionId,message:text,state:state}};
    const d=await fetchJson("/chat",{{method:"POST",headers:{{"Content-Type":"application/json","x-demo-key":DEMO_KEY}},body:JSON.stringify(payload)}});
    state=d.state||state;
    add("IA: "+d.message);

    if(d.report_docx_b64){{ b1=d.report_docx_b64; n1=d.report_docx_filename; }}
    if(d.proposal_docx_b64){{ b2=d.proposal_docx_b64; n2=d.proposal_docx_filename; }}
    if(d.piece_docx_b64){{ b3=d.piece_docx_b64; n3=d.piece_docx_filename; }}
    if(b1&&b2&&b3) enableDl(true);

  }} catch(e) {{
    add("⚠️ Erro: " + e.message);
  }} finally {{
    setBusy(false);
  }}
}}
send.onclick = doSend;

// ENTER envia
msg.addEventListener("keydown",(e)=>{{
  if(e.key==="Enter") {{
    e.preventDefault();
    doSend();
  }}
}});

dl1.onclick=()=>{{ if(b1) downloadDocx(b1,n1); }};
dl2.onclick=()=>{{ if(b2) downloadDocx(b2,n2); }};
dl3.onclick=()=>{{ if(b3) downloadDocx(b3,n3); }};

add("Cole o DEMO_KEY e clique em Ativar.");
</script>
</body>
</html>
"""

@app.get("/widget", response_class=HTMLResponse)
def widget():
    return HTMLResponse(WIDGET_HTML)
