import os
import uuid
import json
import base64
import re
from io import BytesIO
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple

import openai
from openai import OpenAI

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH


# =========================================================
# CONFIG
# =========================================================
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://correamendes.wpcomstaging.com")
DEMO_KEY = os.getenv("DEMO_KEY", "").strip()
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# Proposta defaults (fallback)
FEE_ENTRADA_DEFAULT = int(os.getenv("FEE_ENTRADA", "5000"))
FEE_SALDO_DEFAULT = int(os.getenv("FEE_SALDO", "20000"))
FEE_PARCELAS_DEFAULT = int(os.getenv("FEE_PARCELAS", "10"))
MANDATARIA_NOME = os.getenv("MANDATARIA_NOME", "Dra. Ester Cristina Salles Mendes")
MANDATARIA_OAB = os.getenv("MANDATARIA_OAB", "OAB/SP 105.488")

# Honorários IA: límites (para no salir absurdo)
FEE_MIN_TOTAL = int(os.getenv("FEE_MIN_TOTAL", "1500"))
FEE_MAX_TOTAL = int(os.getenv("FEE_MAX_TOTAL", "250000"))
FEE_DEFAULT_PARCELAS = int(os.getenv("FEE_DEFAULT_PARCELAS", "10"))
FEE_VALIDITY_DAYS = int(os.getenv("FEE_VALIDITY_DAYS", "7"))

TIPOS_PECA = [
    "Notificação Extrajudicial",
    "Petição Inicial",
    "Contestação",
    "Réplica",
    "Recurso",
    "Minuta de Acordo",
    "Petição Intermediária (Manifestação)",
]

# Upload limits
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "7"))
MAX_FILES_PER_SESSION = int(os.getenv("MAX_FILES_PER_SESSION", "10"))
MAX_TOTAL_MB_PER_SESSION = int(os.getenv("MAX_TOTAL_MB_PER_SESSION", "20"))


# =========================================================
# APP
# =========================================================
app = FastAPI(title="S&M OS 6.1 — Demo Backend", version="0.8.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# IN-MEMORY UPLOAD STORE (Render: ephemeral; ok para demo)
# =========================================================
UPLOADS: Dict[str, List[Dict[str, Any]]] = {}  # session_id -> list(files)


# =========================================================
# INTAKE
# =========================================================
FIELDS_ORDER = [
    ("area_subarea", "Qual a área/subárea? (ex.: cível/consumidor/indenizatória)"),
    ("fase", "Qual a fase? (consultivo / pré-contencioso / processo / recurso / execução)"),
    ("objetivo_cliente", "Qual o objetivo do cliente? (o que ele quer obter)"),
    ("partes", "Quem são as partes? (autor/réu e relação entre eles)"),
    ("contratante_nome", "Qual o nome completo do Contratante/Recebedor para a Proposta de Honorários?"),
    ("tipo_peca", "Qual peça você precisa gerar? (selecione uma opção)"),
    ("fatos_cronologia", "Conte os fatos em ordem (inclua: afastamento, CAT, demissão, retorno, etc. se houver)."),
    ("provas_existentes", "Quais provas/documentos você já tem? (liste) — Você também pode subir arquivos agora."),
    ("urgencia_prazo", "Há urgência ou prazo crítico? (qual?)"),
    ("valor_envovido", "Qual o valor envolvido/impacto? (se não souber, estimativa)"),
    ("notas_adicionais", "Alguma informação adicional relevante? (ex.: foi demitido após o evento, houve INSS, etc.)"),
]
REQUIRED_FIELDS = [k for k, _ in FIELDS_ORDER]


# =========================================================
# OS 6.1 (BASE) + CONTRATO JSON
# =========================================================
OS_6_1_PROMPT = r"""
SALLES & MENDES OS 6.1 — SISTEMA OPERACIONAL JURÍDICO ESCALÁVEL
(VOLUME + ESTRATÉGICO + CONSULTIVO + PRODUTO)
(…cole o seu OS 6.1 completo aqui…)
"""

OUTPUT_CONTRACT = r"""
CONTRATO DE SAÍDA (OBRIGATÓRIO)
- Retorne APENAS JSON (sem markdown).
- Não invente fatos: se não estiver no intake, use [PREENCHER] e/ou "CONDICIONAL:".
- estrategia_18_pontos: lista com EXATAMENTE 18 itens.
- tipo_peca deve ecoar exatamente o escolhido.
- minuta_peca deve iniciar com "Copie e cole no timbrado do seu escritório antes de finalizar."
- secoes: objeto com as 18 chaves do OS (não devolver tudo como "—").
"""

SYSTEM_OS_JSON = OS_6_1_PROMPT + "\n\n" + OUTPUT_CONTRACT


# =========================================================
# HELPERS
# =========================================================
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

def friendly_openai_error(e: Exception) -> HTTPException:
    if isinstance(e, openai.RateLimitError):
        return HTTPException(status_code=429, detail="Rate limit/quota. Verifique Billing/Créditos.")
    if isinstance(e, openai.AuthenticationError):
        return HTTPException(status_code=401, detail="OPENAI_API_KEY inválida.")
    return HTTPException(status_code=500, detail=f"Erro OpenAI: {type(e).__name__}: {str(e)}")

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

def add_list_bullets(doc: Document, items: List[str]):
    try:
        for it in items:
            doc.add_paragraph(str(it), style="List Bullet")
    except Exception:
        for it in items:
            doc.add_paragraph(f"• {it}")

def normalize_points(raw: Any) -> List[str]:
    items: List[str] = []
    if raw is None:
        items = []
    elif isinstance(raw, list):
        items = [str(x).strip() for x in raw if str(x).strip()]
    elif isinstance(raw, str):
        s = raw.strip()
        lines = [x.strip() for x in s.splitlines() if x.strip()]
        if len(lines) <= 1 and ";" in s:
            lines = [x.strip() for x in s.split(";") if x.strip()]
        items = lines
    else:
        items = [str(raw).strip()]

    cleaned = []
    for it in items:
        it2 = re.sub(r"^\s*[\-\•]\s*", "", it)
        it2 = re.sub(r"^\s*\d+\s*[\)\.\-:]\s*", "", it2).strip()
        if it2:
            cleaned.append(it2)
    return cleaned

def force_18(items: List[str]) -> List[str]:
    items = [x.strip() for x in items if x.strip()]
    if len(items) > 18:
        return items[:18]
    if len(items) < 18:
        for i in range(len(items) + 1, 19):
            items.append(
                f"CONDICIONAL: Completar o ponto {i} após validar pendências críticas (prova mínima, prazo, objeto e narrativa adversa)."
            )
    return items

def repair_18_points_with_model(client: OpenAI, items: List[str]) -> List[str]:
    try:
        repair_system = (
            "Retorne APENAS JSON: {'estrategia_18_pontos':[...]} com EXATAMENTE 18 itens. "
            "Use os itens fornecidos; se faltar, complete com 'CONDICIONAL:' sem inventar fatos."
        )
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": repair_system},
                {"role": "user", "content": json.dumps({"estrategia_18_pontos": items}, ensure_ascii=False)},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        fixed = force_18(normalize_points(data.get("estrategia_18_pontos")))
        return fixed
    except Exception:
        return force_18(items)

def count_empty_sections(secoes: Dict[str, Any]) -> int:
    keys = [
        "1_CLASSIFICACAO","2_SINTESE","3_QUESTAO_JURIDICA","4_ANALISE_TECNICA","5_FORCA_DA_TESE","6_CONFIABILIDADE",
        "7_PROVAS","8_RISCOS","9_CENARIOS","10_ANALISE_ECONOMICA","11_RENTABILIDADE","12_SCORES","13_RED_TEAM",
        "14_ESTRATEGIA","15_ACOES_PRIORITARIAS","16_PENDENCIAS","17_ALERTAS","18_REFLEXAO_FINAL"
    ]
    empty = 0
    for k in keys:
        v = secoes.get(k)
        if v is None:
            empty += 1
            continue
        s = str(v).strip()
        if s == "" or s == "—" or s == "-":
            empty += 1
    return empty

def repair_sections_with_model(client: OpenAI, state: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    prompt = (
        "Você deve retornar APENAS JSON com a chave 'secoes' contendo as 18 chaves do OS. "
        "Preencha com base estrita no intake (sem inventar fatos). "
        "Se faltar dado: use 'CONDICIONAL:' e '[PREENCHER]'. "
        "Não devolver tudo como '—'."
    )
    payload = {
        "intake": state,
        "estrategia_18_pontos": report.get("estrategia_18_pontos", []),
        "tipo_peca": state.get("tipo_peca"),
    }
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role":"system","content":prompt},{"role":"user","content":json.dumps(payload, ensure_ascii=False)}],
        temperature=0.2,
        response_format={"type":"json_object"},
    )
    data = json.loads(resp.choices[0].message.content)
    return data.get("secoes", {}) if isinstance(data.get("secoes"), dict) else {}

def sanitize_minuta(minuta: str, state: Dict[str, Any]) -> str:
    """
    Anti-alucinação: si aparecen hechos sensibles no presentes en intake -> los vuelve [PREENCHER].
    """
    intake_text = " ".join([str(v) for v in state.values() if v]).lower()

    rules: List[Tuple[str, str]] = [
        ("demitid", "[PREENCHER: confirmar se houve demissão e em que condições]"),
        ("sem justa causa", "[PREENCHER: confirmar modalidade de desligamento]"),
        ("cat", "[PREENCHER: confirmar se houve emissão de CAT]"),
        ("inss", "[PREENCHER: confirmar se houve benefício INSS/afastamento]"),
        ("afast", "[PREENCHER: confirmar período de afastamento]"),
        ("cirurg", "[PREENCHER: confirmar se houve cirurgia e laudos]"),
        ("fratur", "[PREENCHER: confirmar diagnóstico (CID) e laudos]"),
    ]

    out = minuta
    for needle, repl in rules:
        if needle in out.lower() and needle not in intake_text:
            # reemplaza frases que contengan el needle
            out = re.sub(rf"([^.]*\b{needle}\b[^.]*\.)", repl + "\n", out, flags=re.IGNORECASE)

    return out


# =========================================================
# Upload extraction (simple)
# =========================================================
def extract_text_from_upload(filename: str, mime: str, b64: str) -> str:
    try:
        raw = base64.b64decode(b64)
        low = filename.lower()

        if low.endswith(".txt"):
            return raw.decode("utf-8", errors="ignore")[:4000]

        if low.endswith(".docx"):
            d = Document(BytesIO(raw))
            txt = "\n".join([p.text for p in d.paragraphs if p.text.strip()])
            return txt[:4000]

        # pdf/images: no extraemos en esta versión (se puede agregar pypdf o vision luego)
        return ""
    except Exception:
        return ""


# =========================================================
# IA: honorários por caso (IA)
# =========================================================
def generate_fee_json(client: OpenAI, state: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    prompt = f"""
Você é um assistente de precificação de honorários advocatícios (Brasil).
Objetivo: sugerir valores JUSTOS e defensáveis (sem promessa de êxito).
Respeite: tabela mínima OAB como referência (se não tiver, seja conservador).
Saída: APENAS JSON com:
- total
- entrada
- saldo
- parcelas (int)
- justificativa_curta (3-6 linhas)
- observacoes (lista curta)

Limites:
- total >= {FEE_MIN_TOTAL} e <= {FEE_MAX_TOTAL}
- entrada entre 20% e 40% do total (salvo urgência alta)
- parcelas entre 1 e 12

Baseie-se em:
- fase: {state.get('fase')}
- tipo_peca: {state.get('tipo_peca')}
- área/subárea: {state.get('area_subarea')}
- valor_envovido: {state.get('valor_envovido')}
- risco_improcedencia: {report.get('risco_improcedencia')}
- forca_tese: {report.get('forca_tese')}
- confiabilidade: {report.get('confiabilidade_analise')}
- urgencia: {state.get('urgencia_prazo')}
- provas: {state.get('provas_existentes')}
"""
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role":"system","content":prompt},{"role":"user","content":"Gere a sugestão."}],
        temperature=0.2,
        response_format={"type":"json_object"},
    )
    data = json.loads(resp.choices[0].message.content)

    # sane defaults
    total = int(max(FEE_MIN_TOTAL, min(FEE_MAX_TOTAL, int(float(data.get("total", FEE_ENTRADA_DEFAULT + FEE_SALDO_DEFAULT))))))
    parcelas = int(data.get("parcelas", FEE_DEFAULT_PARCELAS))
    parcelas = max(1, min(12, parcelas))

    entrada = int(float(data.get("entrada", int(total * 0.25))))
    entrada = max(int(total * 0.2), min(int(total * 0.4), entrada))

    saldo = total - entrada

    return {
        "total": total,
        "entrada": entrada,
        "saldo": saldo,
        "parcelas": parcelas,
        "justificativa_curta": str(data.get("justificativa_curta", "")).strip()[:1000],
        "observacoes": data.get("observacoes", []) if isinstance(data.get("observacoes", []), list) else []
    }


# =========================================================
# IA: reporte JSON completo
# =========================================================
def generate_report_json(state: Dict[str, Any]) -> Dict[str, Any]:
    client = get_client()

    files = UPLOADS.get(state.get("_session_id", ""), [])
    file_list = [{"name": f["filename"], "mime": f["mime"], "text_excerpt": f.get("text_excerpt","")[:700]} for f in files]

    user_case = {
        "intake": state,
        "uploads": file_list
    }

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role":"system","content":SYSTEM_OS_JSON},
                {"role":"user","content":json.dumps(user_case, ensure_ascii=False)}
            ],
            temperature=TEMPERATURE,
            response_format={"type":"json_object"},
        )
        data = json.loads(resp.choices[0].message.content)

        # 18 puntos
        pts = normalize_points(data.get("estrategia_18_pontos"))
        if len(pts) != 18:
            pts = repair_18_points_with_model(client, pts)
        data["estrategia_18_pontos"] = force_18(pts)

        # tipo_peca
        if data.get("tipo_peca") and data.get("tipo_peca") != state.get("tipo_peca"):
            data["tipo_peca"] = state.get("tipo_peca")

        # minuta (timbrado + sanitizar)
        minuta = str(data.get("minuta_peca","")).strip()
        if not minuta.lower().startswith("copie e cole no timbrado"):
            minuta = "Copie e cole no timbrado do seu escritório antes de finalizar.\n\n" + minuta
        minuta = sanitize_minuta(minuta, state)
        data["minuta_peca"] = minuta

        # seções: si vienen vacías, regenerar
        if not isinstance(data.get("secoes"), dict):
            data["secoes"] = {}
        if count_empty_sections(data["secoes"]) >= 10:
            data["secoes"] = repair_sections_with_model(client, state, data)

        return data

    except Exception as e:
        raise friendly_openai_error(e)


# =========================================================
# DOCX Builders
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

    doc.add_paragraph("")
    add_h(doc, "Classificações técnicas", 13)
    add_p(doc, f"Força da tese: {report.get('forca_tese','—')}")
    add_p(doc, f"Confiabilidade da análise: {report.get('confiabilidade_analise','—')}")
    add_p(doc, f"Risco de improcedência: {report.get('risco_improcedencia','—')}")
    add_p(doc, f"Suficiência de dados: {report.get('suficiencia_dados','—')}")
    add_p(doc, f"Status: {report.get('status','—')}")
    add_p(doc, f"Modo operacional detectado: {report.get('modo_operacional_detectado','—')}")

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
    for title, key in order:
        add_h(doc, title, 12)
        body = secoes.get(key, "CONDICIONAL: seção não preenchida — rever intake.")
        if isinstance(body, list):
            add_list_bullets(doc, [str(x) for x in body])
        else:
            add_p(doc, str(body))

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

    contratante = state.get("contratante_nome") or "________________________________________"
    objeto_curto = f"Atuação no caso informado (Área: {state.get('area_subarea','—')})."
    validade = (datetime.now().date()).strftime("%d/%m/%Y")
    validade_ate = (datetime.now().date()).strftime("%d/%m/%Y")

    total = int(fee.get("total", FEE_ENTRADA_DEFAULT + FEE_SALDO_DEFAULT))
    entrada = int(fee.get("entrada", FEE_ENTRADA_DEFAULT))
    saldo = int(fee.get("saldo", FEE_SALDO_DEFAULT))
    parcelas = int(fee.get("parcelas", FEE_PARCELAS_DEFAULT))
    parcela_val = int(saldo / max(parcelas, 1))

    t1 = doc.add_table(rows=6, cols=2)
    t1.style = "Table Grid"
    t1.cell(0, 0).text = "Contratante / Recebedor"
    t1.cell(0, 1).text = str(contratante)
    t1.cell(1, 0).text = "Mandatária"
    t1.cell(1, 1).text = f"{MANDATARIA_NOME} — {MANDATARIA_OAB}"
    t1.cell(2, 0).text = "Objeto"
    t1.cell(2, 1).text = objeto_curto
    t1.cell(3, 0).text = "Data"
    t1.cell(3, 1).text = datetime.now().strftime("%d/%m/%Y")
    t1.cell(4, 0).text = "Validade da proposta"
    t1.cell(4, 1).text = f"{FEE_VALIDITY_DAYS} dias"
    t1.cell(5, 0).text = "Observação"
    t1.cell(5, 1).text = "Obrigação de meio. Sem promessa de êxito."

    doc.add_paragraph("")
    add_h(doc, "1. Escopo dos serviços", 13)
    escopo = [
        "Análise técnica dos fatos e documentos informados.",
        "Definição de estratégia jurídica (principal e subsidiária).",
        "Elaboração de peças/manifestações cabíveis dentro do objeto contratado.",
        "Acompanhamento e orientação estratégica durante o trâmite (conforme contratado).",
    ]
    add_list_bullets(doc, escopo)

    doc.add_paragraph("")
    add_h(doc, "2. Honorários (sugestão por caso)", 13)
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

    obs = fee.get("observacoes", [])
    if isinstance(obs, list) and obs:
        doc.add_paragraph("")
        add_h(doc, "Observações", 13)
        add_list_bullets(doc, [str(x) for x in obs])

    doc.add_paragraph("")
    add_h(doc, "3. Condições e limites", 13)
    cond = [
        "Não inclui custas, taxas, perícias, emolumentos, diligências, deslocamentos e despesas externas.",
        "Obrigação de meio, sem garantia de êxito ou promessa de resultado.",
        "Se surgir demanda autônoma fora do objeto, será feito orçamento complementar.",
        "Em caso de inadimplemento, poderá haver suspensão de atos não urgentes até regularização (salvo deveres éticos)."
    ]
    add_list_bullets(doc, cond)

    doc.add_paragraph("")
    add_p(doc, f"{MANDATARIA_NOME} — {MANDATARIA_OAB}")
    doc.add_paragraph("")
    add_p(doc, "Aceite do cliente: ______________________________________________")
    add_p(doc, str(contratante))
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
    foot = doc.add_paragraph("Nota: minuta assistiva. Proibido inventar fatos/provas. Ajuste [PREENCHER] antes de assinar/protocolar.")
    foot.runs[0].italic = True
    return doc


# =========================================================
# MODELS
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
    stored: bool = True


# =========================================================
# API
# =========================================================
@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "sm-os-demo",
        "version": "0.8.0",
        "has_openai_key": bool(OPENAI_API_KEY),
        "allowed_origin": ALLOWED_ORIGIN,
        "model": MODEL,
        "max_file_mb": MAX_FILE_MB,
        "max_files_per_session": MAX_FILES_PER_SESSION,
    }

@app.post("/session/new", response_model=SessionOut)
def session_new(x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = str(uuid.uuid4())
    UPLOADS[sid] = []
    return SessionOut(
        session_id=sid,
        message="Vamos iniciar o diagnóstico.\n\n" + FIELDS_ORDER[0][1],
        state={"_session_id": sid}
    )

@app.post("/upload", response_model=UploadOut)
def upload_file(inp: UploadIn, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = inp.session_id
    if not sid:
        raise HTTPException(status_code=400, detail="session_id requerido")

    if sid not in UPLOADS:
        UPLOADS[sid] = []

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
        "text_excerpt": text_excerpt
    })
    return UploadOut(file_id=file_id, filename=inp.filename, size_bytes=size)

@app.post("/chat", response_model=ChatOut)
def chat(inp: ChatIn, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    state = inp.state or {}
    sid = state.get("_session_id") or inp.session_id
    state["_session_id"] = sid

    # Captura respuesta en el primer campo faltante
    for key, _question in FIELDS_ORDER:
        if not state.get(key):
            val = (inp.message or "").strip()
            if key == "tipo_peca" and val not in TIPOS_PECA:
                raise HTTPException(status_code=400, detail="Tipo de peça inválido. Selecione uma opção.")
            state[key] = val
            break

    # Cuando estamos en la etapa de "provas", anexamos lista de archivos (si hay)
    if state.get("provas_existentes") and sid in UPLOADS:
        state["provas_arquivos"] = [f["filename"] for f in UPLOADS[sid]]

    if not is_sufficient(state):
        return ChatOut(message=next_missing(state), state=state)

    # Genera reporte
    report = generate_report_json(state)

    # Genera honorários por IA
    client = get_client()
    try:
        fee = generate_fee_json(client, state, report)
    except Exception:
        # fallback si falla
        fee = {
            "total": FEE_ENTRADA_DEFAULT + FEE_SALDO_DEFAULT,
            "entrada": FEE_ENTRADA_DEFAULT,
            "saldo": FEE_SALDO_DEFAULT,
            "parcelas": FEE_PARCELAS_DEFAULT,
            "justificativa_curta": "Fallback: valores padrão por indisponibilidade do módulo de precificação.",
            "observacoes": []
        }

    # DOCX
    doc_report = build_report_strategy_docx(report, state)
    doc_prop = build_proposal_docx(state, fee)
    doc_piece = build_piece_docx(report, state)

    ts = datetime.now().strftime("%Y%m%d-%H%M")
    tipo_safe = state.get("tipo_peca", "Peca").replace(" ", "_").replace("/", "_")

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
# WIDGET (con upload)
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
    --panel: rgba(15,17,26,.62);
    --panel2: rgba(11,13,18,.55);
    --text:#eef1f7;
    --muted:rgba(238,241,247,.72);
    --gold:#f5c451;
    --line:rgba(255,255,255,.12);
    --line2:rgba(245,196,81,.22);
    --radius:18px;
  }}
  *{{box-sizing:border-box}}
  html, body {{ height:100%; }}
  body{{ margin:0; background: transparent !important; color: var(--text);
    font-family: system-ui, -apple-system, Segoe UI, Inter, Arial; }}
  .shell{{ height:100%; display:flex; flex-direction:column; gap:10px; min-height:0; }}
  .head{{ padding: 12px 14px; background: var(--panel); border: 1px solid var(--line);
    border-radius: var(--radius); backdrop-filter: blur(10px);
    display:flex; align-items:center; justify-content:space-between; gap:12px; flex:0 0 auto; }}
  .brand{{display:flex; align-items:center; gap:10px; min-width:0}}
  .logo{{ width:34px;height:34px;border-radius:12px; display:grid;place-items:center;
    font-weight:900; color: rgba(245,196,81,.95); background: rgba(245,196,81,.12);
    border: 1px solid var(--line2); }}
  .title{{font-weight:900; font-size:14px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}}
  .sub{{margin-top:3px; font-size:12px; color:var(--muted)}}
  .pills{{display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end}}
  .pill{{ font-size:12px; padding:7px 10px; border-radius:999px; border:1px solid var(--line2);
    background: rgba(245,196,81,.10); color: rgba(245,196,81,.95); }}
  .grid{{ flex:1; display:grid; grid-template-columns: 1.2fr .8fr; gap: 10px; min-height: 0; }}
  @media (max-width: 980px){{ .grid{{ grid-template-columns: 1fr; }} .side{{ display:none; }} }}
  .chat{{ display:flex; flex-direction:column; min-height:0; gap:10px; }}
  .activation{{ display:flex; gap:10px; align-items:center; padding:12px 14px; border-radius: var(--radius);
    background: var(--panel2); border:1px solid var(--line); backdrop-filter: blur(10px); }}
  .badge{{ font-size:12px; padding:6px 10px; border-radius:999px; border:1px solid var(--line2);
    background: rgba(245,196,81,.10); color: rgba(245,196,81,.95); white-space:nowrap; }}
  .key{{ flex:1; padding:12px; border-radius:12px; border:1px solid rgba(255,255,255,.16);
    background: rgba(0,0,0,.25); color: var(--text); outline:none; }}
  .btn{{ padding:12px 14px; border-radius:12px; border:1px solid rgba(245,196,81,.35);
    background: linear-gradient(180deg, rgba(245,196,81,.95), rgba(201,146,28,.95));
    font-weight:900; cursor:pointer; color:#1a1204; }}
  .btn2{{ padding:12px 14px; border-radius:12px; border:1px solid rgba(255,255,255,.18);
    background: rgba(255,255,255,.06); color: var(--text); font-weight:900; cursor:pointer; }}
  .progress{{ display:flex; align-items:center; gap:10px; padding:10px 14px; border-radius: var(--radius);
    background: var(--panel2); border:1px solid var(--line); backdrop-filter: blur(10px); }}
  .bar{{ height:8px; border-radius:999px; background: rgba(255,255,255,.10); overflow:hidden; flex:1; }}
  .bar > div{{ height:100%; width:0%; background: linear-gradient(90deg, rgba(245,196,81,.95), rgba(245,196,81,.25)); transition: width .25s ease; }}
  .step{{font-size:12.5px; color:var(--muted); white-space:nowrap}}
  #chatLog{{ flex:1; min-height:0; overflow:auto; padding:14px; border-radius: var(--radius);
    background: rgba(0,0,0,.18); border:1px solid rgba(255,255,255,.10); backdrop-filter: blur(6px); }}
  .msgWrap{{margin-bottom:12px;display:flex}}
  .msgWrap.user{{justify-content:flex-end}}
  .bubble{{ max-width:78%; padding:12px; border-radius:14px; white-space:pre-wrap; line-height:1.45; font-size:14px; }}
  .bot .bubble{{ background: rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.12); }}
  .user .bubble{{ background: rgba(245,196,81,.16); border:1px solid rgba(245,196,81,.22); }}
  .notice{{ margin:10px 0; padding:10px 12px; border-radius:14px; border:1px solid rgba(255,255,255,.12);
    background: rgba(255,255,255,.06); color: rgba(255,255,255,.86); font-size:13px; }}
  .err{{ border-color: rgba(255,112,112,.25); background: rgba(255,112,112,.10); color:#ffd6d6; }}
  .ok{{ border-color: rgba(122,255,170,.25); background: rgba(122,255,170,.10); color:#d8ffe8; }}
  .choices{{ display:none; gap:8px; flex-wrap:wrap; padding: 0 2px; margin-top:-2px; margin-bottom:2px; }}
  .choiceBtn{{ padding:10px 12px; border-radius:12px; border:1px solid rgba(245,196,81,.22);
    background: rgba(245,196,81,.10); color: rgba(245,196,81,.95); font-weight:900; cursor:pointer; font-size:12.5px; }}
  .row{{ display:flex; gap:10px; padding:12px 14px; border-radius: var(--radius);
    background: var(--panel2); border:1px solid var(--line); backdrop-filter: blur(10px); align-items:center; }}
  .input{{ flex:1; padding:12px; border-radius:12px; border:1px solid rgba(255,255,255,.16);
    background: rgba(0,0,0,.25); color: var(--text); outline:none; }}
  .side{{ display:flex; flex-direction:column; gap:10px; min-height:0; }}
  .card{{ border-radius: var(--radius); background: var(--panel); border:1px solid var(--line);
    backdrop-filter: blur(10px); padding:14px; }}
  .card h3{{ margin:0 0 10px 0; font-size:13px; color: rgba(245,196,81,.95); }}
  .kv{{ display:grid; grid-template-columns: 1fr; gap:8px; font-size:13px; color: rgba(255,255,255,.82); }}
  .kv b{{ color: rgba(255,255,255,.92); }}
  .actions{{display:flex; gap:10px; flex-wrap:wrap; margin-top:10px}}
  .smallbtn{{ padding:10px 12px; border-radius:12px; border:1px solid rgba(255,255,255,.18);
    background: rgba(255,255,255,.06); color: var(--text); font-weight:900; cursor:pointer; font-size:12.5px; }}

  .uploadBox{{ display:none; gap:10px; align-items:center; flex-wrap:wrap; padding:12px 14px; border-radius: var(--radius);
    background: var(--panel2); border:1px solid var(--line); backdrop-filter: blur(10px); }}
  .fileList{{ font-size:12px; color:var(--muted); }}
</style>
</head>
<body>
<div class="shell">
  <div class="head">
    <div class="brand">
      <div class="logo">S&M</div>
      <div>
        <div class="title">Diagnóstico Jurídico Inteligente</div>
        <div class="sub">3 DOCX: Relatório+Estratégia(18) • Proposta dinâmica • Peça (timbrado)</div>
      </div>
    </div>
    <div class="pills">
      <span class="pill">DEMO</span>
      <span class="pill" id="statusPill">Status: pronto</span>
    </div>
  </div>

  <div class="grid">
    <div class="chat">
      <div class="activation">
        <span class="badge">Ativação</span>
        <input class="key" id="keyInput" placeholder="Cole aqui o DEMO_KEY"/>
        <button class="btn" id="keyBtn">Ativar</button>
        <button class="btn2" id="resetBtn">Reiniciar</button>
      </div>

      <div class="progress">
        <div class="bar"><div id="barFill"></div></div>
        <div class="step" id="stepText">Etapa 0/{len(REQUIRED_FIELDS)}</div>
      </div>

      <div id="chatLog"></div>

      <div class="choices" id="choices"></div>

      <div class="uploadBox" id="uploadBox">
        <span class="badge">Provas</span>
        <input type="file" id="fileInput" multiple />
        <button class="btn2" id="uploadBtn">Subir</button>
        <div class="fileList" id="fileList"></div>
      </div>

      <div class="row">
        <input class="input" id="chatInput" placeholder="Digite aqui..." disabled />
        <button class="btn" id="chatSend" disabled>Enviar</button>
      </div>
    </div>

    <div class="side">
      <div class="card">
        <h3>Downloads</h3>
        <div class="actions">
          <button class="smallbtn" id="dlReportBtn" disabled>Baixar Relatório+Estratégia .docx</button>
          <button class="smallbtn" id="dlPropBtn" disabled>Baixar Proposta .docx</button>
          <button class="smallbtn" id="dlPieceBtn" disabled>Baixar Peça .docx</button>
        </div>
      </div>
      <div class="card">
        <h3>Dados capturados</h3>
        <div class="kv" id="kv"></div>
      </div>
    </div>
  </div>
</div>

<script>
const STORE_KEY="sm_os_demo_key";

const fieldLabels = {{
  area_subarea:"Área/Subárea",
  fase:"Fase",
  objetivo_cliente:"Objetivo",
  partes:"Partes",
  contratante_nome:"Contratante/Recebedor",
  tipo_peca:"Tipo de peça",
  fatos_cronologia:"Fatos",
  provas_existentes:"Provas (texto)",
  urgencia_prazo:"Urgência/Prazo",
  valor_envovido:"Valor/Impacto",
  notas_adicionais:"Notas adicionais"
}};
const fieldOrder = Object.keys(fieldLabels);

const PIECE_OPTIONS = {json.dumps(TIPOS_PECA)};

let DEMO_KEY = localStorage.getItem(STORE_KEY) || "";
let sessionId = null;
let state = {{}};

let b64Report=null, nameReport=null;
let b64Prop=null, nameProp=null;
let b64Piece=null, namePiece=null;

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
const choices = document.getElementById("choices");

const uploadBox = document.getElementById("uploadBox");
const fileInput = document.getElementById("fileInput");
const uploadBtn = document.getElementById("uploadBtn");
const fileList = document.getElementById("fileList");

const dlReportBtn = document.getElementById("dlReportBtn");
const dlPropBtn = document.getElementById("dlPropBtn");
const dlPieceBtn = document.getElementById("dlPieceBtn");

keyInput.value = DEMO_KEY;

function setStatus(text){{ statusPill.textContent = "Status: " + text; }}

function progress(){{
  let filled=0;
  for(const k of fieldOrder) if(state && state[k]) filled++;
  const pct = Math.round((filled / fieldOrder.length) * 100);
  barFill.style.width = pct + "%";
  stepText.textContent = "Etapa " + filled + "/" + fieldOrder.length;
}}

function escapeHtml(s){{ return s.replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;"); }}

function renderKV(){{
  kv.innerHTML="";
  for(const k of fieldOrder){{
    const v = (state && state[k]) ? state[k] : "—";
    const div = document.createElement("div");
    div.innerHTML = "<b>"+fieldLabels[k]+":</b><br/>"+escapeHtml(String(v)).slice(0, 260);
    kv.appendChild(div);
  }}
  progress();
}}

function addMsg(role, text){{
  const wrap=document.createElement("div");
  wrap.className="msgWrap "+(role==="user"?"user":"bot");
  const b=document.createElement("div");
  b.className="bubble";
  b.textContent=text;
  wrap.appendChild(b);
  log.appendChild(wrap);
  log.scrollTop=log.scrollHeight;

  // Mostrar upload cuando el bot pregunta por provas/documentos
  if(role==="bot" && text.toLowerCase().includes("provas/documentos")) {{
    uploadBox.style.display="flex";
  }}
}}

function addNotice(text, type="") {{
  const div=document.createElement("div");
  div.className="notice "+type;
  div.textContent=text;
  log.appendChild(div);
  log.scrollTop=log.scrollHeight;
}}

async function fetchJson(url, options){{
  const res=await fetch(url, options);
  let data={{}};
  try{{ data=await res.json(); }}catch(e){{}}
  if(!res.ok) throw new Error(data.detail || data.message || ("HTTP "+res.status));
  return data;
}}

function setReady(ready){{
  input.disabled=!ready;
  btn.disabled=!ready;
}}

function enableDownloads(enable){{
  dlReportBtn.disabled=!enable;
  dlPropBtn.disabled=!enable;
  dlPieceBtn.disabled=!enable;
}}

function clearDownloads(){{
  b64Report=b64Prop=b64Piece=null;
  nameReport=nameProp=namePiece=null;
  enableDownloads(false);
}}

function downloadDocx(b64, filename){{
  const binary=atob(b64);
  const bytes=new Uint8Array(binary.length);
  for(let i=0;i<binary.length;i++) bytes[i]=binary.charCodeAt(i);
  const blob=new Blob([bytes],{{type:"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}});
  const url=URL.createObjectURL(blob);
  const a=document.createElement("a");
  a.href=url; a.download=filename||"arquivo.docx";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}}

function showPieceChoices(show){{
  choices.style.display=show?"flex":"none";
  if(!show){{ choices.innerHTML=""; return; }}
  choices.innerHTML="";
  for(const opt of PIECE_OPTIONS){{
    const b=document.createElement("button");
    b.className="choiceBtn";
    b.textContent=opt;
    b.addEventListener("click",()=>{{ input.value=opt; send(); }});
    choices.appendChild(b);
  }}
}}

async function startSession(){{
  if(!DEMO_KEY){{ addNotice("Cole o DEMO_KEY e clique em Ativar.","err"); return; }}
  setReady(false); setStatus("iniciando");
  addNotice("⏳ Iniciando sessão…");
  clearDownloads(); choices.innerHTML=""; choices.style.display="none";
  uploadBox.style.display="none"; fileList.textContent="";

  const data = await fetchJson("/session/new", {{
    method:"POST",
    headers:{{"x-demo-key":DEMO_KEY}}
  }});
  sessionId=data.session_id;
  state=data.state||{{}};
  renderKV();
  addMsg("bot", data.message);
  setReady(true); setStatus("ativo");
  input.focus();
}}

async function send(){{
  const text=input.value.trim();
  if(!text) return;
  input.value="";
  addMsg("user", text);
  showPieceChoices(false);

  setReady(false); setStatus("processando");
  addNotice("⏳ Processando…");

  try {{
    const payload={{session_id:sessionId||"local", message:text, state:state||{{}}}};
    const data=await fetchJson("/chat", {{
      method:"POST",
      headers:{{"Content-Type":"application/json","x-demo-key":DEMO_KEY}},
      body:JSON.stringify(payload)
    }});
    state=data.state||state;
    renderKV();
    addMsg("bot", data.message||"(sem mensagem)");

    if((data.message||"").toLowerCase().includes("qual peça você precisa gerar")) {{
      showPieceChoices(true);
    }}

    if(data.report_docx_b64 && data.proposal_docx_b64 && data.piece_docx_b64){{
      b64Report=data.report_docx_b64; nameReport=data.report_docx_filename;
      b64Prop=data.proposal_docx_b64; nameProp=data.proposal_docx_filename;
      b64Piece=data.piece_docx_b64; namePiece=data.piece_docx_filename;
      enableDownloads(true);
      addNotice("✅ 3 DOCX prontos.","ok");
    }}

    setReady(true); setStatus("ativo");
  }} catch(err) {{
    clearDownloads();
    addNotice("⚠️ Falha: "+err.message, "err");
    setStatus("erro");
    setReady(false);
  }}
}}

async function fileToB64(file){{
  return new Promise((resolve,reject)=>{{
    const r=new FileReader();
    r.onload=()=> {{
      const s = r.result;
      // s = data:mime;base64,XXXX
      resolve(String(s).split(",")[1]);
    }};
    r.onerror=reject;
    r.readAsDataURL(file);
  }});
}}

uploadBtn.addEventListener("click", async ()=>{{
  if(!sessionId){{ addNotice("Ative a sessão antes de subir arquivos.","err"); return; }}
  const files=[...fileInput.files];
  if(!files.length) return;

  addNotice("⏳ Subindo arquivos…");
  for(const f of files){{
    const b64 = await fileToB64(f);
    const payload={{session_id:sessionId, filename:f.name, mime:f.type||"application/octet-stream", b64:b64}};
    try {{
      const out = await fetchJson("/upload", {{
        method:"POST",
        headers:{{"Content-Type":"application/json","x-demo-key":DEMO_KEY}},
        body:JSON.stringify(payload)
      }});
      fileList.textContent += "✅ " + out.filename + " (" + out.size_bytes + " bytes)\\n";
    }} catch(err) {{
      fileList.textContent += "❌ " + f.name + " — " + err.message + "\\n";
    }}
  }}
  addNotice("Arquivos processados. Continue respondendo a pergunta de provas (ou prossiga).","ok");
}});

keyBtn.addEventListener("click", ()=>{{
  DEMO_KEY=keyInput.value.trim();
  localStorage.setItem(STORE_KEY, DEMO_KEY);
  addNotice("Código registrado.");
  startSession();
}});

resetBtn.addEventListener("click", ()=>{{
  sessionId=null; state={{}};
  renderKV(); clearDownloads();
  uploadBox.style.display="none"; fileList.textContent="";
  addNotice("🔄 Reiniciando…");
  startSession();
}});

btn.addEventListener("click", send);
input.addEventListener("keydown",(e)=>{{ if(e.key==="Enter") send(); }});

dlReportBtn.addEventListener("click", ()=>{{ if(b64Report) downloadDocx(b64Report, nameReport); }});
dlPropBtn.addEventListener("click", ()=>{{ if(b64Prop) downloadDocx(b64Prop, nameProp); }});
dlPieceBtn.addEventListener("click", ()=>{{ if(b64Piece) downloadDocx(b64Piece, namePiece); }});

renderKV();
addNotice(DEMO_KEY ? "Código encontrado. Clique em Ativar." : "Cole o DEMO_KEY e clique em Ativar.");
setStatus("pronto");
</script>
</body>
</html>
"""

@app.get("/widget", response_class=HTMLResponse)
def widget(transparent: int = Query(default=0)):
    return HTMLResponse(WIDGET_HTML)
