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

# ─────────────────────────────────────────────
# Environment
# ─────────────────────────────────────────────
OPENAI_API_KEY        = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_MODEL          = (os.getenv("OPENAI_MODEL") or "gpt-4o").strip()
DEMO_KEY              = (os.getenv("DEMO_KEY") or "").strip()
ALLOWED_ORIGIN        = (os.getenv("ALLOWED_ORIGIN") or "*").strip()
OS_6_1_PROMPT         = (os.getenv("OS_6_1_PROMPT") or "").strip()
DATAJUD_ENABLED       = os.getenv("DATAJUD_ENABLED", "false").lower() == "true"
DATAJUD_BASE_URL      = (os.getenv("DATAJUD_BASE_URL") or "https://api-publica.datajud.cnj.jus.br").strip().rstrip("/")
DATAJUD_API_KEY       = (os.getenv("DATAJUD_API_KEY") or "").strip()
DATAJUD_TIMEOUT_S     = int(os.getenv("DATAJUD_TIMEOUT_S", "25"))
DATAJUD_DEFAULT_ALIAS = (os.getenv("DATAJUD_DEFAULT_ALIAS") or "").strip()
DATAJUD_SORT_FIELD    = (os.getenv("DATAJUD_SORT_FIELD") or "dataHoraUltimaAtualizacao").strip()
MANDATARIA_NOME       = (os.getenv("MANDATARIA_NOME") or "").strip()
MANDATARIA_OAB        = (os.getenv("MANDATARIA_OAB") or "").strip()

# ─────────────────────────────────────────────
# Códigos TPU — Tabela Processual Unificada CNJ
# Justiça do Trabalho
# ─────────────────────────────────────────────

# Movimentos que indicam SENTENÇA (mérito 1º grau)
CODIGOS_SENTENCA = {
    11: "Julgamento",
    193: "Sentença",
    198: "Sentença com Resolução de Mérito",
    199: "Sentença sem Resolução de Mérito",
    17: "Sentença Homologatória",
    14: "Improcedência",
    15: "Procedência",
    16: "Procedência em Parte",
}

# Movimentos que indicam DECISÃO INTERLOCUTÓRIA
CODIGOS_DECISAO_INTERLOCUTORIA = {
    85: "Decisão",
    26: "Despacho",
    51: "Despacho de Mero Expediente",
    60: "Liminar",
    61: "Antecipação de Tutela",
    87: "Decisão Interlocutória",
    1038: "Decisão Parcial de Mérito",
}

# Movimentos que indicam ACÓRDÃO (2º grau)
CODIGOS_ACORDAO = {
    941: "Acórdão",
    237: "Acórdão Publicado",
    196: "Julgamento de Recurso",
    7: "Recurso Improvido",
    8: "Recurso Provido",
    9: "Recurso Provido em Parte",
}

# Assuntos trabalhistas — horas extras e periculosidade
ASSUNTOS_HORAS_EXTRAS = {1723, 14548, 14549, 14550, 14551}
ASSUNTOS_PERICULOSIDADE = {1856, 14552, 14553}
ASSUNTOS_INSALUBRIDADE = {1855, 14554}
ASSUNTOS_TEMATICOS = ASSUNTOS_HORAS_EXTRAS | ASSUNTOS_PERICULOSIDADE | ASSUNTOS_INSALUBRIDADE

# ─────────────────────────────────────────────
# Intent detection keywords
# ─────────────────────────────────────────────
INTENT_PROCESS_FULL = [
    "andamento completo", "histórico completo", "timeline", "linha do tempo",
    "todas as movimentações", "todos os andamentos", "andamento detalhado",
    "histórico do processo", "movimentações completas",
]
INTENT_MAGISTRADO = [
    "magistrado", "juiz", "juíza", "quem sentenciou", "quem julgou",
    "quem decidiu", "prolator", "responsável pelo processo",
]
INTENT_BANCO_DECISOES = [
    "banco de decisões", "outros processos", "processos similares",
    "processos semelhantes", "decisões do juiz", "decisões da vara",
    "jurisprudência da vara", "padrão decisório", "como esse juiz decide",
    "histórico do juiz", "outros casos",
]
INTENT_TEMATICO = [
    "horas extras", "hora extra", "adicional de periculosidade", "periculosidade",
    "insalubridade", "adicional noturno", "análise temática",
]
INTENT_CLASSIFICAR = [
    "sentença", "decisão interlocutória", "acórdão", "tipo de decisão",
    "classifica", "separar decisões", "tipos de movimentação",
]

def detect_intent(message: str) -> str:
    """Detecta intenção principal da mensagem do usuário."""
    msg = message.lower()
    if any(k in msg for k in INTENT_BANCO_DECISOES):
        return "banco_decisoes"
    if any(k in msg for k in INTENT_MAGISTRADO):
        return "magistrado"
    if any(k in msg for k in INTENT_TEMATICO):
        return "tematico"
    if any(k in msg for k in INTENT_CLASSIFICAR):
        return "classificar"
    if any(k in msg for k in INTENT_PROCESS_FULL):
        return "andamento_completo"
    return "resumo"  # padrão

# ─────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────
def build_system_prompt(extra_context: str = "") -> str:
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
            f"\n\nEste sistema opera para: {MANDATARIA_NOME} — {MANDATARIA_OAB}. "
            "Todas as análises são assistivas e sujeitas à revisão do advogado responsável."
        )
    context_block = f"\n\n===CONTEXTO===\n{extra_context}\n===FIM===" if extra_context else ""
    return base + mandate_info + context_block

# ─────────────────────────────────────────────
# FastAPI
# ─────────────────────────────────────────────
app = FastAPI(title="SM OS Chat", version="4.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if ALLOWED_ORIGIN == "*" else [ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
SESSIONS: Dict[str, Dict[str, Any]] = {}

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
    assunto_codigo: Optional[int] = None
    size: int = 10
    cursor: Optional[str] = None

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def auth_or_401(x_demo_key: Optional[str]) -> None:
    if DEMO_KEY and x_demo_key != DEMO_KEY:
        raise HTTPException(status_code=401, detail="Não autorizado")

def get_session_state(session_id: str) -> Dict[str, Any]:
    return SESSIONS.setdefault(
        session_id,
        {"messages": [], "uploaded_contexts": [], "last_datajud_search": None,
         "last_process": None},
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

def infer_datajud_alias(numero: str) -> Optional[str]:
    m = re.match(r"(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})", numero or "")
    if not m:
        return None
    justice, tribunal = m.group(4), m.group(5)
    try:
        tr_num = int(tribunal)
    except Exception:
        return None
    if justice == "5":
        return f"api_publica_trt{tr_num}"
    if justice == "4":
        return f"api_publica_trf{tr_num}"
    return None

def compact_text(text: str, limit: int = 6000) -> str:
    text = (text or "").strip()
    return text[:limit] if len(text) > limit else text

def extract_text_from_upload(file: UploadFile, data: bytes) -> str:
    filename = (file.filename or "").lower()
    if filename.endswith((".txt", ".md")):
        return data.decode("utf-8", errors="ignore")
    if filename.endswith(".pdf") and PdfReader:
        try:
            reader = PdfReader(io.BytesIO(data))
            return "\n".join(p.extract_text() or "" for p in reader.pages[:40]).strip()
        except Exception:
            return ""
    if filename.endswith(".docx") and DocxDocument:
        try:
            doc = DocxDocument(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs if p.text).strip()
        except Exception:
            return ""
    return ""

def encode_cursor(values: Optional[List[Any]]) -> Optional[str]:
    if not values:
        return None
    return base64.urlsafe_b64encode(json.dumps(values).encode()).decode()

def decode_cursor(token: Optional[str]) -> Optional[List[Any]]:
    if not token:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(token.encode()).decode())
    except Exception:
        return None

# ─────────────────────────────────────────────
# Classificadores de movimentação
# ─────────────────────────────────────────────
def classify_movimento(mov: Dict[str, Any]) -> str:
    """Classifica uma movimentação em sentença / decisão_interlocutoria / acordao / outro."""
    codigo = None
    # campo codigoNacional pode vir de formas diferentes
    if isinstance(mov.get("codigoNacional"), int):
        codigo = mov["codigoNacional"]
    elif isinstance(mov.get("movimentoNacional"), dict):
        codigo = mov["movimentoNacional"].get("codigo")

    if codigo:
        if codigo in CODIGOS_SENTENCA:
            return "sentenca"
        if codigo in CODIGOS_DECISAO_INTERLOCUTORIA:
            return "decisao_interlocutoria"
        if codigo in CODIGOS_ACORDAO:
            return "acordao"

    nome = (mov.get("nome") or "").lower()
    if any(w in nome for w in ["sentença", "sentenca", "julgament", "procedente", "improcedente"]):
        return "sentenca"
    if any(w in nome for w in ["acórdão", "acordao", "recurso provido", "recurso improvido"]):
        return "acordao"
    if any(w in nome for w in ["decisão", "decisao", "despacho", "liminar", "tutela"]):
        return "decisao_interlocutoria"
    return "outro"

def extract_magistrado_from_movimentos(movimentos: List[Dict[str, Any]]) -> Optional[str]:
    """
    Tenta extrair o nome/identificador do magistrado prolator.
    O DataJud envia 'magistradoProlator' ou 'responsavelMovimento' dentro de cada movimento.
    """
    for mov in movimentos:
        # Tenta campo direto
        mag = mov.get("magistradoProlator") or mov.get("responsavelMovimento")
        if mag:
            if isinstance(mag, dict):
                nome = mag.get("nome") or mag.get("nomeServidor") or mag.get("cpf")
            else:
                nome = str(mag)
            if nome and len(nome) > 3:
                return nome
        # Tenta dentro de complementos
        complementos = mov.get("complementosTabelados") or []
        for comp in complementos:
            if isinstance(comp, dict):
                desc = (comp.get("descricao") or "").lower()
                if "magistrado" in desc or "juiz" in desc:
                    return comp.get("valor") or comp.get("nome")
    return None

def get_orgao_julgador(source: Dict[str, Any]) -> Optional[str]:
    orgao = source.get("orgaoJulgador") or {}
    return orgao.get("nome") or orgao.get("codigo")

# ─────────────────────────────────────────────
# DataJud Service
# ─────────────────────────────────────────────
class DataJudError(Exception):
    pass

class DataJudService:
    def __init__(self):
        self.enabled       = DATAJUD_ENABLED
        self.base_url      = DATAJUD_BASE_URL
        self.api_key       = DATAJUD_API_KEY
        self.timeout_s     = DATAJUD_TIMEOUT_S
        self.default_alias = DATAJUD_DEFAULT_ALIAS
        self.sort_field    = DATAJUD_SORT_FIELD

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"APIKey {self.api_key}"
        return h

    def _post(self, path: str, payload: dict) -> dict:
        if not self.enabled:
            raise DataJudError("DataJud desabilitado.")
        if not self.api_key:
            raise DataJudError("DATAJUD_API_KEY não configurado.")
        url = f"{self.base_url}{path}"
        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout_s)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            body = e.response.text if e.response else ""
            raise DataJudError(f"HTTP {getattr(e.response, 'status_code', '?')}: {body[:800]}")
        except requests.RequestException as e:
            raise DataJudError(f"Falha de conexão: {str(e)}")

    def search(self, alias: str, query: dict, size: int = 10,
               sort: list = None, search_after: list = None,
               source_fields: list = None) -> dict:
        alias = (alias or self.default_alias or "").strip()
        if not alias:
            raise DataJudError("Alias do tribunal não informado.")
        payload: dict = {
            "size":  min(size, 50),
            "query": query,
            "sort":  sort or [{self.sort_field: "desc"}],
        }
        if search_after:
            payload["search_after"] = search_after
        if source_fields:
            payload["_source"] = source_fields
        return self._post(f"/{alias}/_search", payload)

    def get_process(self, numero: str, alias: str = None) -> dict:
        alias = alias or infer_datajud_alias(numero) or self.default_alias
        if not alias:
            raise DataJudError("Não foi possível inferir o tribunal do número CNJ.")
        numero_limpo = normalize_process_number(numero)
        return self.search(
            alias=alias,
            query={"match": {"numeroProcesso": numero_limpo}},
            size=1,
        )

    def get_process_full(self, numero: str, alias: str = None) -> dict:
        """Busca processo pedindo TODOS os campos disponíveis."""
        alias = alias or infer_datajud_alias(numero) or self.default_alias
        if not alias:
            raise DataJudError("Não foi possível inferir o tribunal do número CNJ.")
        numero_limpo = normalize_process_number(numero)
        # Sem _source filter = retorna tudo
        return self.search(
            alias=alias,
            query={"match": {"numeroProcesso": numero_limpo}},
            size=1,
        )

    def search_by_orgao_and_assunto(self, orgao_nome: str, assunto_codigo: int = None,
                                     assunto_nome: str = None, alias: str = None,
                                     size: int = 10) -> dict:
        """Banco de decisões: processos da mesma vara com assunto similar."""
        alias = alias or self.default_alias
        must = [{"match": {"orgaoJulgador.nome": orgao_nome}}]
        if assunto_codigo:
            must.append({"match": {"assuntos.codigo": assunto_codigo}})
        if assunto_nome:
            must.append({"match": {"assuntos.nome": assunto_nome}})
        return self.search(
            alias=alias,
            query={"bool": {"must": must}},
            size=size,
            source_fields=[
                "numeroProcesso", "tribunal", "grau", "dataAjuizamento",
                "dataHoraUltimaAtualizacao", "classe", "assuntos",
                "orgaoJulgador", "movimentos", "valorCausa",
            ],
        )

    def search_tematico(self, codigos_assunto: set, alias: str = None, size: int = 15) -> dict:
        """Busca por tema (horas extras, periculosidade, insalubridade)."""
        alias = alias or self.default_alias
        should = [{"match": {"assuntos.codigo": c}} for c in codigos_assunto]
        return self.search(
            alias=alias,
            query={"bool": {"should": should, "minimum_should_match": 1}},
            size=size,
            source_fields=[
                "numeroProcesso", "tribunal", "grau", "dataAjuizamento",
                "dataHoraUltimaAtualizacao", "classe", "assuntos",
                "orgaoJulgador", "movimentos", "valorCausa",
            ],
        )

    def extract_sources(self, raw: dict) -> List[dict]:
        return [h.get("_source") or {} for h in ((raw or {}).get("hits") or {}).get("hits") or []]

    def normalize_process(self, source: dict) -> dict:
        movimentos = source.get("movimentos") or []
        movimentos_sorted = sorted(movimentos, key=lambda x: x.get("dataHora") or "", reverse=True)
        ultima_mov = movimentos_sorted[0] if movimentos_sorted else None
        assuntos   = source.get("assuntos") or []
        classe     = source.get("classe") or {}
        orgao      = source.get("orgaoJulgador") or {}

        # Classificar movimentos por tipo
        sentencas   = [m for m in movimentos_sorted if classify_movimento(m) == "sentenca"]
        interlocut  = [m for m in movimentos_sorted if classify_movimento(m) == "decisao_interlocutoria"]
        acordaos    = [m for m in movimentos_sorted if classify_movimento(m) == "acordao"]

        # Tentar extrair magistrado
        magistrado = extract_magistrado_from_movimentos(movimentos_sorted)

        return {
            "numero_processo":          source.get("numeroProcesso"),
            "tribunal":                 source.get("tribunal"),
            "grau":                     source.get("grau"),
            "data_ajuizamento":         source.get("dataAjuizamento"),
            "ultima_atualizacao":       source.get("dataHoraUltimaAtualizacao"),
            "valor_causa":              source.get("valorCausa"),
            "classe_nome":              classe.get("nome"),
            "classe_codigo":            classe.get("codigo"),
            "orgao_julgador":           orgao.get("nome"),
            "orgao_codigo":             orgao.get("codigo"),
            "assuntos":                 [a.get("nome") for a in assuntos if a.get("nome")],
            "assuntos_codigos":         [a.get("codigo") for a in assuntos if a.get("codigo")],
            "movimentos_total":         len(movimentos),
            "magistrado":               magistrado,
            "ultima_movimentacao_nome": (ultima_mov or {}).get("nome"),
            "ultima_movimentacao_data": (ultima_mov or {}).get("dataHora"),
            "movimentos_todos":         movimentos_sorted,
            "sentencas":                sentencas,
            "decisoes_interlocutorias": interlocut,
            "acordaos":                 acordaos,
            "raw":                      source,
        }

DATAJUD = DataJudService()

# ─────────────────────────────────────────────
# Context builders para GPT
# ─────────────────────────────────────────────
def _fmt_mov(mov: Dict[str, Any], incluir_tipo: bool = False) -> str:
    nome  = mov.get("nome") or "Movimentação"
    data  = (mov.get("dataHora") or "")[:10]
    tipo  = f" [{classify_movimento(mov).replace('_',' ').upper()}]" if incluir_tipo else ""
    mag   = mov.get("magistradoProlator") or ""
    mag_s = f" | Magistrado: {mag}" if mag and isinstance(mag, str) else ""
    return f"  • {data}{tipo} — {nome}{mag_s}"

def build_context_resumo(proc: dict) -> str:
    assuntos = ", ".join(proc["assuntos"]) or "não identificados"
    lines = [
        f"PROCESSO: {proc['numero_processo']}",
        f"Tribunal: {proc['tribunal']} | Grau: {proc['grau']}",
        f"Classe: {proc['classe_nome']} | Vara: {proc['orgao_julgador']}",
        f"Assuntos: {assuntos}",
        f"Ajuizamento: {proc['data_ajuizamento'] or 'n/d'}",
        f"Última atualização: {proc['ultima_atualizacao'] or 'n/d'}",
        f"Total de movimentações: {proc['movimentos_total']}",
        f"Magistrado identificado: {proc['magistrado'] or 'não disponível na API'}",
        f"Última movimentação: {proc['ultima_movimentacao_nome']} ({proc['ultima_movimentacao_data'] or ''[:10]})",
        "",
        "ÚLTIMAS 10 MOVIMENTAÇÕES:",
    ] + [_fmt_mov(m) for m in proc["movimentos_todos"][:10]]
    return "\n".join(lines)

def build_context_andamento_completo(proc: dict) -> str:
    lines = [
        f"ANDAMENTO COMPLETO — {proc['numero_processo']}",
        f"Tribunal: {proc['tribunal']} | Vara: {proc['orgao_julgador']}",
        f"Ajuizamento: {proc['data_ajuizamento'] or 'n/d'} | Total movimentações: {proc['movimentos_total']}",
        f"Magistrado prolator identificado: {proc['magistrado'] or 'não disponível via API'}",
        "",
        f"▶ SENTENÇAS ({len(proc['sentencas'])}):",
    ] + ([_fmt_mov(m) for m in proc["sentencas"]] or ["  (nenhuma encontrada)"]) + [
        "",
        f"▶ ACÓRDÃOS ({len(proc['acordaos'])}):",
    ] + ([_fmt_mov(m) for m in proc["acordaos"]] or ["  (nenhum encontrado)"]) + [
        "",
        f"▶ DECISÕES INTERLOCUTÓRIAS ({len(proc['decisoes_interlocutorias'])}):",
    ] + ([_fmt_mov(m, True) for m in proc["decisoes_interlocutorias"][:10]] or ["  (nenhuma)"]) + [
        "",
        f"▶ HISTÓRICO CRONOLÓGICO COMPLETO ({proc['movimentos_total']} movimentos):",
    ] + [_fmt_mov(m, True) for m in proc["movimentos_todos"]]
    return "\n".join(lines)

def build_context_magistrado(proc: dict) -> str:
    mag = proc["magistrado"] or "não disponível diretamente via API pública"
    # Tentar identificar nos movimentos com mais detalhe
    mag_detalhes = []
    for m in proc["movimentos_todos"]:
        raw_mag = m.get("magistradoProlator") or m.get("responsavelMovimento")
        if raw_mag:
            data  = (m.get("dataHora") or "")[:10]
            nome_mov = m.get("nome") or ""
            mag_detalhes.append(f"  • {data} — {nome_mov} | Prolator: {raw_mag}")
    lines = [
        f"IDENTIFICAÇÃO DO MAGISTRADO — {proc['numero_processo']}",
        f"Vara/Órgão Julgador: {proc['orgao_julgador']}",
        f"Magistrado prolator (campo API): {mag}",
        "",
        "Movimentos com magistrado identificado nos dados brutos:"
        if mag_detalhes else "Nenhum movimento retornou magistradoProlator na API do DataJud.",
    ] + mag_detalhes[:20]
    return "\n".join(lines)

def build_context_banco_decisoes(processos: List[dict], orgao: str, assunto: str) -> str:
    lines = [
        f"BANCO DE DECISÕES — {orgao}",
        f"Assunto filtrado: {assunto}",
        f"Processos encontrados: {len(processos)}",
        "",
    ]
    for p in processos:
        proc = DATAJUD.normalize_process(p)
        mag  = proc["magistrado"] or "n/d"
        ult  = proc["ultima_movimentacao_nome"] or "n/d"
        n_sent = len(proc["sentencas"])
        lines += [
            f"── {proc['numero_processo']}",
            f"   Vara: {proc['orgao_julgador']} | Magistrado: {mag}",
            f"   Assuntos: {', '.join(proc['assuntos'][:3])}",
            f"   Ajuizamento: {proc['data_ajuizamento'] or 'n/d'} | Sentenças: {n_sent}",
            f"   Última movimentação: {ult}",
            "",
        ]
    return "\n".join(lines)

def build_context_tematico(processos: List[dict], tema: str) -> str:
    lines = [
        f"ANÁLISE TEMÁTICA — {tema.upper()}",
        f"Processos encontrados: {len(processos)}",
        "",
    ]
    total_sentencas  = 0
    total_acordaos   = 0
    total_procedente = 0
    for p in processos:
        proc = DATAJUD.normalize_process(p)
        total_sentencas  += len(proc["sentencas"])
        total_acordaos   += len(proc["acordaos"])
        # Heurística: sentença de procedência
        for s in proc["sentencas"]:
            nome_s = (s.get("nome") or "").lower()
            if "procedente" in nome_s and "improcedente" not in nome_s:
                total_procedente += 1
        lines += [
            f"── {proc['numero_processo']}",
            f"   Vara: {proc['orgao_julgador']} | Magistrado: {proc['magistrado'] or 'n/d'}",
            f"   Assuntos: {', '.join(proc['assuntos'][:4])}",
            f"   Sentenças: {len(proc['sentencas'])} | Acórdãos: {len(proc['acordaos'])}",
            f"   Última mov.: {proc['ultima_movimentacao_nome'] or 'n/d'}",
            "",
        ]
    lines += [
        "── CONSOLIDADO:",
        f"   Total sentenças: {total_sentencas}",
        f"   Total acórdãos: {total_acordaos}",
        f"   Sentenças de procedência (estimado): {total_procedente}",
    ]
    return "\n".join(lines)

def build_context_classificacao(proc: dict) -> str:
    lines = [
        f"CLASSIFICAÇÃO DE DECISÕES — {proc['numero_processo']}",
        f"Vara: {proc['orgao_julgador']} | Total movimentos: {proc['movimentos_total']}",
        "",
        f"SENTENÇAS DE MÉRITO ({len(proc['sentencas'])}):",
    ] + ([_fmt_mov(m) for m in proc["sentencas"]] or ["  (nenhuma)"]) + [
        "",
        f"ACÓRDÃOS ({len(proc['acordaos'])}):",
    ] + ([_fmt_mov(m) for m in proc["acordaos"]] or ["  (nenhum)"]) + [
        "",
        f"DECISÕES INTERLOCUTÓRIAS ({len(proc['decisoes_interlocutorias'])}):",
    ] + ([_fmt_mov(m) for m in proc["decisoes_interlocutorias"][:15]] or ["  (nenhuma)"]) + [
        "",
        f"OUTROS ANDAMENTOS ({len([m for m in proc['movimentos_todos'] if classify_movimento(m) == 'outro'])}):",
        "  (despachos de expediente, conclusão, redistribuição, etc.)",
    ]
    return "\n".join(lines)

# ─────────────────────────────────────────────
# Intent-to-prompt instructions
# ─────────────────────────────────────────────
INSTRUCAO_POR_INTENT = {
    "resumo": (
        "Realize análise jurídica completa do processo acima conforme protocolo OS 6.1. "
        "Identifique fase processual, última movimentação relevante, riscos e próximos passos. "
        "Destaque alertas críticos. Responda em português."
    ),
    "andamento_completo": (
        "Apresente o andamento completo do processo em ordem cronológica. "
        "Organize por categorias: sentenças, acórdãos, decisões interlocutórias e demais andamentos. "
        "Comente o significado jurídico de cada fase. Sinalize marcos importantes (citação, audiência, sentença, recurso). "
        "Responda em português."
    ),
    "magistrado": (
        "Identifique e informe tudo que for possível sobre o magistrado responsável pelo processo. "
        "Se o campo magistradoProlator não estiver disponível na API, explique essa limitação e informe o órgão julgador. "
        "Analise o padrão das decisões do processo para inferir tendências. Responda em português."
    ),
    "banco_decisoes": (
        "Analise os processos semelhantes da mesma vara listados acima. "
        "Identifique padrões decisórios do magistrado/vara: taxa de procedência, temas mais recorrentes, "
        "tendência em horas extras e periculosidade. Use isso para orientar estratégia no caso principal. "
        "Responda em português."
    ),
    "tematico": (
        "Realize análise temática dos processos encontrados. "
        "Identifique padrões: taxa de procedência em horas extras, periculosidade e insalubridade; "
        "magistrados que mais julgam o tema; tendência das varas do TRT2. "
        "Use os dados para fundamentar estratégia no caso. Responda em português."
    ),
    "classificar": (
        "Classifique e explique cada tipo de decisão do processo: "
        "sentenças de mérito, acórdãos, decisões interlocutórias e despachos. "
        "Explique o impacto jurídico de cada uma e o que esperar nas próximas fases. "
        "Responda em português."
    ),
}

# ─────────────────────────────────────────────
# OpenAI
# ─────────────────────────────────────────────
def call_openai(messages: List[dict], temperature: float = 0.15) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY não configurado.")
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": OPENAI_MODEL, "messages": messages, "temperature": temperature},
        timeout=90,
    )
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"Erro OpenAI: {(e.response.text if e.response else '')[:800]}")
    return (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip() \
           or "Não consegui gerar resposta."

# ─────────────────────────────────────────────
# Intent detection helpers
# ─────────────────────────────────────────────
def looks_like_process_request(message: str) -> bool:
    msg = message.lower()
    if detect_process_numbers(message):
        return True
    return any(k in msg for k in [
        "resumo do processo", "andamento", "movimentações", "status do processo",
        "magistrado", "juiz", "quem sentenciou", "banco de decisões",
        "processos similares", "análise temática", "horas extras",
        "periculosidade", "sentença", "acórdão",
    ])

def looks_like_natural_search(message: str) -> bool:
    msg = message.lower()
    return any(w in msg for w in ["busque", "procure", "pesquise", "me mostre processos"]) \
        and any(f in msg for f in ["classe", "assunto", "tribunal", "órgão"])

def parse_natural_search(message: str) -> Optional[DataJudSearchRequest]:
    msg = message or ""
    req = DataJudSearchRequest(size=5)
    for pattern, field in [
        (r"classe\s*[:=]\s*([^,;\n]+)", "classe_nome"),
        (r"assunto\s*[:=]\s*([^,;\n]+)", "assunto_nome"),
        (r"tribunal\s*[:=]\s*([^,;\n]+)", "tribunal"),
        (r"(?:órgão|orgao)\s*[:=]\s*([^,;\n]+)", "orgao_julgador"),
    ]:
        m = re.search(pattern, msg, flags=re.I)
        if m:
            setattr(req, field, m.group(1).strip())
    numbers = detect_process_numbers(msg)
    if numbers:
        req.numero_processo = numbers[0]
    if not any([req.numero_processo, req.classe_nome, req.assunto_nome,
                req.tribunal, req.orgao_julgador]):
        return None
    return req

# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────
@app.get("/ping")
def ping():
    return {"ok": True}

@app.get("/health")
def health():
    return {
        "ok": True, "version": "4.0.0",
        "model": OPENAI_MODEL,
        "os_61_loaded": bool(OS_6_1_PROMPT),
        "datajud": {"enabled": DATAJUD_ENABLED, "alias": DATAJUD_DEFAULT_ALIAS},
    }

@app.post("/session/new", response_model=SessionOut)
def session_new(x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = str(uuid.uuid4())
    SESSIONS[sid] = {"messages": [], "uploaded_contexts": [], "last_datajud_search": None, "last_process": None}
    return SessionOut(session_id=sid, state={})

@app.post("/upload")
async def upload(session_id: str = Form(...), file: UploadFile = File(...),
                 x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    session = get_session_state(session_id)
    data = await file.read()
    text = compact_text(extract_text_from_upload(file, data), 12000)
    session["uploaded_contexts"].append({"filename": file.filename, "text": text})
    return {"ok": True, "message": f"Arquivo '{file.filename}' anexado com sucesso.", "filename": file.filename}

@app.get("/datajud/test-process")
def datajud_test(numero: str, alias: Optional[str] = None):
    try:
        raw   = DATAJUD.get_process_full(numero, alias)
        items = DATAJUD.extract_sources(raw)
        if not items:
            return {"ok": True, "found": False}
        return {"ok": True, "found": True, "process": DATAJUD.normalize_process(items[0])}
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

    # ── 1. Busca natural estruturada ──────────────────────────────────
    if looks_like_natural_search(message):
        req = parse_natural_search(message)
        if req:
            try:
                query: dict
                must = []
                if req.numero_processo:
                    must.append({"match": {"numeroProcesso": normalize_process_number(req.numero_processo)}})
                if req.classe_nome:
                    must.append({"match": {"classe.nome": req.classe_nome}})
                if req.assunto_nome:
                    must.append({"match": {"assuntos.nome": req.assunto_nome}})
                if req.orgao_julgador:
                    must.append({"match": {"orgaoJulgador.nome": req.orgao_julgador}})
                query = {"bool": {"must": must}} if must else {"match_all": {}}
                raw   = DATAJUD.search(DATAJUD_DEFAULT_ALIAS, query, size=req.size)
                items = DATAJUD.extract_sources(raw)
                if not items:
                    reply = "Não encontrei processos com esses filtros."
                else:
                    lines = [f"**{len(items)} processo(s) encontrado(s):**\n"]
                    for s in items[:5]:
                        p = DATAJUD.normalize_process(s)
                        lines.append(f"- **{p['numero_processo']}** | {p['tribunal']} | {p['classe_nome']} | {p['orgao_julgador']}")
                    lines.append("\nEnvie o número de qualquer processo para análise completa.")
                    reply = "\n".join(lines)
                session["messages"].append({"role": "assistant", "content": reply})
                return ChatOut(message=reply, state=state)
            except Exception as e:
                reply = f"Erro na busca DataJud: {str(e)}"
                session["messages"].append({"role": "assistant", "content": reply})
                return ChatOut(message=reply, state=state)

    # ── 2. Requisição envolvendo processo ────────────────────────────
    if DATAJUD_ENABLED and looks_like_process_request(message):
        numbers = detect_process_numbers(message)
        intent  = detect_intent(message)

        # Recuperar número do processo — da mensagem ou da sessão
        numero = None
        if numbers:
            numero = numbers[0]
            session["last_process_numero"] = numero
        elif session.get("last_process_numero") and any(
            k in message.lower() for k in INTENT_MAGISTRADO + INTENT_BANCO_DECISOES +
            INTENT_TEMATICO + INTENT_CLASSIFICAR + INTENT_PROCESS_FULL
        ):
            numero = session["last_process_numero"]

        if numero:
            try:
                alias = infer_datajud_alias(numero) or DATAJUD_DEFAULT_ALIAS
                raw   = DATAJUD.get_process_full(numero, alias)
                items = DATAJUD.extract_sources(raw)

                if not items:
                    reply = f"Não encontrei o processo **{numero}** no DataJud."
                    session["messages"].append({"role": "assistant", "content": reply})
                    return ChatOut(message=reply, state=state)

                proc = DATAJUD.normalize_process(items[0])
                session["last_process"] = proc
                session["last_process_numero"] = numero

                # ── Banco de decisões: busca adicional ────────────────
                if intent == "banco_decisoes":
                    orgao   = proc["orgao_julgador"] or ""
                    assunto = (proc["assuntos"] or [""])[0]
                    assunto_cod = (proc["assuntos_codigos"] or [None])[0]
                    try:
                        raw2   = DATAJUD.search_by_orgao_and_assunto(orgao, assunto_cod, assunto, alias, size=8)
                        fontes = DATAJUD.extract_sources(raw2)
                        # Excluir o próprio processo
                        fontes = [f for f in fontes if
                                  normalize_process_number(f.get("numeroProcesso") or "") !=
                                  normalize_process_number(numero)][:6]
                        context_block = (
                            build_context_resumo(proc) + "\n\n" +
                            build_context_banco_decisoes(fontes, orgao, assunto)
                        )
                    except Exception:
                        context_block = build_context_resumo(proc)

                # ── Análise temática: busca por assunto ───────────────
                elif intent == "tematico":
                    msg_lower = message.lower()
                    if "periculosidade" in msg_lower:
                        codigos = ASSUNTOS_PERICULOSIDADE
                        tema    = "Adicional de Periculosidade"
                    elif "insalubridade" in msg_lower:
                        codigos = ASSUNTOS_INSALUBRIDADE
                        tema    = "Adicional de Insalubridade"
                    else:
                        codigos = ASSUNTOS_HORAS_EXTRAS
                        tema    = "Horas Extras"
                    try:
                        raw2   = DATAJUD.search_tematico(codigos, alias, size=12)
                        fontes = DATAJUD.extract_sources(raw2)
                        context_block = (
                            build_context_resumo(proc) + "\n\n" +
                            build_context_tematico(fontes, tema)
                        )
                    except Exception:
                        context_block = build_context_resumo(proc)
                        tema = "tema solicitado"

                # ── Intents que usam apenas o processo principal ───────
                elif intent == "andamento_completo":
                    context_block = build_context_andamento_completo(proc)
                elif intent == "magistrado":
                    context_block = build_context_magistrado(proc)
                elif intent == "classificar":
                    context_block = build_context_classificacao(proc)
                else:
                    context_block = build_context_resumo(proc)

                instrucao = INSTRUCAO_POR_INTENT.get(intent, INSTRUCAO_POR_INTENT["resumo"])
                system_prompt = build_system_prompt(extra_context=context_block)
                msgs = [{"role": "system", "content": system_prompt}]
                for item in session["messages"][-6:]:
                    if item["role"] in {"user", "assistant"}:
                        msgs.append(item)
                msgs[-1] = {"role": "user", "content": f"{message}\n\n[INSTRUÇÃO: {instrucao}]"}

                reply = call_openai(msgs, temperature=0.15)
                session["messages"].append({"role": "assistant", "content": reply})
                return ChatOut(message=reply, state=state)

            except DataJudError as e:
                reply = f"Não consegui consultar o DataJud. Motivo: {str(e)}"
                session["messages"].append({"role": "assistant", "content": reply})
                return ChatOut(message=reply, state=state)

    # ── 3. Chat jurídico livre com OS 6.1 ────────────────────────────
    uploaded_context = "\n\n".join(
        f"[Arquivo: {x.get('filename')}]\n{compact_text(x.get('text') or '', 4000)}"
        for x in session.get("uploaded_contexts", [])[-3:] if x.get("text")
    ).strip()

    system_prompt = build_system_prompt(extra_context=uploaded_context)
    msgs = [{"role": "system", "content": system_prompt}]
    for item in session["messages"][-12:]:
        if item["role"] in {"user", "assistant"}:
            msgs.append(item)

    try:
        reply = call_openai(msgs, temperature=0.2)
    except Exception as e:
        reply = f"Erro no servidor: {str(e)}"

    session["messages"].append({"role": "assistant", "content": reply})
    session["messages"] = session["messages"][-20:]
    return ChatOut(message=reply, state=state)
