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
DATAJUD_DEFAULT_ALIAS = (os.getenv("DATAJUD_DEFAULT_ALIAS") or "api_publica_trt2").strip()
DATAJUD_SORT_FIELD    = (os.getenv("DATAJUD_SORT_FIELD") or "dataHoraUltimaAtualizacao").strip()
MANDATARIA_NOME       = (os.getenv("MANDATARIA_NOME") or "").strip()
MANDATARIA_OAB        = (os.getenv("MANDATARIA_OAB") or "").strip()

# ─────────────────────────────────────────────
# Mapa completo de aliases DataJud — todos os tribunais brasileiros
# Formato CNJ: NNNNNNN-DD.AAAA.J.TR.OOOO
#   J=1 STF/STJ  J=2 JE/JF  J=3 TSE/TRE  J=4 STM/TRF  J=5 TST/TRT  J=6 TJDFT  J=8 TJ
# ─────────────────────────────────────────────

ALIAS_MAP: Dict[str, str] = {
    # ── Justiça do Trabalho (J=5) ──────────────────────────────────
    "5.01": "api_publica_trt1",   # TRT1  — Rio de Janeiro
    "5.02": "api_publica_trt2",   # TRT2  — São Paulo
    "5.03": "api_publica_trt3",   # TRT3  — Minas Gerais
    "5.04": "api_publica_trt4",   # TRT4  — Rio Grande do Sul
    "5.05": "api_publica_trt5",   # TRT5  — Bahia
    "5.06": "api_publica_trt6",   # TRT6  — Pernambuco
    "5.07": "api_publica_trt7",   # TRT7  — Ceará
    "5.08": "api_publica_trt8",   # TRT8  — Pará/Amapá
    "5.09": "api_publica_trt9",   # TRT9  — Paraná
    "5.10": "api_publica_trt10",  # TRT10 — DF/TO
    "5.11": "api_publica_trt11",  # TRT11 — AM/RR
    "5.12": "api_publica_trt12",  # TRT12 — Santa Catarina
    "5.13": "api_publica_trt13",  # TRT13 — Paraíba
    "5.14": "api_publica_trt14",  # TRT14 — RO/AC
    "5.15": "api_publica_trt15",  # TRT15 — Campinas
    "5.16": "api_publica_trt16",  # TRT16 — Maranhão
    "5.17": "api_publica_trt17",  # TRT17 — Espírito Santo
    "5.18": "api_publica_trt18",  # TRT18 — Goiás
    "5.19": "api_publica_trt19",  # TRT19 — Alagoas
    "5.20": "api_publica_trt20",  # TRT20 — Sergipe
    "5.21": "api_publica_trt21",  # TRT21 — Rio Grande do Norte
    "5.22": "api_publica_trt22",  # TRT22 — Piauí
    "5.23": "api_publica_trt23",  # TRT23 — Mato Grosso
    "5.24": "api_publica_trt24",  # TRT24 — Mato Grosso do Sul
    "5.00": "api_publica_tst",    # TST

    # ── Justiça Federal (J=4) ───────────────────────────────────────
    "4.01": "api_publica_trf1",   # TRF1 — DF e Norte/Centro-Oeste
    "4.02": "api_publica_trf2",   # TRF2 — RJ/ES
    "4.03": "api_publica_trf3",   # TRF3 — SP/MS
    "4.04": "api_publica_trf4",   # TRF4 — Sul
    "4.05": "api_publica_trf5",   # TRF5 — Nordeste
    "4.06": "api_publica_trf6",   # TRF6 — MG

    # ── Tribunais de Justiça Estaduais (J=8) ───────────────────────
    "8.01": "api_publica_tjac",   # TJAC — Acre
    "8.02": "api_publica_tjal",   # TJAL — Alagoas
    "8.03": "api_publica_tjam",   # TJAM — Amazonas
    "8.04": "api_publica_tjap",   # TJAP — Amapá
    "8.05": "api_publica_tjba",   # TJBA — Bahia
    "8.06": "api_publica_tjce",   # TJCE — Ceará
    "8.07": "api_publica_tjdft",  # TJDFT — Distrito Federal
    "8.08": "api_publica_tjes",   # TJES — Espírito Santo
    "8.09": "api_publica_tjgo",   # TJGO — Goiás
    "8.10": "api_publica_tjma",   # TJMA — Maranhão
    "8.11": "api_publica_tjmg",   # TJMG — Minas Gerais
    "8.12": "api_publica_tjms",   # TJMS — Mato Grosso do Sul
    "8.13": "api_publica_tjmt",   # TJMT — Mato Grosso
    "8.14": "api_publica_tjpa",   # TJPA — Pará
    "8.15": "api_publica_tjpb",   # TJPB — Paraíba
    "8.16": "api_publica_tjpe",   # TJPE — Pernambuco
    "8.17": "api_publica_tjpi",   # TJPI — Piauí
    "8.18": "api_publica_tjpr",   # TJPR — Paraná
    "8.19": "api_publica_tjrj",   # TJRJ — Rio de Janeiro
    "8.20": "api_publica_tjrn",   # TJRN — Rio Grande do Norte
    "8.21": "api_publica_tjro",   # TJRO — Rondônia
    "8.22": "api_publica_tjrr",   # TJRR — Roraima
    "8.23": "api_publica_tjrs",   # TJRS — Rio Grande do Sul
    "8.24": "api_publica_tjsc",   # TJSC — Santa Catarina
    "8.25": "api_publica_tjse",   # TJSE — Sergipe
    "8.26": "api_publica_tjsp",   # TJSP — São Paulo
    "8.27": "api_publica_tjto",   # TJTO — Tocantins

    # ── Justiça Eleitoral (J=3) ─────────────────────────────────────
    "3.00": "api_publica_tse",
    "3.01": "api_publica_tre-ac",
    "3.02": "api_publica_tre-al",
    "3.03": "api_publica_tre-am",
    "3.04": "api_publica_tre-ap",
    "3.05": "api_publica_tre-ba",
    "3.06": "api_publica_tre-ce",
    "3.07": "api_publica_tre-df",
    "3.08": "api_publica_tre-es",
    "3.09": "api_publica_tre-go",
    "3.10": "api_publica_tre-ma",
    "3.11": "api_publica_tre-mg",
    "3.12": "api_publica_tre-ms",
    "3.13": "api_publica_tre-mt",
    "3.14": "api_publica_tre-pa",
    "3.15": "api_publica_tre-pb",
    "3.16": "api_publica_tre-pe",
    "3.17": "api_publica_tre-pi",
    "3.18": "api_publica_tre-pr",
    "3.19": "api_publica_tre-rj",
    "3.20": "api_publica_tre-rn",
    "3.21": "api_publica_tre-ro",
    "3.22": "api_publica_tre-rr",
    "3.23": "api_publica_tre-rs",
    "3.24": "api_publica_tre-sc",
    "3.25": "api_publica_tre-se",
    "3.26": "api_publica_tre-sp",
    "3.27": "api_publica_tre-to",

    # ── Superiores (J=1) ────────────────────────────────────────────
    "1.00": "api_publica_stj",
    "1.01": "api_publica_stf",

    # ── Militar (J=9) ───────────────────────────────────────────────
    "9.00": "api_publica_stm",
}

# Nomes legíveis para exibição ao usuário
ALIAS_NOME: Dict[str, str] = {
    "api_publica_trt1":  "TRT1 (Rio de Janeiro)",
    "api_publica_trt2":  "TRT2 (São Paulo)",
    "api_publica_trt3":  "TRT3 (Minas Gerais)",
    "api_publica_trt4":  "TRT4 (Rio Grande do Sul)",
    "api_publica_trt5":  "TRT5 (Bahia)",
    "api_publica_trt6":  "TRT6 (Pernambuco)",
    "api_publica_trt7":  "TRT7 (Ceará)",
    "api_publica_trt8":  "TRT8 (Pará/Amapá)",
    "api_publica_trt9":  "TRT9 (Paraná)",
    "api_publica_trt10": "TRT10 (DF/TO)",
    "api_publica_trt11": "TRT11 (AM/RR)",
    "api_publica_trt12": "TRT12 (Santa Catarina)",
    "api_publica_trt13": "TRT13 (Paraíba)",
    "api_publica_trt14": "TRT14 (RO/AC)",
    "api_publica_trt15": "TRT15 (Campinas)",
    "api_publica_trt16": "TRT16 (Maranhão)",
    "api_publica_trt17": "TRT17 (Espírito Santo)",
    "api_publica_trt18": "TRT18 (Goiás)",
    "api_publica_trt19": "TRT19 (Alagoas)",
    "api_publica_trt20": "TRT20 (Sergipe)",
    "api_publica_trt21": "TRT21 (RN)",
    "api_publica_trt22": "TRT22 (Piauí)",
    "api_publica_trt23": "TRT23 (Mato Grosso)",
    "api_publica_trt24": "TRT24 (MS)",
    "api_publica_tst":   "TST",
    "api_publica_trf1":  "TRF1",
    "api_publica_trf2":  "TRF2",
    "api_publica_trf3":  "TRF3",
    "api_publica_trf4":  "TRF4",
    "api_publica_trf5":  "TRF5",
    "api_publica_trf6":  "TRF6",
    "api_publica_stj":   "STJ",
    "api_publica_stf":   "STF",
}

def alias_para_nome(alias: str) -> str:
    return ALIAS_NOME.get(alias, alias)

# ─────────────────────────────────────────────
# Inferência de alias a partir do número CNJ
# ─────────────────────────────────────────────
PROCESS_NUMBER_RE = re.compile(r"\b(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})\b")

def infer_alias_from_cnj(numero: str) -> Optional[str]:
    """
    Extrai J e TR do número CNJ e mapeia para o alias DataJud.
    Retorna None se não reconhecido.
    """
    m = PROCESS_NUMBER_RE.match(numero.strip()) or PROCESS_NUMBER_RE.search(numero)
    if not m:
        return None
    j  = m.group(4)   # segmento de justiça
    tr = m.group(5)   # código do tribunal
    try:
        tr_int = int(tr)
    except ValueError:
        return None

    key = f"{j}.{tr_int:02d}"
    return ALIAS_MAP.get(key)

def detect_process_numbers(text: str) -> List[str]:
    if not text:
        return []
    found = PROCESS_NUMBER_RE.findall(text)
    out, seen = [], set()
    for groups in found:
        full = f"{groups[0]}-{groups[1]}.{groups[2]}.{groups[3]}.{groups[4]}.{groups[5]}"
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out

def normalize_process_number(numero: str) -> str:
    return re.sub(r"\D", "", numero or "")

# Detecção de tribunal mencionado em linguagem natural
TRIBUNAL_KEYWORDS: Dict[str, str] = {
    "trt1": "api_publica_trt1",   "rio de janeiro": "api_publica_trt1",
    "trt2": "api_publica_trt2",   "são paulo": "api_publica_trt2", "sao paulo": "api_publica_trt2",
    "trt3": "api_publica_trt3",   "minas gerais": "api_publica_trt3", "mg": "api_publica_trt3",
    "trt4": "api_publica_trt4",   "rio grande do sul": "api_publica_trt4", "rs": "api_publica_trt4",
    "trt5": "api_publica_trt5",   "bahia": "api_publica_trt5",
    "trt6": "api_publica_trt6",   "pernambuco": "api_publica_trt6",
    "trt7": "api_publica_trt7",   "ceará": "api_publica_trt7", "ceara": "api_publica_trt7",
    "trt9": "api_publica_trt9",   "paraná": "api_publica_trt9", "parana": "api_publica_trt9",
    "trt15": "api_publica_trt15", "campinas": "api_publica_trt15",
    "tst": "api_publica_tst",
    "trf1": "api_publica_trf1",   "trf2": "api_publica_trf2",
    "trf3": "api_publica_trf3",   "trf4": "api_publica_trf4",
    "stj": "api_publica_stj",     "stf": "api_publica_stf",
    "tjsp": "api_publica_tjsp",   "tjrj": "api_publica_tjrj",
    "tjmg": "api_publica_tjmg",   "tjrs": "api_publica_tjrs",
    "tjpr": "api_publica_tjpr",   "tjba": "api_publica_tjba",
}

def detect_tribunal_from_message(message: str) -> Optional[str]:
    msg = message.lower()
    for keyword, alias in TRIBUNAL_KEYWORDS.items():
        if keyword in msg:
            return alias
    return None

# ─────────────────────────────────────────────
# Códigos TPU
# ─────────────────────────────────────────────
CODIGOS_SENTENCA = {11, 193, 198, 199, 17, 14, 15, 16}
CODIGOS_DECISAO_INTERLOCUTORIA = {85, 26, 51, 60, 61, 87, 1038}
CODIGOS_ACORDAO = {941, 237, 196, 7, 8, 9}

ASSUNTOS_HORAS_EXTRAS    = {1723, 14548, 14549, 14550, 14551}
ASSUNTOS_PERICULOSIDADE  = {1856, 14552, 14553}
ASSUNTOS_INSALUBRIDADE   = {1855, 14554}

INTENT_PROCESS_FULL  = ["andamento completo","histórico completo","timeline","linha do tempo","todas as movimentações","todos os andamentos","andamento detalhado","movimentações completas"]
INTENT_MAGISTRADO    = ["magistrado","juiz","juíza","quem sentenciou","quem julgou","quem decidiu","prolator"]
INTENT_BANCO_DECISOES = ["banco de decisões","processos similares","processos semelhantes","decisões do juiz","decisões da vara","padrão decisório","como esse juiz decide","outros casos","outros processos"]
INTENT_TEMATICO      = ["horas extras","hora extra","adicional de periculosidade","periculosidade","insalubridade","análise temática"]
INTENT_CLASSIFICAR   = ["sentença","decisão interlocutória","acórdão","tipo de decisão","classifica","separar decisões"]

def detect_intent(message: str) -> str:
    msg = message.lower()
    if any(k in msg for k in INTENT_BANCO_DECISOES):  return "banco_decisoes"
    if any(k in msg for k in INTENT_MAGISTRADO):       return "magistrado"
    if any(k in msg for k in INTENT_TEMATICO):         return "tematico"
    if any(k in msg for k in INTENT_CLASSIFICAR):      return "classificar"
    if any(k in msg for k in INTENT_PROCESS_FULL):     return "andamento_completo"
    return "resumo"

# ─────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────
def build_system_prompt(extra_context: str = "") -> str:
    base = OS_6_1_PROMPT if OS_6_1_PROMPT else (
        "Você é um assistente jurídico especializado em Direito do Trabalho brasileiro. "
        "Responda sempre em português, de forma técnica, clara e objetiva. "
        "Nunca prometa resultados. Separe fato de hipótese. "
        "Cite fundamentos legais (CLT, súmulas TST/TRT) quando relevante."
    )
    mandate = (f"\n\nEste sistema opera para: {MANDATARIA_NOME} — {MANDATARIA_OAB}. "
               "Análises são assistivas, sujeitas à revisão do advogado responsável."
               if MANDATARIA_NOME or MANDATARIA_OAB else "")
    ctx = f"\n\n===CONTEXTO===\n{extra_context}\n===FIM===" if extra_context else ""
    return base + mandate + ctx

# ─────────────────────────────────────────────
# FastAPI
# ─────────────────────────────────────────────
app = FastAPI(title="SM OS Chat", version="5.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if ALLOWED_ORIGIN == "*" else [ALLOWED_ORIGIN],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
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
    size: int = 10
    cursor: Optional[str] = None

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def auth_or_401(key: Optional[str]):
    if DEMO_KEY and key != DEMO_KEY:
        raise HTTPException(status_code=401, detail="Não autorizado")

def get_session(sid: str) -> dict:
    return SESSIONS.setdefault(sid, {
        "messages": [], "uploaded_contexts": [],
        "last_process": None, "last_process_numero": None,
        "last_alias": None,
    })

def compact_text(text: str, limit: int = 6000) -> str:
    return (text or "").strip()[:limit]

def extract_text_from_upload(file: UploadFile, data: bytes) -> str:
    fn = (file.filename or "").lower()
    if fn.endswith((".txt", ".md")):
        return data.decode("utf-8", errors="ignore")
    if fn.endswith(".pdf") and PdfReader:
        try:
            r = PdfReader(io.BytesIO(data))
            return "\n".join(p.extract_text() or "" for p in r.pages[:40]).strip()
        except Exception:
            return ""
    if fn.endswith(".docx") and DocxDocument:
        try:
            d = DocxDocument(io.BytesIO(data))
            return "\n".join(p.text for p in d.paragraphs if p.text).strip()
        except Exception:
            return ""
    return ""

# ─────────────────────────────────────────────
# Classificadores
# ─────────────────────────────────────────────
def classify_movimento(mov: dict) -> str:
    codigo = mov.get("codigoNacional")
    if not codigo and isinstance(mov.get("movimentoNacional"), dict):
        codigo = mov["movimentoNacional"].get("codigo")
    if codigo:
        if codigo in CODIGOS_SENTENCA:              return "sentenca"
        if codigo in CODIGOS_DECISAO_INTERLOCUTORIA: return "decisao_interlocutoria"
        if codigo in CODIGOS_ACORDAO:               return "acordao"
    nome = (mov.get("nome") or "").lower()
    if any(w in nome for w in ["sentença","sentenca","procedente","improcedente","julgament"]):
        return "sentenca"
    if any(w in nome for w in ["acórdão","acordao","recurso provido","recurso improvido"]):
        return "acordao"
    if any(w in nome for w in ["decisão","decisao","despacho","liminar","tutela"]):
        return "decisao_interlocutoria"
    return "outro"

def extract_magistrado(movimentos: List[dict]) -> Optional[str]:
    for mov in movimentos:
        mag = mov.get("magistradoProlator") or mov.get("responsavelMovimento")
        if mag:
            nome = mag.get("nome") or mag.get("nomeServidor") or str(mag) if isinstance(mag, dict) else str(mag)
            if nome and len(nome) > 3:
                return nome
    return None

# ─────────────────────────────────────────────
# DataJud Service
# ─────────────────────────────────────────────
class DataJudError(Exception):
    pass

class DataJudService:
    def _headers(self):
        h = {"Content-Type": "application/json"}
        if DATAJUD_API_KEY:
            h["Authorization"] = f"APIKey {DATAJUD_API_KEY}"
        return h

    def _post(self, alias: str, payload: dict) -> dict:
        if not DATAJUD_ENABLED:
            raise DataJudError("DataJud desabilitado (DATAJUD_ENABLED=false).")
        if not DATAJUD_API_KEY:
            raise DataJudError("DATAJUD_API_KEY não configurado.")
        if not alias:
            raise DataJudError("Alias do tribunal não identificado.")
        url = f"{DATAJUD_BASE_URL}/{alias}/_search"
        try:
            r = requests.post(url, headers=self._headers(), json=payload, timeout=DATAJUD_TIMEOUT_S)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            body = e.response.text if e.response else ""
            raise DataJudError(f"HTTP {getattr(e.response,'status_code','?')}: {body[:600]}")
        except requests.RequestException as e:
            raise DataJudError(f"Falha de conexão: {e}")

    def search(self, alias: str, query: dict, size: int = 10,
               sort: list = None, source_fields: list = None) -> dict:
        payload: dict = {
            "size":  min(size, 50),
            "query": query,
            "sort":  sort or [{DATAJUD_SORT_FIELD: "desc"}],
        }
        if source_fields:
            payload["_source"] = source_fields
        return self._post(alias, payload)

    def get_process(self, numero: str, alias: str) -> dict:
        return self.search(
            alias=alias,
            query={"match": {"numeroProcesso": normalize_process_number(numero)}},
            size=1,
        )

    def extract_sources(self, raw: dict) -> List[dict]:
        return [h.get("_source") or {} for h in
                ((raw or {}).get("hits") or {}).get("hits") or []]

    def normalize(self, source: dict) -> dict:
        movs   = source.get("movimentos") or []
        movs_s = sorted(movs, key=lambda x: x.get("dataHora") or "", reverse=True)
        orgao  = source.get("orgaoJulgador") or {}
        classe = source.get("classe") or {}
        assuntos = source.get("assuntos") or []
        return {
            "numero_processo":          source.get("numeroProcesso"),
            "tribunal":                 source.get("tribunal"),
            "grau":                     source.get("grau"),
            "data_ajuizamento":         source.get("dataAjuizamento"),
            "ultima_atualizacao":       source.get("dataHoraUltimaAtualizacao"),
            "valor_causa":              source.get("valorCausa"),
            "classe_nome":              classe.get("nome"),
            "orgao_julgador":           orgao.get("nome"),
            "assuntos":                 [a.get("nome") for a in assuntos if a.get("nome")],
            "assuntos_codigos":         [a.get("codigo") for a in assuntos if a.get("codigo")],
            "movimentos_total":         len(movs),
            "magistrado":               extract_magistrado(movs_s),
            "ultima_movimentacao_nome": (movs_s[0] if movs_s else {}).get("nome"),
            "ultima_movimentacao_data": (movs_s[0] if movs_s else {}).get("dataHora", "")[:10],
            "movimentos_todos":         movs_s,
            "sentencas":                [m for m in movs_s if classify_movimento(m) == "sentenca"],
            "decisoes_interlocutorias": [m for m in movs_s if classify_movimento(m) == "decisao_interlocutoria"],
            "acordaos":                 [m for m in movs_s if classify_movimento(m) == "acordao"],
        }

DATAJUD = DataJudService()

# ─────────────────────────────────────────────
# Context builders
# ─────────────────────────────────────────────
def _fmt(mov: dict, tipo: bool = False) -> str:
    data = (mov.get("dataHora") or "")[:10]
    nome = mov.get("nome") or "Movimentação"
    t    = f" [{classify_movimento(mov).replace('_',' ').upper()}]" if tipo else ""
    return f"  • {data}{t} — {nome}"

def ctx_resumo(proc: dict, alias: str = "") -> str:
    tribunal_nome = alias_para_nome(alias) if alias else proc.get("tribunal", "")
    lines = [
        f"PROCESSO: {proc['numero_processo']}",
        f"Tribunal: {tribunal_nome} | Grau: {proc['grau']}",
        f"Classe: {proc['classe_nome']} | Vara: {proc['orgao_julgador']}",
        f"Assuntos: {', '.join(proc['assuntos']) or 'n/d'}",
        f"Ajuizamento: {proc['data_ajuizamento'] or 'n/d'} | Última atualização: {proc['ultima_atualizacao'] or 'n/d'}",
        f"Magistrado identificado: {proc['magistrado'] or 'não disponível via API'}",
        f"Última movimentação: {proc['ultima_movimentacao_nome']} ({proc['ultima_movimentacao_data']})",
        f"Total movimentações: {proc['movimentos_total']}",
        "", "ÚLTIMAS 10 MOVIMENTAÇÕES:",
    ] + [_fmt(m) for m in proc["movimentos_todos"][:10]]
    return "\n".join(lines)

def ctx_completo(proc: dict, alias: str = "") -> str:
    tribunal_nome = alias_para_nome(alias) if alias else proc.get("tribunal", "")
    lines = [
        f"ANDAMENTO COMPLETO — {proc['numero_processo']} | {tribunal_nome}",
        f"Vara: {proc['orgao_julgador']} | Magistrado: {proc['magistrado'] or 'n/d'}",
        f"Ajuizamento: {proc['data_ajuizamento'] or 'n/d'} | Total: {proc['movimentos_total']} movimentos",
        "",
        f"▶ SENTENÇAS ({len(proc['sentencas'])}):",
    ] + ([_fmt(m) for m in proc["sentencas"]] or ["  (nenhuma)"]) + [
        f"\n▶ ACÓRDÃOS ({len(proc['acordaos'])}):",
    ] + ([_fmt(m) for m in proc["acordaos"]] or ["  (nenhum)"]) + [
        f"\n▶ DECISÕES INTERLOCUTÓRIAS ({len(proc['decisoes_interlocutorias'])}):",
    ] + ([_fmt(m) for m in proc["decisoes_interlocutorias"][:10]] or ["  (nenhuma)"]) + [
        f"\n▶ HISTÓRICO CRONOLÓGICO COMPLETO:",
    ] + [_fmt(m, True) for m in proc["movimentos_todos"]]
    return "\n".join(lines)

def ctx_magistrado(proc: dict) -> str:
    detalhes = []
    for m in proc["movimentos_todos"]:
        mag = m.get("magistradoProlator") or m.get("responsavelMovimento")
        if mag:
            detalhes.append(f"  • {(m.get('dataHora') or '')[:10]} — {m.get('nome')} | {mag}")
    return "\n".join([
        f"MAGISTRADO — {proc['numero_processo']}",
        f"Vara: {proc['orgao_julgador']}",
        f"Campo magistradoProlator (API): {proc['magistrado'] or 'não disponível'}",
        "",
        "Movimentos com magistrado nos dados brutos:"
        if detalhes else "Nenhum movimento retornou magistradoProlator nesta consulta.",
    ] + detalhes[:20])

def ctx_classificacao(proc: dict) -> str:
    return "\n".join([
        f"CLASSIFICAÇÃO — {proc['numero_processo']}",
        f"Total movimentos: {proc['movimentos_total']}",
        f"\nSENTENÇAS ({len(proc['sentencas'])}):",
    ] + ([_fmt(m) for m in proc["sentencas"]] or ["  (nenhuma)"]) + [
        f"\nACÓRDÃOS ({len(proc['acordaos'])}):",
    ] + ([_fmt(m) for m in proc["acordaos"]] or ["  (nenhum)"]) + [
        f"\nDECISÕES INTERLOCUTÓRIAS ({len(proc['decisoes_interlocutorias'])}):",
    ] + ([_fmt(m) for m in proc["decisoes_interlocutorias"][:15]] or ["  (nenhuma)"]))

def ctx_banco(processos: List[dict], orgao: str, assunto: str) -> str:
    lines = [f"BANCO DE DECISÕES — {orgao} | Tema: {assunto}", f"Processos: {len(processos)}", ""]
    for s in processos:
        p = DATAJUD.normalize(s)
        lines += [
            f"── {p['numero_processo']} | Magistrado: {p['magistrado'] or 'n/d'}",
            f"   Assuntos: {', '.join(p['assuntos'][:3])} | Sentenças: {len(p['sentencas'])}",
            f"   Última mov.: {p['ultima_movimentacao_nome'] or 'n/d'}", "",
        ]
    return "\n".join(lines)

def ctx_tematico(processos: List[dict], tema: str) -> str:
    t_sent = t_acord = t_proc = 0
    lines = [f"ANÁLISE TEMÁTICA — {tema} | Processos: {len(processos)}", ""]
    for s in processos:
        p = DATAJUD.normalize(s)
        t_sent  += len(p["sentencas"])
        t_acord += len(p["acordaos"])
        for sent in p["sentencas"]:
            if "procedente" in (sent.get("nome") or "").lower() and "improcedente" not in (sent.get("nome") or "").lower():
                t_proc += 1
        lines += [
            f"── {p['numero_processo']} | {p['orgao_julgador']} | Magistrado: {p['magistrado'] or 'n/d'}",
            f"   {', '.join(p['assuntos'][:3])} | Sentenças: {len(p['sentencas'])}",
            f"   Última: {p['ultima_movimentacao_nome'] or 'n/d'}", "",
        ]
    lines += ["── CONSOLIDADO:", f"   Sentenças: {t_sent} | Acórdãos: {t_acord} | Procedências estimadas: {t_proc}"]
    return "\n".join(lines)

INSTRUCAO: Dict[str, str] = {
    "resumo":            "Realize análise jurídica completa conforme OS 6.1. Fase processual, riscos, próximos passos, alertas. Responda em português.",
    "andamento_completo":"Apresente andamento completo em ordem cronológica. Organize por sentenças, acórdãos, decisões interlocutórias. Comente marcos processuais. Responda em português.",
    "magistrado":        "Identifique o magistrado responsável. Se não disponível via API, informe a limitação e o órgão julgador. Analise padrão decisório inferível. Responda em português.",
    "banco_decisoes":    "Analise os processos semelhantes da mesma vara. Identifique padrão decisório, taxa de procedência e tendências estratégicas. Responda em português.",
    "tematico":          "Realize análise temática. Taxa de procedência, magistrados recorrentes, tendências da vara/tribunal no tema. Oriente estratégia. Responda em português.",
    "classificar":       "Classifique e explique cada tipo de decisão. Impacto jurídico de cada uma e expectativas das próximas fases. Responda em português.",
}

# ─────────────────────────────────────────────
# OpenAI
# ─────────────────────────────────────────────
def call_openai(messages: List[dict], temperature: float = 0.15) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY não configurado.")
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": OPENAI_MODEL, "messages": messages, "temperature": temperature},
        timeout=90,
    )
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"Erro OpenAI: {(e.response.text if e.response else '')[:600]}")
    return (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip() or "Sem resposta."

# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────
@app.get("/ping")
def ping():
    return {"ok": True}

@app.get("/health")
def health():
    return {
        "ok": True, "version": "5.0.0", "model": OPENAI_MODEL,
        "os_61_loaded": bool(OS_6_1_PROMPT),
        "datajud": {"enabled": DATAJUD_ENABLED, "tribunais_suportados": len(ALIAS_MAP)},
    }

@app.get("/tribunais")
def listar_tribunais():
    """Lista todos os tribunais suportados."""
    return {"tribunais": [
        {"alias": v, "nome": alias_para_nome(v), "chave_cnj": k}
        for k, v in sorted(ALIAS_MAP.items())
    ]}

@app.post("/session/new", response_model=SessionOut)
def session_new(x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    sid = str(uuid.uuid4())
    SESSIONS[sid] = {"messages": [], "uploaded_contexts": [],
                     "last_process": None, "last_process_numero": None, "last_alias": None}
    return SessionOut(session_id=sid, state={})

@app.post("/upload")
async def upload(session_id: str = Form(...), file: UploadFile = File(...),
                 x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    session = get_session(session_id)
    data = await file.read()
    text = compact_text(extract_text_from_upload(file, data), 12000)
    session["uploaded_contexts"].append({"filename": file.filename, "text": text})
    return {"ok": True, "message": f"Arquivo '{file.filename}' anexado.", "filename": file.filename}

@app.post("/chat", response_model=ChatOut)
def chat_endpoint(payload: ChatIn, x_demo_key: Optional[str] = Header(default=None)):
    auth_or_401(x_demo_key)
    session = get_session(payload.session_id)
    message = (payload.message or "").strip()
    state   = payload.state or {}

    session["messages"].append({"role": "user", "content": message})
    session["messages"] = session["messages"][-20:]

    # ── 1. Detectar processo e tribunal ───────────────────────────────
    numbers = detect_process_numbers(message)

    if DATAJUD_ENABLED and (numbers or (
        session.get("last_process_numero") and
        any(k in message.lower() for k in
            INTENT_MAGISTRADO + INTENT_BANCO_DECISOES + INTENT_TEMATICO +
            INTENT_CLASSIFICAR + INTENT_PROCESS_FULL + ["processo","andamento"])
    )):
        # Resolver número e alias
        numero = numbers[0] if numbers else session.get("last_process_numero")
        intent = detect_intent(message)

        # Prioridade: (1) inferido do número CNJ, (2) mencionado na mensagem, (3) sessão anterior, (4) default
        alias = (
            (infer_alias_from_cnj(numero) if numero else None)
            or detect_tribunal_from_message(message)
            or session.get("last_alias")
            or DATAJUD_DEFAULT_ALIAS
        )

        if numero:
            try:
                raw   = DATAJUD.get_process(numero, alias)
                items = DATAJUD.extract_sources(raw)

                if not items:
                    # Tenta inferir e informar o tribunal consultado
                    tribunal_tentado = alias_para_nome(alias)
                    reply = (
                        f"Não encontrei o processo **{numero}** no **{tribunal_tentado}**.\n\n"
                        f"Se o processo for de outro tribunal, informe qual. Por exemplo:\n"
                        f'> *"Processo {numero} no TRT3"* ou *"no TJSP"*'
                    )
                    session["messages"].append({"role": "assistant", "content": reply})
                    return ChatOut(message=reply, state=state)

                proc = DATAJUD.normalize(items[0])
                session["last_process"]         = proc
                session["last_process_numero"]  = numero
                session["last_alias"]           = alias

                # ── Montar contexto conforme intent ────────────────
                if intent == "banco_decisoes":
                    orgao   = proc["orgao_julgador"] or ""
                    assunto = (proc["assuntos"] or [""])[0]
                    assunto_cod = (proc["assuntos_codigos"] or [None])[0]
                    must = [{"match": {"orgaoJulgador.nome": orgao}}]
                    if assunto_cod:
                        must.append({"match": {"assuntos.codigo": assunto_cod}})
                    try:
                        r2  = DATAJUD.search(alias, {"bool": {"must": must}}, size=8)
                        s2  = [f for f in DATAJUD.extract_sources(r2)
                               if normalize_process_number(f.get("numeroProcesso","")) != normalize_process_number(numero)][:6]
                        context = ctx_resumo(proc, alias) + "\n\n" + ctx_banco(s2, orgao, assunto)
                    except Exception:
                        context = ctx_resumo(proc, alias)

                elif intent == "tematico":
                    ml = message.lower()
                    if "periculosidade" in ml:
                        codigos, tema = ASSUNTOS_PERICULOSIDADE, "Adicional de Periculosidade"
                    elif "insalubridade" in ml:
                        codigos, tema = ASSUNTOS_INSALUBRIDADE, "Adicional de Insalubridade"
                    else:
                        codigos, tema = ASSUNTOS_HORAS_EXTRAS, "Horas Extras"
                    try:
                        should = [{"match": {"assuntos.codigo": c}} for c in codigos]
                        r2  = DATAJUD.search(alias, {"bool": {"should": should, "minimum_should_match": 1}}, size=12)
                        s2  = DATAJUD.extract_sources(r2)
                        context = ctx_resumo(proc, alias) + "\n\n" + ctx_tematico(s2, tema)
                    except Exception:
                        context = ctx_resumo(proc, alias)

                elif intent == "andamento_completo":
                    context = ctx_completo(proc, alias)
                elif intent == "magistrado":
                    context = ctx_magistrado(proc)
                elif intent == "classificar":
                    context = ctx_classificacao(proc)
                else:
                    context = ctx_resumo(proc, alias)

                system_prompt = build_system_prompt(extra_context=context)
                msgs = [{"role": "system", "content": system_prompt}]
                for item in session["messages"][-6:]:
                    if item["role"] in {"user", "assistant"}:
                        msgs.append(item)
                instrucao = INSTRUCAO.get(intent, INSTRUCAO["resumo"])
                msgs[-1] = {"role": "user", "content": f"{message}\n\n[INSTRUÇÃO: {instrucao}]"}

                reply = call_openai(msgs, temperature=0.15)
                session["messages"].append({"role": "assistant", "content": reply})
                return ChatOut(message=reply, state=state)

            except DataJudError as e:
                reply = f"Erro ao consultar o DataJud: {str(e)}"
                session["messages"].append({"role": "assistant", "content": reply})
                return ChatOut(message=reply, state=state)

    # ── 2. Chat jurídico livre com OS 6.1 ────────────────────────────
    uploaded_ctx = "\n\n".join(
        f"[{x.get('filename')}]\n{compact_text(x.get('text') or '', 4000)}"
        for x in session.get("uploaded_contexts", [])[-3:] if x.get("text")
    ).strip()

    system_prompt = build_system_prompt(extra_context=uploaded_ctx)
    msgs = [{"role": "system", "content": system_prompt}]
    for item in session["messages"][-12:]:
        if item["role"] in {"user", "assistant"}:
            msgs.append(item)
    try:
        reply = call_openai(msgs, temperature=0.2)
    except Exception as e:
        reply = f"Erro: {str(e)}"

    session["messages"].append({"role": "assistant", "content": reply})
    session["messages"] = session["messages"][-20:]
    return ChatOut(message=reply, state=state)
