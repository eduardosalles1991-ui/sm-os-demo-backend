"""
SM OS Chat — Jurimetrix v8.0
════════════════════════════════════════════════════════
Backend completo: DataJud + MNI/PJe + OS 6.1 + Auth + Asaas
Domínio: https://jurimetrix.com
════════════════════════════════════════════════════════
"""
import os, re, io, uuid, json, base64, textwrap, logging
from typing import Any, Dict, List, Optional
import requests
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("smos")

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None
try:
    from docx import Document as DocxDocument
except Exception:
    DocxDocument = None
try:
    from relatorio_pdf import build_relatorio_pdf, parse_analise_gpt
    PDF_AVAILABLE = True
except Exception:
    PDF_AVAILABLE = False

# ═══════════════════════════════════════════════════════
# ENVIRONMENT
# ═══════════════════════════════════════════════════════
def _e(k, d=""): return (os.getenv(k) or d).strip()

OPENAI_API_KEY        = _e("OPENAI_API_KEY")
OPENAI_MODEL          = _e("OPENAI_MODEL", "gpt-4o")
DEMO_KEY              = _e("DEMO_KEY")
ALLOWED_ORIGIN        = _e("ALLOWED_ORIGIN", "https://jurimetrix.com")
OS_6_1_PROMPT         = _e("OS_6_1_PROMPT")
MANDATARIA_NOME       = _e("MANDATARIA_NOME")
MANDATARIA_OAB        = _e("MANDATARIA_OAB")

DATAJUD_ENABLED       = os.getenv("DATAJUD_ENABLED","false").lower() == "true"
DATAJUD_BASE_URL      = _e("DATAJUD_BASE_URL","https://api-publica.datajud.cnj.jus.br").rstrip("/")
DATAJUD_API_KEY       = _e("DATAJUD_API_KEY")
DATAJUD_TIMEOUT_S     = int(os.getenv("DATAJUD_TIMEOUT_S","25"))
DATAJUD_DEFAULT_ALIAS = _e("DATAJUD_DEFAULT_ALIAS","api_publica_trt2")
DATAJUD_SORT_FIELD    = _e("DATAJUD_SORT_FIELD","dataHoraUltimaAtualizacao")

MNI_ENABLED   = os.getenv("MNI_ENABLED","false").lower() == "true"
MNI_BASE_URL  = _e("MNI_BASE_URL","https://pje.trt2.jus.br/pje/intercomunicacao")
MNI_USERNAME  = _e("MNI_USERNAME")
MNI_PASSWORD  = _e("MNI_PASSWORD")
MNI_TIMEOUT_S = int(os.getenv("MNI_TIMEOUT_S","30"))

# Supabase
SUPABASE_URL         = _e("SUPABASE_URL")
SUPABASE_SERVICE_KEY = _e("SUPABASE_SERVICE_KEY")
SUPABASE_JWT_SECRET  = _e("SUPABASE_JWT_SECRET")

# Mercado Pago
MP_ACCESS_TOKEN = _e("MP_ACCESS_TOKEN")
MP_PUBLIC_KEY   = _e("MP_PUBLIC_KEY")

try:
    import supabase_client as SB
    SUPABASE_OK = SB.is_configured()
    if SUPABASE_OK:
        log.info("✅ Supabase configurado.")
    else:
        log.warning("⚠️  Supabase: SUPABASE_URL ou SUPABASE_SERVICE_KEY faltando.")
except Exception as _se:
    SB = None
    SUPABASE_OK = False
    log.warning(f"⚠️  supabase_client não carregado: {_se}")

def get_user_from_request(authorization: Optional[str]) -> Optional[str]:
    """Extrai user_id do Bearer token Supabase."""
    if not SUPABASE_OK or not authorization: return None
    return SB.get_user_id_from_token(authorization)

# ═══════════════════════════════════════════════════════
# ALIAS MAP — todos os tribunais brasileiros
# ═══════════════════════════════════════════════════════
ALIAS_MAP: Dict[str,str] = {
    "5.00":"api_publica_tst",
    **{f"5.{i:02d}":f"api_publica_trt{i}" for i in range(1,25)},
    **{f"4.{i:02d}":f"api_publica_trf{i}" for i in range(1,7)},
    "8.01":"api_publica_tjac","8.02":"api_publica_tjal","8.03":"api_publica_tjam",
    "8.04":"api_publica_tjap","8.05":"api_publica_tjba","8.06":"api_publica_tjce",
    "8.07":"api_publica_tjdft","8.08":"api_publica_tjes","8.09":"api_publica_tjgo",
    "8.10":"api_publica_tjma","8.11":"api_publica_tjmg","8.12":"api_publica_tjms",
    "8.13":"api_publica_tjmt","8.14":"api_publica_tjpa","8.15":"api_publica_tjpb",
    "8.16":"api_publica_tjpe","8.17":"api_publica_tjpi","8.18":"api_publica_tjpr",
    "8.19":"api_publica_tjrj","8.20":"api_publica_tjrn","8.21":"api_publica_tjro",
    "8.22":"api_publica_tjrr","8.23":"api_publica_tjrs","8.24":"api_publica_tjsc",
    "8.25":"api_publica_tjse","8.26":"api_publica_tjsp","8.27":"api_publica_tjto",
    "3.00":"api_publica_tse","1.00":"api_publica_stj","1.01":"api_publica_stf",
    "9.00":"api_publica_stm",
}
ALIAS_NOME: Dict[str,str] = {
    "api_publica_trt1":"TRT1 (RJ)","api_publica_trt2":"TRT2 (SP)",
    "api_publica_trt3":"TRT3 (MG)","api_publica_trt4":"TRT4 (RS)",
    "api_publica_trt5":"TRT5 (BA)","api_publica_trt6":"TRT6 (PE)",
    "api_publica_trt7":"TRT7 (CE)","api_publica_trt9":"TRT9 (PR)",
    "api_publica_trt10":"TRT10 (DF/TO)","api_publica_trt15":"TRT15 (Campinas)",
    "api_publica_tst":"TST","api_publica_trf1":"TRF1","api_publica_trf2":"TRF2",
    "api_publica_trf3":"TRF3","api_publica_trf4":"TRF4","api_publica_trf5":"TRF5",
    "api_publica_tjsp":"TJSP","api_publica_tjrj":"TJRJ","api_publica_tjmg":"TJMG",
    "api_publica_tjrs":"TJRS","api_publica_tjpr":"TJPR","api_publica_stj":"STJ",
    "api_publica_stf":"STF",
}
TRIBUNAL_KW: Dict[str,str] = {
    "trt1":"api_publica_trt1","trt2":"api_publica_trt2","trt3":"api_publica_trt3",
    "trt4":"api_publica_trt4","trt5":"api_publica_trt5","trt6":"api_publica_trt6",
    "trt7":"api_publica_trt7","trt9":"api_publica_trt9","trt15":"api_publica_trt15",
    "tst":"api_publica_tst","trf1":"api_publica_trf1","trf2":"api_publica_trf2",
    "trf3":"api_publica_trf3","trf4":"api_publica_trf4",
    "tjsp":"api_publica_tjsp","tjrj":"api_publica_tjrj","tjmg":"api_publica_tjmg",
    "tjrs":"api_publica_tjrs","stj":"api_publica_stj","stf":"api_publica_stf",
    "sao paulo":"api_publica_trt2","minas gerais":"api_publica_trt3",
    "rio de janeiro":"api_publica_trt1","parana":"api_publica_trt9",
    "campinas":"api_publica_trt15",
}
def alias_nome(a:str)->str: return ALIAS_NOME.get(a,a)

# ═══════════════════════════════════════════════════════
# CNJ UTILS
# ═══════════════════════════════════════════════════════
PROC_RE = re.compile(r"\b(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})\b")

def detect_numbers(text:str)->List[str]:
    out,seen=[],set()
    for g in PROC_RE.findall(text or ""):
        n=f"{g[0]}-{g[1]}.{g[2]}.{g[3]}.{g[4]}.{g[5]}"
        if n not in seen: seen.add(n); out.append(n)
    return out

def norm_num(n:str)->str: return re.sub(r"\D","",n or "")

def infer_alias(numero:str)->Optional[str]:
    m=PROC_RE.search(numero or "")
    if not m: return None
    return ALIAS_MAP.get(f"{m.group(4)}.{int(m.group(5)):02d}")

def trib_from_msg(msg:str)->Optional[str]:
    low=msg.lower()
    for k,v in TRIBUNAL_KW.items():
        if k in low: return v
    return None

# ═══════════════════════════════════════════════════════
# SMART PROMPT ROUTER — 5 níveis
# ═══════════════════════════════════════════════════════
ESC_PROC_TRIGGERS = [
    "processos de","processos do","processos da",
    "todos os processos","quantos processos",
    "histórico processual de","listar processos de",
]
ESC_PESSOA_TRIGGERS = [
    "dados de","informações sobre","quem é","cpf de",
    "endereço de","telefone de","sócio de",
    "enriquecer dados","dados cadastrais",
]
ESC_EMPRESA_TRIGGERS = [
    "cnpj","empresa","razão social","sócios da empresa",
    "dados da empresa","situação cadastral",
]
ESC_ADV_TRIGGERS = [
    "advogado","oab","carteira oab","escritório de",
    "processos do advogado","quem é o advogado",
]

def detect_escavador_intent(msg: str) -> Optional[str]:
    if not ESCAVADOR_OK:
        return None
    m = msg.lower()
    if any(t in m for t in ESC_ADV_TRIGGERS):   return "advogado"
    if any(t in m for t in ESC_PROC_TRIGGERS):  return "processos"
    if any(t in m for t in ESC_EMPRESA_TRIGGERS):return "empresa"
    if any(t in m for t in ESC_PESSOA_TRIGGERS): return "pessoa"
    return None

def extract_escavador_query(msg: str, tipo: str) -> dict:
    import re
    q = {"tipo": tipo}
    cpf = re.search(r'\d{3}\.?\d{3}\.?\d{3}-?\d{2}', msg)
    if cpf: q["cpf"] = cpf.group()
    cnpj = re.search(r'\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}', msg)
    if cnpj: q["cnpj"] = cnpj.group()
    oab = re.search(r'OAB[/\s]*([A-Z]{2})[/\s]*(\d[\d.]+)', msg, re.IGNORECASE)
    if oab: q["oab"] = f"OAB/{oab.group(1).upper()} {oab.group(2)}"
    nome_quotes = None
    nome_prep   = re.search(r'(?:de|do|da|sobre|para)\s+([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+(?:\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+)+)', msg)
    if nome_quotes: q["nome"] = nome_quotes.group(1)
    elif nome_prep: q["nome"] = nome_prep.group(1)
    return q

BACEN_TRIGGERS = [
    "selic","inpc","ipca","tr ","taxa de juros","juros mora","correção monetária",
    "correcao monetaria","atualizar valor","calcular juros","índice econômico",
    "indice economico","juro de mora","atualização monetária","atualizacao monetaria",
    "quanto vale hoje","valor atualizado","verbas atualizadas","cálculo trabalhista",
    "calculo trabalhista","quanto seria hoje","fgts corrigido","multa corrigida",
]

def detect_bacen_intent(msg: str) -> Optional[str]:
    if not BACEN_OK: return None
    m = msg.lower()
    if any(t in m for t in BACEN_TRIGGERS): return "indices"
    import re
    if re.search(r'r\$\s*[\d.,]+', m) and any(w in m for w in ["corrig","atualiz","juros","mora"]):
        return "calculo"
    return None

# PDF auto-generation triggers
PDF_TRIGGERS = [
    "gerar pdf","gere pdf","gera pdf","gerar relatório","gere relatório","gera relatório",
    "gerar relatorio","gere relatorio","gera relatorio","relatório pdf","relatorio pdf",
    "quero o pdf","quero pdf","baixar pdf","download pdf","exportar pdf",
    "relatório completo","relatorio completo","gerar documento","documento pdf",
    "me envia o pdf","me manda o pdf","pdf do processo","relatório do processo",
    "relatorio do processo","gere o relatório","gera o relatório",
    "faz um pdf","faz o pdf","faz pdf","fazer pdf","fazer o pdf",
    "em pdf","no pdf","como pdf","formato pdf","salvar pdf","salvar em pdf",
    "necessito pdf","preciso do pdf","preciso pdf","preciso em pdf",
    "manda pdf","manda o pdf","envia pdf","envia o pdf",
    "cria um pdf","cria o pdf","criar pdf",
    "imprimir","para imprimir","versão para impressão",
]

def detect_pdf_intent(msg: str) -> bool:
    m = msg.lower()
    return any(t in m for t in PDF_TRIGGERS)

TRIVIAL_EXACT = {
    "olá","oi","ola","bom dia","boa tarde","boa noite","tudo bem","tudo bom",
    "obrigado","obrigada","valeu","ok","certo","entendi","show","perfeito",
    "ótimo","otimo","pode","sim","não","nao","hello","hi","thanks","thank you",
}
OS61_TRIGGERS = [
    "análise completa","analise completa","full analysis",
    "análise os 6.1","analise os 6.1","os 6.1","os61",
    "red team","análise de risco completa",
    "estratégia completa","análise completa com red team",
    "matriz de score","scores completos",
    "elabore","redija","minuta","petição","peticao","contestação","contestacao",
]
JUR_EXTRA_TRIGGERS = [
    "força da tese","forca da tese","risco jurídico","risco juridico",
    "viabilidade do caso","viabilidade jurídica","rentabilidade do caso",
    "tenho um caso","novo caso","quero abrir processo",
    "fui demitido","fui dispensado","rescisão indevida",
    "verbas trabalhistas","score",
]
DOC_MEDIO_TRIGGERS = [
    "analise","análise","analyze","pontos principais","principais pontos",
    "o que é importante","riscos desse documento","riscos do documento",
    "o que devo saber","avalie","avalia","avaliação",
    "implicações","consequências","posso usar","serve para","pertinente",
]
DOC_RESUMO_TRIGGERS = [
    "resume","resumo","resumir","summarize","summary",
    "explica","explain","o que diz","me conta",
    "do que se trata","do que trata","sobre o que","qual o assunto",
    "me fala sobre","me diga","síntese","sintese",
]
PROC_TRIGGERS = [
    "andamento","movimentações","movimentacoes","magistrado","juiz","juíza",
    "partes","banco de decisões","timeline","histórico do processo",
]

def classify_prompt(message:str, has_proc:bool)->str:
    msg=message.lower().strip()
    words=set(msg.split())
    if words & TRIVIAL_EXACT and len(message.split())<=6: return "simples"
    if any(t in msg for t in OS61_TRIGGERS):             return "os61"
    if any(t in msg for t in DOC_RESUMO_TRIGGERS):       return "doc_resumo"
    if any(t in msg for t in DOC_MEDIO_TRIGGERS):        return "doc_medio"
    if any(t in msg for t in JUR_EXTRA_TRIGGERS):        return "juridico"
    if has_proc or any(t in msg for t in PROC_TRIGGERS): return "juridico"
    jur=["direito","lei","clt","tst","trt","prazo","processo","trabalhista",
         "jurisprudência","jurisprudencia","súmula","sumula","artigo",
         "indenização","indenizacao","multa","rescisão","rescisao","fgts","verbas","contrato"]
    if any(t in msg for t in jur): return "juridico"
    return "simples"

PROMPT_CONV = (
    "Você é um assistente do escritório Salles & Mendes / Jurimetrix. "
    "Responda em português, de forma direta e cordial. Seja breve. "
    "O sistema Jurimetrix gera relatórios em PDF automaticamente quando solicitado."
)
PROMPT_JUR = (
    "Você é um assistente jurídico especializado em Direito do Trabalho brasileiro. "
    "Responda em português, de forma técnica, clara e objetiva. "
    "Nunca prometa resultados. Separe fato de hipótese. "
    "Cite fundamentos legais (CLT, súmulas TST/TRT) quando relevante. "
    "Nunca diga que não pode gerar PDF — o sistema gera automaticamente."
)
_FMT: Dict[str,str] = {
    "simples":    "Responda de forma breve e cordial, em no máximo 2-3 frases. Sem listas longas.",
    "doc_resumo": "Resumo direto em no máximo 4 parágrafos curtos. NÃO use scores, red team nem seções OS 6.1. Seja conciso.",
    "doc_medio":  "Responda com: 1) Síntese (3 linhas), 2) Pontos principais (bullets curtos), 3) Riscos/alertas (bullets curtos). Máximo 1 página. Sem 18 seções.",
    "juridico":   "Responda tecnicamente mas de forma CONCISA. Foco em: fase processual, última movimentação, riscos e próximos passos. Máximo 4-6 parágrafos. Sem OS 6.1 completo. Sem repetir informações.",
    "os61":       "Execute análise OS 6.1 completa: Classificação, Síntese, Análise Técnica, Força da Tese, Confiabilidade, Provas, Riscos, Cenários, Análise Econômica, Scores, Red Team, Estratégia, Ações Prioritárias, Pendências, Alertas, Reflexão Final.",
}

def build_system_prompt(level:str, ctx:str="")->str:
    base = (OS_6_1_PROMPT if OS_6_1_PROMPT else PROMPT_JUR) if level=="os61" else \
           (PROMPT_JUR if level in ("juridico","doc_medio") else PROMPT_CONV)
    mandate = (f"\n\nEste sistema opera para: {MANDATARIA_NOME} — {MANDATARIA_OAB}. "
               "Análises são assistivas, sujeitas à revisão do advogado responsável."
               if MANDATARIA_NOME or MANDATARIA_OAB else "")
    context = f"\n\n===CONTEXTO===\n{ctx}\n===FIM===" if ctx else ""
    return base + mandate + context

# ═══════════════════════════════════════════════════════
# INTENT DETECTION
# ═══════════════════════════════════════════════════════
INT_FULL  = ["andamento completo","histórico completo","timeline","todas as movimentações"]
INT_MAG   = ["magistrado","juiz","juíza","quem sentenciou","quem julgou","nome do juiz","prolator"]
INT_PART  = ["partes","quem são as partes","autor","reclamante","reclamado","réu","advogado"]
INT_BANCO = ["banco de decisões","processos similares","decisões do juiz","padrão decisório","outros casos"]
INT_TEM   = ["horas extras","hora extra","periculosidade","insalubridade","análise temática"]
INT_CL    = ["sentença","acórdão","decisão interlocutória","tipos de decisão","separar decisões"]

def detect_intent(msg:str)->str:
    m=msg.lower()
    if any(k in m for k in INT_BANCO): return "banco_decisoes"
    if any(k in m for k in INT_PART):  return "partes"
    if any(k in m for k in INT_MAG):   return "magistrado"
    if any(k in m for k in INT_TEM):   return "tematico"
    if any(k in m for k in INT_CL):    return "classificar"
    if any(k in m for k in INT_FULL):  return "andamento_completo"
    return "resumo"

# ═══════════════════════════════════════════════════════
# MNI / PJe CLIENT
# ═══════════════════════════════════════════════════════
class MNIError(Exception): pass

class MNIClient:
    def _envelope(self, numero:str)->str:
        return textwrap.dedent(f"""<?xml version="1.0" encoding="UTF-8"?>
        <soapenv:Envelope
          xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
          xmlns:mni="http://www.cnj.jus.br/servicos/sistemas/intercomunicacao/1.0">
          <soapenv:Header/>
          <soapenv:Body>
            <mni:consultarProcesso>
              <numeroProcesso>{numero}</numeroProcesso>
              <idConsultante>{MNI_USERNAME}</idConsultante>
              <senhaConsultante>{MNI_PASSWORD}</senhaConsultante>
              <movimentos>true</movimentos>
              <incluirDocumentos>false</incluirDocumentos>
            </mni:consultarProcesso>
          </soapenv:Body>
        </soapenv:Envelope>""")

    def consultar(self, numero:str)->Dict[str,Any]:
        if not MNI_ENABLED:         raise MNIError("MNI desabilitado.")
        if not MNI_USERNAME:        raise MNIError("MNI_USERNAME não configurado.")
        if not MNI_PASSWORD:        raise MNIError("MNI_PASSWORD não configurado.")
        try:
            r = requests.post(
                MNI_BASE_URL,
                data=self._envelope(numero).encode("utf-8"),
                headers={"Content-Type":"text/xml; charset=utf-8","SOAPAction":'"consultarProcesso"'},
                timeout=MNI_TIMEOUT_S,
            )
            r.raise_for_status()
        except requests.HTTPError as e:
            raise MNIError(f"HTTP {getattr(e.response,'status_code','?')}: {(e.response.text if e.response else '')[:300]}")
        except requests.RequestException as e:
            raise MNIError(f"Conexão MNI: {e}")
        return self._parse(r.text)

    def _parse(self, xml:str)->Dict[str,Any]:
        def first(tag:str)->Optional[str]:
            m=re.search(rf"<(?:[^:>]+:)?{re.escape(tag)}[^>]*>(.*?)</(?:[^:>]+:)?{re.escape(tag)}>",xml,re.DOTALL|re.IGNORECASE)
            return m.group(1).strip() if m else None
        result:Dict[str,Any]={"juiz":None,"polo_ativo":[],"polo_passivo":[],"advogados":[],"movimentos_mni":[],"valor_causa":None}
        result["juiz"]=first("magistrado") or first("nomeMagistrado")
        result["valor_causa"]=first("valorCausa")
        for bloco in re.findall(r"<(?:[^:>]+:)?polo[^>]*>(.*?)</(?:[^:>]+:)?polo>",xml,re.DOTALL|re.IGNORECASE):
            tipo=re.search(r'tipo["\s]*[=:]["\s]*([^"<>\s]+)',bloco,re.IGNORECASE)
            nomes=re.findall(r"<(?:[^:>]+:)?nome[^>]*>(.*?)</(?:[^:>]+:)?nome>",bloco,re.DOTALL|re.IGNORECASE)
            advs=re.findall(r"<(?:[^:>]+:)?advogado[^>]*>(.*?)</(?:[^:>]+:)?advogado>",bloco,re.DOTALL|re.IGNORECASE)
            pt=(tipo.group(1) if tipo else "").lower()
            ns=[n.strip() for n in nomes if n.strip()]
            if "at" in pt or "ativo" in pt or "reclamante" in pt: result["polo_ativo"].extend(ns)
            else: result["polo_passivo"].extend(ns)
            result["advogados"].extend([re.sub(r"<[^>]+>","",a).strip() for a in advs if a.strip()])
        for bloco in re.findall(r"<(?:[^:>]+:)?movimento[^>]*>(.*?)</(?:[^:>]+:)?movimento>",xml,re.DOTALL|re.IGNORECASE):
            dh=re.search(r"<(?:[^:>]+:)?dataHora[^>]*>(.*?)</",bloco,re.DOTALL|re.IGNORECASE)
            nd=re.search(r"<(?:[^:>]+:)?descricao[^>]*>(.*?)</",bloco,re.DOTALL|re.IGNORECASE)
            nd2=re.search(r"<(?:[^:>]+:)?nome[^>]*>(.*?)</",bloco,re.DOTALL|re.IGNORECASE)
            mag=re.search(r"<(?:[^:>]+:)?magistrado[^>]*>(.*?)</",bloco,re.DOTALL|re.IGNORECASE)
            result["movimentos_mni"].append({
                "dataHora":(dh.group(1) if dh else "").strip()[:19],
                "nome":(nd.group(1) if nd else (nd2.group(1) if nd2 else "")).strip(),
                "magistrado":(mag.group(1) if mag else "").strip() or None,
            })
        if not result["juiz"]:
            for m in result["movimentos_mni"]:
                if m.get("magistrado"): result["juiz"]=m["magistrado"]; break
        return result

MNI = MNIClient()

def enrich_with_mni(proc:dict, numero:str)->dict:
    proc["mni_status"]="disabled"; proc["mni_error"]=None; proc["movimentos_mni"]=[]; proc["movimentos_mni_total"]="N/A"
    if not MNI_ENABLED: return proc
    try:
        d=MNI.consultar(numero)
        proc["mni_status"]="ok"
        if d.get("juiz"):         proc["magistrado"]=d["juiz"]
        if d.get("polo_ativo"):   proc["polo_ativo"]=d["polo_ativo"]
        if d.get("polo_passivo"): proc["polo_passivo"]=d["polo_passivo"]
        if d.get("advogados"):    proc["advogados"]=d["advogados"]
        if d.get("valor_causa") and not proc.get("valor_causa"): proc["valor_causa"]=d["valor_causa"]
        proc["movimentos_mni"]=d.get("movimentos_mni") or []
        proc["movimentos_mni_total"]=len(proc["movimentos_mni"])
    except MNIError as e:
        proc["mni_status"]="error"; proc["mni_error"]=str(e)
    return proc


def enrich_with_escavador(proc: dict, numero: str) -> dict:
    """
    Enriquece dados do processo com Escavador quando DataJud/MNI
    não trouxe partes, advogados ou magistrado.
    """
    if not ESCAVADOR_OK or not ESC:
        return proc

    has_partes = bool(proc.get("polo_ativo")) or bool(proc.get("polo_passivo"))
    has_advs = bool(proc.get("advogados"))
    if has_partes and has_advs:
        return proc

    try:
        numero_limpo = re.sub(r'\D', '', numero)

        # Busca pelo número CNJ (método correto)
        result = ESC.ESCAVADOR.buscar_processo_por_numero(numero)

        if not result or result.get("error"):
            return proc

        # V2 retorna objeto direto do processo
        # V1 retorna lista em items/data
        items = result.get("items") or result.get("data") or []
        
        # Se o resultado é o processo direto (V2)
        if not items and (result.get("numero_cnj") or result.get("numero")):
            items = [result]

        # Percorre resultados buscando o processo exato
        for item in (items if isinstance(items, list) else [items] if isinstance(items, dict) else []):
            num_item = re.sub(r'\D', '', str(item.get("numero_cnj") or item.get("numero_novo") or item.get("numero") or ""))
            
            # Match exato ou parcial
            if num_item == numero_limpo or numero_limpo in num_item or num_item in numero_limpo:
                # Extrair partes de múltiplos formatos
                if not proc.get("polo_ativo"):
                    pa = (item.get("titulo_polo_ativo") or 
                          item.get("polo_ativo") or
                          item.get("partes_polo_ativo") or "")
                    if isinstance(pa, list):
                        proc["polo_ativo"] = [p.get("nome", p) if isinstance(p, dict) else str(p) for p in pa]
                    elif isinstance(pa, str) and pa:
                        proc["polo_ativo"] = [pa]

                if not proc.get("polo_passivo"):
                    pp = (item.get("titulo_polo_passivo") or
                          item.get("polo_passivo") or
                          item.get("partes_polo_passivo") or "")
                    if isinstance(pp, list):
                        proc["polo_passivo"] = [p.get("nome", p) if isinstance(p, dict) else str(p) for p in pp]
                    elif isinstance(pp, str) and pp:
                        proc["polo_passivo"] = [pp]

                # Advogados
                if not proc.get("advogados"):
                    advs = item.get("advogados") or []
                    if advs:
                        proc["advogados"] = [
                            a.get("nome", a) if isinstance(a, dict) else str(a)
                            for a in advs[:5]
                        ]

                # Envolvidos (formato alternativo)
                envolvidos = item.get("envolvidos") or item.get("partes") or []
                for env in envolvidos:
                    if not isinstance(env, dict):
                        continue
                    nome = env.get("nome") or ""
                    tipo = (env.get("tipo_envolvido") or env.get("tipo") or env.get("polo") or "").lower()
                    if not nome:
                        continue
                    if ("ativo" in tipo or "reclamante" in tipo or "autor" in tipo) and not proc.get("polo_ativo"):
                        proc["polo_ativo"] = [nome]
                    elif ("passivo" in tipo or "reclamado" in tipo or "réu" in tipo) and not proc.get("polo_passivo"):
                        proc["polo_passivo"] = [nome]
                    elif "advogado" in tipo and not proc.get("advogados"):
                        proc.setdefault("advogados", []).append(nome)
                    elif ("juiz" in tipo or "magistrad" in tipo or "relator" in tipo or "prolator" in tipo) and not proc.get("magistrado"):
                        proc["magistrado"] = nome

                # Magistrado do Escavador (campo direto)
                if not proc.get("magistrado"):
                    mag_esc = item.get("magistrado") or item.get("juiz") or item.get("relator") or ""
                    if isinstance(mag_esc, dict):
                        mag_esc = mag_esc.get("nome") or ""
                    if mag_esc and len(str(mag_esc)) > 3:
                        proc["magistrado"] = str(mag_esc)

                if proc.get("polo_ativo") or proc.get("polo_passivo") or proc.get("magistrado"):
                    proc["escavador_enriched"] = True
                    log.info(f"[Escavador] Processo {numero}: dados encontrados via Escavador (partes={bool(proc.get('polo_ativo'))}, juiz={bool(proc.get('magistrado'))})")
                break

    except Exception as e:
        log.warning(f"[Escavador] enrich falhou para {numero}: {e}")

    return proc


def fire_tribunal_async(numero: str) -> Optional[int]:
    """Dispara busca no tribunal e retorna async_id (sem esperar)."""
    if not ESCAVADOR_OK or not ESC:
        return None
    try:
        log.info(f"[Tribunal] Disparando busca async para {numero}")
        result = ESC._post(f"processo-tribunal/{numero}/async", {})
        async_id = None
        if isinstance(result, dict):
            async_id = result.get("id") or result.get("resposta", {}).get("id")
        if async_id:
            log.info(f"[Tribunal] Async disparado ID={async_id}")
            return async_id
        else:
            log.warning(f"[Tribunal] Sem ID retornado: {str(result)[:200]}")
    except Exception as e:
        log.warning(f"[Tribunal] Erro ao disparar async: {e}")
    return None


def check_tribunal_result(proc: dict, async_id: int) -> dict:
    """Verifica se resultado do tribunal async está pronto e enriquece proc."""
    if not async_id or not ESCAVADOR_OK or not ESC:
        return proc
    if proc.get("magistrado"):
        return proc
    try:
        check = ESC._get(f"async/resultados/{async_id}")
        log.info(f"[Tribunal] Async {async_id} resposta: {str(check)[:500]}")

        status = ""
        if isinstance(check, dict):
            status = (check.get("status") or check.get("resposta", {}).get("status") or "").lower()

        if status in ("sucesso", "finalizado", "completed"):
            dados = check.get("resposta", {}).get("resposta") or check.get("resposta") or check.get("resultado") or check
            extraido = ESC.ESCAVADOR.extrair_dados_tribunal(dados)
            log.info(f"[Tribunal] Extraído: mag={extraido.get('magistrado')}, pa={extraido.get('polo_ativo')}, pp={extraido.get('polo_passivo')}")

            if extraido.get("magistrado"):
                proc["magistrado"] = extraido["magistrado"]
                proc["tribunal_enriched"] = True
                log.info(f"[Tribunal] ✅ Magistrado encontrado: {extraido['magistrado']}")
            if extraido.get("polo_ativo") and not proc.get("polo_ativo"):
                proc["polo_ativo"] = extraido["polo_ativo"]
            if extraido.get("polo_passivo") and not proc.get("polo_passivo"):
                proc["polo_passivo"] = extraido["polo_passivo"]
            if extraido.get("advogados") and not proc.get("advogados"):
                proc["advogados"] = extraido["advogados"]
        else:
            log.info(f"[Tribunal] Async {async_id} status='{status}' (ainda não pronto)")

    except Exception as e:
        log.warning(f"[Tribunal] Erro ao verificar async {async_id}: {e}")
    return proc


def enrich_with_tribunal(proc: dict, numero: str) -> dict:
    """
    Busca no tribunal via Escavador async (fire-and-check com cache).
    Usado para /relatorio e PDF onde podemos esperar mais.
    """
    if not ESCAVADOR_OK or not ESC:
        return proc
    if proc.get("magistrado"):
        return proc

    numero_limpo = re.sub(r'\D', '', numero)
    cache_key = f"tribunal_async_{numero_limpo}"
    async_id = _tribunal_cache.get(cache_key)

    if async_id:
        proc = check_tribunal_result(proc, async_id)
        _tribunal_cache.pop(cache_key, None)
    else:
        new_id = fire_tribunal_async(numero)
        if new_id:
            _tribunal_cache[cache_key] = new_id

    return proc

# Cache global para IDs de busca assíncrona
_tribunal_cache = {}

# ═══════════════════════════════════════════════════════
# DATAJUD
# ═══════════════════════════════════════════════════════
COD_SENT={11,193,198,199,17,14,15,16}
COD_INT={85,26,51,60,61,87,1038}
COD_AC={941,237,196,7,8,9}
ASS_HE={1723,14548,14549,14550,14551}
ASS_PERI={1856,14552,14553}
ASS_INS={1855,14554}

def classif_mov(mov:dict)->str:
    cod=mov.get("codigoNacional")
    if not cod and isinstance(mov.get("movimentoNacional"),dict): cod=mov["movimentoNacional"].get("codigo")
    if cod:
        if cod in COD_SENT: return "sentenca"
        if cod in COD_INT:  return "decisao_interlocutoria"
        if cod in COD_AC:   return "acordao"
    n=(mov.get("nome") or "").lower()
    if any(w in n for w in ["sentença","procedente","improcedente","julgament"]): return "sentenca"
    if any(w in n for w in ["acórdão","recurso provido","recurso improvido"]):   return "acordao"
    if any(w in n for w in ["decisão","despacho","liminar","tutela"]):           return "decisao_interlocutoria"
    return "outro"

def extract_mag_datajud(movs:list)->Optional[str]:
    """
    Extrai nome do magistrado das movimentações DataJud.
    Busca em: magistradoProlator, responsavelMovimento, complementos e texto das movimentações.
    """
    # 1. Campos estruturados (mais confiável)
    for m in movs:
        mag=m.get("magistradoProlator") or m.get("responsavelMovimento")
        if mag:
            nome=(mag.get("nome") or mag.get("nomeServidor")) if isinstance(mag,dict) else str(mag)
            if nome and len(str(nome))>3: return str(nome)

    # 2. Complementos tabelados
    for m in movs:
        for comp in (m.get("complementosTabelados") or []):
            if isinstance(comp, dict):
                desc = comp.get("descricao") or comp.get("nome") or ""
                # "conclusos a NOME" ou "juntado por NOME"
                match = re.search(r'(?:conclus[oa]s?\s+(?:os\s+autos\s+)?(?:para\s+\w+\s+)?(?:a|ao)\s+|juntado\s+por\s+|prolator[a]?\s*[:\s]+|magistrad[oa]\s*[:\s]+)([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][A-ZÁÀÂÃÉÊÍÓÔÕÚÇ\s]+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ])', desc, re.IGNORECASE)
                if match:
                    nome = match.group(1).strip()
                    if len(nome) > 5 and ' ' in nome:
                        return nome.title()

    # 3. Texto das movimentações (nome da movimentação)
    for m in movs:
        nome_mov = m.get("nome") or ""
        # Padrões: "Conclusos os autos para X a NOME", "Juntado por NOME"
        patterns = [
            r'conclus[oa]s?\s+(?:os\s+autos\s+)?(?:para\s+\w+[\s\w]*?)\s+(?:a|ao)\s+([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][A-ZÁÀÂÃÉÊÍÓÔÕÚÇ\s]+)',
            r'juntado\s+por\s+([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][A-ZÁÀÂÃÉÊÍÓÔÕÚÇ\s]+)',
            r'intimação\s+a[o]?\s+(?:juiz|juíza|magistrad[oa])\s+([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][A-ZÁÀÂÃÉÊÍÓÔÕÚÇ\s]+)',
        ]
        for pat in patterns:
            match = re.search(pat, nome_mov, re.IGNORECASE)
            if match:
                nome = match.group(1).strip()
                # Filtrar falsos positivos
                if (len(nome) > 5 and ' ' in nome and
                    not any(kw in nome.lower() for kw in ['recurso','processo','parte','autor','réu','reclamante','reclamado','documento','sentença'])):
                    return nome.title()

    # 4. Complementos de movimentações como strings
    for m in movs:
        for comp in (m.get("complementos") or []):
            texto = comp if isinstance(comp, str) else (comp.get("descricao") or comp.get("valor") or "") if isinstance(comp, dict) else ""
            if texto:
                match = re.search(r'conclus[oa]s?\s+.*?\s+(?:a|ao)\s+([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][A-ZÁÀÂÃÉÊÍÓÔÕÚÇ\s]+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ])', texto, re.IGNORECASE)
                if match:
                    nome = match.group(1).strip()
                    if len(nome) > 5 and ' ' in nome:
                        return nome.title()

    return None

class DataJudError(Exception): pass

class DataJudService:
    def _h(self):
        h={"Content-Type":"application/json"}
        if DATAJUD_API_KEY: h["Authorization"]=f"APIKey {DATAJUD_API_KEY}"
        return h

    def _post(self,alias:str,payload:dict)->dict:
        if not DATAJUD_ENABLED: raise DataJudError("DataJud desabilitado.")
        if not DATAJUD_API_KEY: raise DataJudError("DATAJUD_API_KEY não configurado.")
        try:
            r=requests.post(f"{DATAJUD_BASE_URL}/{alias}/_search",headers=self._h(),json=payload,timeout=DATAJUD_TIMEOUT_S)
            r.raise_for_status(); return r.json()
        except requests.HTTPError as e:
            raise DataJudError(f"HTTP {getattr(e.response,'status_code','?')}: {(e.response.text if e.response else '')[:300]}")
        except requests.RequestException as e:
            raise DataJudError(f"Conexão DataJud: {e}")

    def search(self,alias:str,query:dict,size:int=10,sort:list=None)->dict:
        return self._post((alias or DATAJUD_DEFAULT_ALIAS).strip(),{
            "size":min(size,50),"query":query,
            "sort":sort or [{DATAJUD_SORT_FIELD:"desc"}],
        })

    def get_process(self,numero:str,alias:str)->dict:
        return self.search(alias,{"match":{"numeroProcesso":norm_num(numero)}},size=1)

    def sources(self,raw:dict)->List[dict]:
        return [h.get("_source") or {} for h in ((raw or {}).get("hits") or {}).get("hits") or []]

    def normalize(self,src:dict)->dict:
        movs=src.get("movimentos") or []
        movs_s=sorted(movs,key=lambda x:x.get("dataHora") or "",reverse=True)
        orgao=src.get("orgaoJulgador") or {}
        classe=src.get("classe") or {}
        asts=src.get("assuntos") or []
        partes_raw=src.get("partes") or []
        pa,pp,advs=[],[],[]
        for p in partes_raw:
            nome=p.get("nome") or p.get("nomeRepresentante") or ""
            tipo=(p.get("tipoParte") or p.get("polo") or "").lower()
            if "at" in tipo or "autor" in tipo or "reclamante" in tipo: pa.append(nome)
            elif "pa" in tipo or "réu" in tipo or "reclamado" in tipo: pp.append(nome)
            for adv in (p.get("advogados") or []):
                n=adv.get("nome") or ""
                if n: advs.append(n)
        return {
            "numero_processo":  src.get("numeroProcesso"),
            "tribunal":         src.get("tribunal"),
            "grau":             src.get("grau"),
            "data_ajuizamento": src.get("dataAjuizamento"),
            "ultima_atualizacao": src.get("dataHoraUltimaAtualizacao"),
            "valor_causa":      src.get("valorCausa"),
            "classe_nome":      classe.get("nome"),
            "orgao_julgador":   orgao.get("nome"),
            "orgao_codigo":     orgao.get("codigo"),
            "assuntos":         [a.get("nome") for a in asts if a.get("nome")],
            "assuntos_codigos": [a.get("codigo") for a in asts if a.get("codigo")],
            "movimentos_total": len(movs),
            "magistrado":       extract_mag_datajud(movs_s),
            "polo_ativo":       pa,
            "polo_passivo":     pp,
            "advogados":        advs,
            "movimentos_todos": movs_s,
            "sentencas":        [m for m in movs_s if classif_mov(m)=="sentenca"],
            "decisoes_interlocutorias":[m for m in movs_s if classif_mov(m)=="decisao_interlocutoria"],
            "acordaos":         [m for m in movs_s if classif_mov(m)=="acordao"],
            "ultima_movimentacao_nome":(movs_s[0] if movs_s else {}).get("nome"),
            "ultima_movimentacao_data":(movs_s[0] if movs_s else {}).get("dataHora","")[:10],
        }

DJ = DataJudService()

# ═══════════════════════════════════════════════════════
# CONTEXT BUILDERS
# ═══════════════════════════════════════════════════════
def fmt_mov(mov:dict,tipo:bool=False)->str:
    data=(mov.get("dataHora") or "")[:10]
    nome=mov.get("nome") or "Andamento"
    t=f" [{classif_mov(mov).replace('_',' ').upper()}]" if tipo else ""
    mag=mov.get("magistrado") or mov.get("magistradoProlator") or ""
    ms=f" | Juiz: {mag}" if mag and isinstance(mag,str) and len(mag)>3 else ""
    return f"  • {data}{t} — {nome}{ms}"

def partes_block(proc:dict)->str:
    lines=[]
    if proc.get("polo_ativo"):   lines.append(f"Polo Ativo (Reclamante): {', '.join(proc['polo_ativo'])}")
    if proc.get("polo_passivo"): lines.append(f"Polo Passivo (Reclamado): {', '.join(proc['polo_passivo'])}")
    if proc.get("advogados"):    lines.append(f"Advogados: {', '.join(proc['advogados'][:5])}")
    if not lines:
        st=proc.get("mni_status","disabled")
        if st=="disabled": lines+=["Partes: disponíveis via MNI/PJe (habilitar nas envs)."]
        elif st=="error":  lines.append(f"Partes: erro MNI — {proc.get('mni_error','')}")
        else:              lines.append("Partes: não retornadas nesta consulta.")
    return "\n".join(lines)

def mni_movs_block(proc:dict)->str:
    movs=proc.get("movimentos_mni") or []
    if not movs: return ""
    lines=[f"\nMOVIMENTAÇÕES PJe/MNI ({len(movs)} registros):"]
    for m in movs[:30]:
        data=(m.get("dataHora") or "")[:10]; nome=m.get("nome") or "Andamento"
        mag=m.get("magistrado") or ""; ms=f" | Juiz: {mag}" if mag else ""
        lines.append(f"  • {data} — {nome}{ms}")
    return "\n".join(lines)

def build_ctx(proc:dict,intent:str,alias:str="")->str:
    trib=alias_nome(alias) if alias else (proc.get("tribunal") or "")
    mag=proc.get("magistrado") or "não disponível via DataJud/MNI"
    asts=", ".join(proc.get("assuntos") or []) or "não identificados"
    mni_s=proc.get("mni_status","disabled")
    mni_info={"ok":"✅ MNI ativo","error":f"⚠️ MNI erro: {proc.get('mni_error','')}","disabled":"ℹ️ MNI desabilitado"}.get(mni_s,"")
    header="\n".join([
        f"PROCESSO: {proc.get('numero_processo')} | {trib} | Grau: {proc.get('grau','n/d')}",
        f"Classe: {proc.get('classe_nome','n/d')} | Vara: {proc.get('orgao_julgador','n/d')}",
        f"Assuntos: {asts}",
        f"Ajuizamento: {proc.get('data_ajuizamento','n/d')} | Atualização: {proc.get('ultima_atualizacao','n/d')}",
        f"Valor da causa: {proc.get('valor_causa','n/d')}",
        f"Magistrado/Juiz: {mag}",
        partes_block(proc),
        f"DataJud: {proc.get('movimentos_total',0)} movimentações | {mni_info}",
    ])
    if intent in ("resumo","partes","magistrado"):
        movs="\n".join(["","ÚLTIMAS 15 MOVIMENTAÇÕES (DataJud):"]+[fmt_mov(m) for m in proc.get("movimentos_todos",[])[:15]])
        return header+movs+mni_movs_block(proc)
    if intent=="andamento_completo":
        body="\n".join([
            "",f"SENTENÇAS ({len(proc.get('sentencas',[]))}):",
        ]+([fmt_mov(m) for m in proc.get("sentencas",[])] or ["  (nenhuma)"])+[
            f"\nACÓRDÃOS ({len(proc.get('acordaos',[]))}):",
        ]+([fmt_mov(m) for m in proc.get("acordaos",[])] or ["  (nenhum)"])+[
            f"\nDECISÕES INTERLOCUTÓRIAS ({len(proc.get('decisoes_interlocutorias',[]))}):",
        ]+([fmt_mov(m) for m in proc.get("decisoes_interlocutorias",[])[:15]] or ["  (nenhuma)"])+[
            "\nHISTÓRICO COMPLETO:",
        ]+[fmt_mov(m,True) for m in proc.get("movimentos_todos",[])])
        return header+body+mni_movs_block(proc)
    if intent=="classificar":
        return header+"\n".join([
            "",f"SENTENÇAS ({len(proc.get('sentencas',[]))}):",
        ]+([fmt_mov(m) for m in proc.get("sentencas",[])] or ["  (nenhuma)"])+[
            f"\nACÓRDÃOS ({len(proc.get('acordaos',[]))}):",
        ]+([fmt_mov(m) for m in proc.get("acordaos",[])] or ["  (nenhum)"])+[
            f"\nDECISÕES INTERLOCUTÓRIAS ({len(proc.get('decisoes_interlocutorias',[]))}):",
        ]+([fmt_mov(m) for m in proc.get("decisoes_interlocutorias",[])[:15]] or ["  (nenhuma)"]))
    return header+"\n\nÚLTIMAS 5 MOVIMENTAÇÕES:\n"+"\n".join([fmt_mov(m) for m in proc.get("movimentos_todos",[])[:5]])

def ctx_banco(processos:List[dict],orgao:str,assunto:str)->str:
    lines=[f"\nBANCO DE DECISÕES — {orgao} | {assunto} | {len(processos)} processos",""]
    for s in processos[:8]:
        p=DJ.normalize(s)
        lines+=[f"── {p['numero_processo']} | Juiz: {p.get('magistrado','n/d')}",
                f"   Polo Ativo: {', '.join(p.get('polo_ativo',[]) or ['n/d'])}",
                f"   {', '.join(p.get('assuntos',[])[:3])} | Sentenças: {len(p.get('sentencas',[]))}",
                f"   Última: {p.get('ultima_movimentacao_nome','n/d')}",""]
    return "\n".join(lines)

def ctx_tematico(processos:List[dict],tema:str)->str:
    ts=tp=0
    lines=[f"\nANÁLISE TEMÁTICA — {tema} | {len(processos)} processos",""]
    for s in processos[:12]:
        p=DJ.normalize(s); ts+=len(p.get("sentencas",[]))
        for sent in p.get("sentencas",[]):
            n=(sent.get("nome") or "").lower()
            if "procedente" in n and "improcedente" not in n: tp+=1
        lines+=[f"── {p['numero_processo']} | {p.get('orgao_julgador','n/d')} | Juiz: {p.get('magistrado','n/d')}",
                f"   {', '.join(p.get('assuntos',[])[:3])} | Sentenças: {len(p.get('sentencas',[]))}",""]
    lines+=["── CONSOLIDADO:",f"   Sentenças: {ts} | Procedências estimadas: {tp}"]
    return "\n".join(lines)

INSTRUCAO:Dict[str,str]={
    "resumo":           "Analise o processo. Destaque: juiz, partes, fase processual, última movimentação, riscos e próximos passos. Responda em português.",
    "partes":           "Identifique as partes (polo ativo, polo passivo, advogados). Comente posição e implicações estratégicas. Responda em português.",
    "magistrado":       "Identifique o magistrado. Analise padrão decisório inferível. Responda em português.",
    "andamento_completo":"Apresente andamento cronológico completo. Organize por categoria. Responda em português.",
    "banco_decisoes":   "Analise processos similares. Identifique padrão decisório e taxa de procedência. Responda em português.",
    "tematico":         "Análise temática: taxa de procedência, padrão dos magistrados, tendência do tribunal. Responda em português.",
    "classificar":      "Classifique cada tipo de decisão. Explique impacto jurídico e expectativas. Responda em português.",
}

# ═══════════════════════════════════════════════════════
# OPENAI
# ═══════════════════════════════════════════════════════
MAX_TOKENS_PER_LEVEL = {
    "simples":    400,
    "doc_resumo": 1200,
    "doc_medio":  1800,
    "juridico":   1500,
    "os61":       4000,
    "direto":     300,
    "erro":       200,
}

def call_openai(messages:List[dict], temperature:float=0.15, max_tokens:int=None) -> str:
    if not OPENAI_API_KEY: raise RuntimeError("OPENAI_API_KEY não configurado.")
    body = {"model":OPENAI_MODEL, "messages":messages, "temperature":temperature}
    if max_tokens:
        # Modelos novos (gpt-4o, gpt-5+) usam max_completion_tokens
        body["max_completion_tokens"] = max_tokens
    r=requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization":f"Bearer {OPENAI_API_KEY}","Content-Type":"application/json"},
        json=body, timeout=90,
    )
    try: r.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"Erro OpenAI: {(e.response.text if e.response else '')[:400]}")
    data = r.json()
    # Log actual token usage from OpenAI
    usage = data.get("usage", {})
    if usage:
        log.info(f"[OpenAI] tokens: input={usage.get('prompt_tokens',0)} output={usage.get('completion_tokens',0)} total={usage.get('total_tokens',0)}")
    return (data.get("choices") or [{}])[0].get("message",{}).get("content","").strip() or "Sem resposta."

# ═══════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════
app = FastAPI(title="Jurimetrix OS Chat", version="8.0.0")
app.add_middleware(CORSMiddleware,
    allow_origins=["*"] if ALLOWED_ORIGIN=="*" else [ALLOWED_ORIGIN, "https://jurimetrix.com", "http://localhost", "https://sm-os-demo-backend.onrender.com", "https://chat.jurimetrix.com", "https://painel.jurimetrix.com", "https://admin.jurimetrix.com"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Serve static files
import os as _os
from fastapi.staticfiles import StaticFiles
_static_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "static")
if _os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")
    log.info(f"✅ Static files served from {_static_dir}")

# Reports directory for auto-generated PDFs
_reports_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "reports")
_os.makedirs(_reports_dir, exist_ok=True)
app.mount("/reports", StaticFiles(directory=_reports_dir), name="reports")
log.info(f"✅ Reports directory: {_reports_dir}")

# ── BACEN ────────────────────────────────────────────────────────────
try:
    import bacen_client as BACEN_MOD
    BACEN_OK = BACEN_MOD.is_configured()
    log.info("✅ BACEN SGS configurado (API pública).")
except Exception as _bacen_e:
    BACEN_MOD = None
    BACEN_OK = False
    log.warning(f"⚠️  bacen_client não carregado: {_bacen_e}")

# ── NL API (Google Natural Language) ─────────────────────────────────
try:
    import nl_client as NL_MOD
    NL_OK = NL_MOD.is_configured()
    log.info(f"{'✅' if NL_OK else '⚠️ '} Google NL API {'configurado' if NL_OK else 'não configurado'}")
except Exception as _nl_e:
    NL_MOD = None
    NL_OK = False
    log.warning(f"⚠️  nl_client não carregado: {_nl_e}")

# ── OCR ──────────────────────────────────────────────────────────────
try:
    import ocr_client as OCR_MOD
    OCR_OK = OCR_MOD.is_configured()
    log.info(f"{'✅' if OCR_OK else '⚠️ '} Google Vision OCR {'configurado' if OCR_OK else 'não configurado'}")
except Exception as _ocr_e:
    OCR_MOD = None
    OCR_OK = False
    log.warning(f"⚠️  ocr_client não carregado: {_ocr_e}")

# ── Escavador ────────────────────────────────────────────────────────
try:
    import escavador_client as ESC
    ESCAVADOR_OK = ESC.is_configured()
    log.info(f"{'✅' if ESCAVADOR_OK else '⚠️ '} Escavador {'configurado' if ESCAVADOR_OK else 'não configurado (ESCAVADOR_API_KEY faltando)'}")
except Exception as _esc_e:
    ESC = None
    ESCAVADOR_OK = False
    log.warning(f"⚠️  escavador_client não carregado: {_esc_e}")

# ── PJe Consulta Pública (scraping) ─────────────────────────────────
try:
    import pje_scraper as PJE
    PJE_OK = PJE.is_configured()
    log.info("✅ PJe Consulta Pública configurado (scraping)")
except Exception as _pje_e:
    PJE = None
    PJE_OK = False
    log.warning(f"⚠️  pje_scraper não carregado: {_pje_e}")

# ── Auth + planos + pagamentos ───────────────────────────────────────
from database import criar_tabelas
from rotas_auth_planos import registrar_rotas
criar_tabelas()
registrar_rotas(app)
log.info("✅ Auth + planos + pagamentos carregados.")

# ── Mercado Pago ─────────────────────────────────────────────────────
try:
    import mp_client as MP
    MP_OK = MP.is_configured()
    if MP_OK:
        log.info("✅ Mercado Pago configurado.")
    else:
        log.warning("⚠️  Mercado Pago: MP_ACCESS_TOKEN não configurado.")
except Exception as _mp_e:
    MP = None
    MP_OK = False
    log.warning(f"⚠️  mp_client não carregado: {_mp_e}")

class MPPreferenciaIn(BaseModel):
    plano: str
    email: str
    periodo: str = "mensal"

class MPWebhookIn(BaseModel):
    type: Optional[str] = None
    action: Optional[str] = None
    data: Optional[Dict[str,Any]] = None

@app.post("/mp/preferencia")
async def mp_criar_preferencia(body: MPPreferenciaIn, authorization: Optional[str] = Header(default=None)):
    if not MP_OK:
        raise HTTPException(503, "Mercado Pago não configurado")
    user_id = get_user_from_request(authorization) if authorization else None
    uid = user_id or "anon"
    try:
        result = MP.criar_preferencia(
            plano_slug=body.plano,
            user_id=uid,
            user_email=body.email,
            periodo=body.periodo,
        )
        return {"ok": True, **result}
    except Exception as e:
        log.error(f"[MP] Erro criar preferência: {e}")
        raise HTTPException(500, str(e))

@app.get("/mp/public-key")
def mp_public_key():
    return {"public_key": MP_PUBLIC_KEY or ""}

@app.post("/mp/webhook")
async def mp_webhook(body: dict):
    log.info(f"[MP Webhook] {body}")
    try:
        tipo = body.get("type") or body.get("topic", "")
        data = body.get("data", {})
        payment_id = data.get("id") if isinstance(data, dict) else body.get("id")

        if tipo == "payment" and payment_id and MP_OK:
            pagamento = MP.verificar_pagamento(str(payment_id))
            status = pagamento.get("status")
            ext_ref = pagamento.get("external_reference", "")
            
            log.info(f"[MP Webhook] payment_id={payment_id} status={status} ext_ref={ext_ref}")
            
            if status == "approved" and ext_ref and "|" in ext_ref and SUPABASE_OK:
                parts = ext_ref.split("|")
                user_id = parts[0]
                plano_slug = parts[1] if len(parts) > 1 else "free"
                plano_info = MP.PLANOS.get(plano_slug, {})
                tokens = plano_info.get("tokens")
                
                SB.atualizar_plano_usuario(
                    user_id=user_id,
                    plano_slug=plano_slug,
                    tokens_mes=tokens,
                    payment_id=str(payment_id),
                )
                log.info(f"[MP Webhook] ✅ Plano {plano_slug} ativado para user {user_id}")
        
        return {"ok": True}
    except Exception as e:
        log.error(f"[MP Webhook] Erro: {e}")
        return {"ok": False, "error": str(e)}

# ── Sessions (in-memory) ─────────────────────────────────────────────
SESSIONS: Dict[str,Dict[str,Any]] = {}

# ── Models ───────────────────────────────────────────────────────────
class SessionOut(BaseModel):
    session_id: str
    state: Dict[str,Any] = {}

class ChatIn(BaseModel):
    session_id: str
    message: str
    state: Optional[Dict[str,Any]] = None
    conversa_id: Optional[str] = None

class ChatOut(BaseModel):
    message: str
    state: Dict[str,Any] = {}
    prompt_level: Optional[str] = None
    conversa_id: Optional[str] = None

class RelatorioIn(BaseModel):
    session_id: str
    numero_processo: Optional[str] = None
    alias: Optional[str] = None

# ── Helpers ──────────────────────────────────────────────────────────
def auth401(k:Optional[str]):
    if DEMO_KEY and k != DEMO_KEY:
        raise HTTPException(status_code=401, detail="Não autorizado")

def sess(sid:str)->dict:
    return SESSIONS.setdefault(sid,{
        "messages":[],"uploaded_contexts":[],
        "last_process":None,"last_process_numero":None,"last_alias":None,
    })

def compact(t:str,lim:int=6000)->str: return (t or "").strip()[:lim]

def extract_text(file:UploadFile,data:bytes)->str:
    fn=(file.filename or "").lower()
    if OCR_OK and OCR_MOD:
        try:
            text = OCR_MOD.extract_text_smart(data, fn)
            if text and len(text) > 50:
                log.info(f"[OCR] {fn}: {len(text)} chars extraídos")
                return text
        except Exception as _ocr_e:
            log.warning(f"[OCR] falhou para {fn}: {_ocr_e}")
    if fn.endswith((".txt",".md")): return data.decode("utf-8",errors="ignore")
    if fn.endswith(".pdf") and PdfReader:
        try: return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages[:40]).strip()
        except: return ""
    if fn.endswith(".docx") and DocxDocument:
        try: return "\n".join(p.text for p in DocxDocument(io.BytesIO(data)).paragraphs if p.text).strip()
        except: return ""
    return ""

# ═══════════════════════════════════════════════════════
# SUPABASE ROUTES — Conversas & Mensagens
# ═══════════════════════════════════════════════════════

class ConversaIn(BaseModel):
    titulo: str = "Nova conversa"
    session_id: Optional[str] = None
    tribunal: Optional[str] = None
    processo_numero: Optional[str] = None

class MensagemIn(BaseModel):
    conversa_id: str
    role: str
    conteudo: str
    tokens_input: int = 0
    tokens_output: int = 0
    prompt_level: Optional[str] = None

@app.get("/conversas")
def listar_conversas(
    authorization: Optional[str] = Header(default=None),
    limit: int = 50,
):
    user_id = get_user_from_request(authorization)
    if not user_id or not SUPABASE_OK:
        return {"ok": False, "conversas": [], "error": "Não autenticado ou Supabase não configurado"}
    try:
        convs = SB.listar_conversas(user_id, limit=limit)
        return {"ok": True, "conversas": convs}
    except Exception as e:
        return {"ok": False, "conversas": [], "error": str(e)}

@app.post("/conversas")
def criar_conversa(
    payload: ConversaIn,
    authorization: Optional[str] = Header(default=None),
):
    user_id = get_user_from_request(authorization)
    if not user_id or not SUPABASE_OK:
        raise HTTPException(401, "Não autenticado")
    try:
        conv = SB.criar_conversa(
            user_id=user_id,
            titulo=payload.titulo,
            session_id=payload.session_id,
            tribunal=payload.tribunal,
            numero_processo=payload.processo_numero,
        )
        return {"ok": True, "conversa": conv}
    except Exception as e:
        log.error(f"[/conversas POST] erro: {e}")
        raise HTTPException(500, str(e))

@app.get("/conversas/{conversa_id}/mensagens")
def get_mensagens(
    conversa_id: str,
    authorization: Optional[str] = Header(default=None),
):
    user_id = get_user_from_request(authorization)
    if not user_id or not SUPABASE_OK:
        raise HTTPException(401, "Não autenticado")
    try:
        msgs = SB.listar_mensagens(conversa_id)
        return {"ok": True, "mensagens": msgs}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/conversas/mensagem")
def salvar_mensagem(
    payload: MensagemIn,
    authorization: Optional[str] = Header(default=None),
):
    user_id = get_user_from_request(authorization)
    if not user_id or not SUPABASE_OK:
        raise HTTPException(401, "Não autenticado")
    try:
        tokens_total = (payload.tokens_input or 0) + (payload.tokens_output or 0)
        msg = SB.salvar_mensagem(
            conversa_id=payload.conversa_id,
            role=payload.role,
            conteudo=payload.conteudo,
            prompt_level=payload.prompt_level,
            tokens_usados=tokens_total,
        )
        return {"ok": True, "mensagem": msg}
    except Exception as e:
        log.error(f"[/conversas/mensagem] erro: {e} | conversa_id={payload.conversa_id} | role={payload.role}")
        raise HTTPException(500, str(e))

@app.delete("/conversas/{conversa_id}")
def deletar_conversa(
    conversa_id: str,
    authorization: Optional[str] = Header(default=None),
):
    user_id = get_user_from_request(authorization)
    if not user_id or not SUPABASE_OK:
        raise HTTPException(401, "Não autenticado")
    try:
        SB.deletar_conversa(conversa_id, user_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/me")
def get_me(authorization: Optional[str] = Header(default=None)):
    if not SUPABASE_OK:
        raise HTTPException(503, "Supabase não configurado.")
    user_id = get_user_from_request(authorization)
    if not user_id:
        log.warning(f"[/me] Token inválido — auth header: {str(authorization)[:40] if authorization else 'None'}")
        raise HTTPException(401, "Token inválido ou expirado.")
    try:
        perfil     = SB.DB.get_perfil(user_id) or {}
        assinatura = SB.DB.get_assinatura(user_id) or {}
        return {"ok": True, "perfil": perfil, "assinatura": assinatura}
    except Exception as e:
        log.error(f"[/me] erro: {e}")
        raise HTTPException(500, str(e))

# ═══════════════════════════════════════════════════════
# JURIMETRIA — Análise Estatística
# ═══════════════════════════════════════════════════════
from nl_client import classificar_resultado_decisao

KW_RESULTADO_MOV = {
    "procedente": "procedente",
    "procedência": "procedente",
    "condeno": "procedente",
    "condenar": "procedente",
    "julgo procedente": "procedente",
    "improcedente": "improcedente",
    "improcedência": "improcedente",
    "julgo improcedente": "improcedente",
    "parcialmente procedente": "parcial",
    "parcial procedência": "parcial",
    "acordo": "acordo",
    "homologação": "acordo",
    "homologo": "acordo",
    "conciliação": "acordo",
    "arquivamento": "arquivado",
    "arquivado": "arquivado",
    "extinto": "extinto",
    "extinção": "extinto",
}

def _classificar_processo(proc: dict) -> str:
    """Classifica resultado do processo pelas movimentações."""
    movs = proc.get("movimentos_todos") or []
    for m in movs:
        nome = (m.get("nome") or "").lower()
        # Checar keywords
        for kw, resultado in KW_RESULTADO_MOV.items():
            if kw in nome:
                return resultado
        # Checar complementos
        for comp in (m.get("complementosTabelados") or []):
            desc = (comp.get("descricao") or comp.get("nome") or "").lower()
            for kw, resultado in KW_RESULTADO_MOV.items():
                if kw in desc:
                    return resultado
    return "indeterminado"

def _parse_datajud_date(raw) -> Optional[str]:
    """Parseia datas DataJud em múltiplos formatos → YYYY-MM-DD."""
    if not raw:
        return None
    s = str(raw).strip()
    # ISO: 2019-10-21T10:00:00
    if len(s) >= 10 and s[4] == '-':
        return s[:10]
    # Compact: 20191021 ou 20191021100000
    digits = re.sub(r'\D', '', s)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return None

def _format_cnj(numero) -> str:
    """Formata número de processo no padrão CNJ: NNNNNNN-DD.AAAA.J.TR.OOOO."""
    if not numero:
        return ""
    digits = re.sub(r'\D', '', str(numero))
    if len(digits) == 20:
        return f"{digits[:7]}-{digits[7:9]}.{digits[9:13]}.{digits[13]}.{digits[14:16]}.{digits[16:]}"
    return str(numero)

def _format_date_br(iso_date) -> str:
    """Converte YYYY-MM-DD → DD/MM/YYYY."""
    if not iso_date or len(str(iso_date)) < 10:
        return ""
    s = str(iso_date)[:10]
    parts = s.split("-")
    if len(parts) == 3:
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return s

def _calcular_duracao_dias(proc: dict) -> Optional[int]:
    """Calcula duração do processo em dias (ajuizamento até sentença ou última mov)."""
    from datetime import datetime as _dt
    data_inicio = _parse_datajud_date(proc.get("data_ajuizamento"))
    if not data_inicio:
        return None
    try:
        inicio = _dt.fromisoformat(data_inicio)
    except:
        return None
    # Procura data da sentença
    for s in (proc.get("sentencas") or []):
        ds = _parse_datajud_date(s.get("dataHora"))
        if ds:
            try:
                dias = (_dt.fromisoformat(ds) - inicio).days
                if dias > 0:
                    return dias
            except:
                pass
    # Fallback: última movimentação
    ult_raw = proc.get("ultima_movimentacao_data") or ""
    ult = _parse_datajud_date(ult_raw)
    if ult:
        try:
            dias = (_dt.fromisoformat(ult) - inicio).days
            if dias > 0:
                return dias
        except:
            pass
    return None

@app.get("/api/jurimetria")
def api_jurimetria(
    assunto: str = "Horas Extras",
    tribunal: str = DATAJUD_DEFAULT_ALIAS,
    vara: str = "",
    limit: int = 50,
):
    """Endpoint de jurimetria — análise estatística de processos similares."""
    if not DATAJUD_ENABLED:
        raise HTTPException(503, "DataJud não habilitado")

    # Construir query
    must = []
    # Assunto — busca por nome (mais confiável que código)
    ass_names = {
        "horas extras": "Horas Extras",
        "periculosidade": "Adicional de Periculosidade",
        "insalubridade": "Adicional de Insalubridade",
    }
    ass_lower = assunto.lower()
    nome_assunto = ass_names.get(ass_lower, assunto)
    must.append({"match_phrase": {"assuntos.nome": nome_assunto}})

    # Vara
    if vara:
        must.append({"match_phrase": {"orgaoJulgador.nome": vara}})

    try:
        size = min(limit, 50)
        query = {"bool": {"must": must}}
        log.info(f"[Jurimetria] Query: {json.dumps(query)} | tribunal={tribunal} | size={size}")
        r = DJ.search(tribunal, query, size=size)
        items = DJ.sources(r)
        log.info(f"[Jurimetria] Resultados: {len(items)} processos encontrados")
    except Exception as e:
        raise HTTPException(500, f"Erro DataJud: {e}")

    # Analisar cada processo
    resultados = {"procedente": 0, "improcedente": 0, "parcial": 0, "acordo": 0, "arquivado": 0, "extinto": 0, "indeterminado": 0}
    duracoes = []
    valores = []
    por_vara = {}
    por_ano = {}
    processos_analisados = []

    for item in items:
        proc = DJ.normalize(item)
        resultado = _classificar_processo(proc)
        resultados[resultado] = resultados.get(resultado, 0) + 1

        duracao = _calcular_duracao_dias(proc)
        if duracao and duracao > 0:
            duracoes.append(duracao)

        vc = proc.get("valor_causa")
        # DataJud pode retornar como dict, string ou número
        if isinstance(vc, dict):
            vc = vc.get("valor") or vc.get("amount")
        if isinstance(vc, str):
            try:
                vc = float(re.sub(r'[^\d.,]', '', vc).replace(',', '.'))
            except:
                vc = None
        if vc and isinstance(vc, (int, float)) and vc > 0:
            valores.append(float(vc))

        # Por vara
        vara_nome = proc.get("orgao_julgador") or "Não identificada"
        if vara_nome not in por_vara:
            por_vara[vara_nome] = {"total": 0, "procedente": 0, "improcedente": 0, "parcial": 0, "acordo": 0}
        por_vara[vara_nome]["total"] += 1
        if resultado in por_vara[vara_nome]:
            por_vara[vara_nome][resultado] += 1

        # Por ano
        ano_date = _parse_datajud_date(proc.get("data_ajuizamento"))
        ano = ano_date[:4] if ano_date else ""
        if ano and ano.isdigit():
            if ano not in por_ano:
                por_ano[ano] = {"total": 0, "procedente": 0, "improcedente": 0, "parcial": 0, "acordo": 0}
            por_ano[ano]["total"] += 1
            if resultado in por_ano[ano]:
                por_ano[ano][resultado] += 1

        processos_analisados.append({
            "numero": _format_cnj(proc.get("numero_processo")),
            "resultado": resultado,
            "duracao_dias": duracao,
            "valor_causa": vc,
            "vara": vara_nome,
            "data_ajuizamento": _format_date_br(_parse_datajud_date(proc.get("data_ajuizamento"))),
            "assuntos": proc.get("assuntos", []),
        })

    total = len(items)
    favoraveis = resultados["procedente"] + resultados["parcial"] + resultados["acordo"]
    taxa_exito = round(favoraveis / total * 100, 1) if total > 0 else 0

    # Top 10 varas por volume
    varas_sorted = sorted(por_vara.items(), key=lambda x: x[1]["total"], reverse=True)[:10]
    varas_stats = []
    for v_nome, v_data in varas_sorted:
        v_total = v_data["total"]
        v_fav = v_data["procedente"] + v_data["parcial"] + v_data["acordo"]
        varas_stats.append({
            "vara": v_nome,
            "total": v_total,
            "taxa_exito": round(v_fav / v_total * 100, 1) if v_total > 0 else 0,
            **v_data,
        })

    return {
        "assunto": assunto,
        "tribunal": tribunal,
        "total_processos": total,
        "resultados": resultados,
        "taxa_exito_geral": taxa_exito,
        "duracao": {
            "media_dias": round(sum(duracoes) / len(duracoes)) if duracoes else None,
            "minima_dias": min(duracoes) if duracoes else None,
            "maxima_dias": max(duracoes) if duracoes else None,
            "mediana_dias": sorted(duracoes)[len(duracoes)//2] if duracoes else None,
            "total_com_duracao": len(duracoes),
        },
        "valores_causa": {
            "medio": round(sum(valores) / len(valores), 2) if valores else None,
            "minimo": min(valores) if valores else None,
            "maximo": max(valores) if valores else None,
            "total_com_valor": len(valores),
        },
        "por_vara": varas_stats,
        "por_ano": dict(sorted(por_ano.items())),
        "processos": processos_analisados[:20],
    }


@app.get("/api/jurimetria/processo/{numero}")
def api_jurimetria_processo(numero: str):
    """
    Jurimetria personalizada: busca processo, identifica vara/juiz/assunto,
    e analisa casos similares para calcular probabilidades.
    """
    if not DATAJUD_ENABLED:
        raise HTTPException(503, "DataJud não habilitado")

    # 1. Buscar o processo
    alias = infer_alias(numero) or DATAJUD_DEFAULT_ALIAS
    try:
        raw = DJ.get_process(numero, alias)
        items = DJ.sources(raw)
        if not items:
            raise HTTPException(404, f"Processo {numero} não encontrado")
        proc = DJ.normalize(items[0])
        proc = enrich_with_escavador(proc, numero)
    except DataJudError as e:
        raise HTTPException(502, f"Erro DataJud: {e}")

    orgao = proc.get("orgao_julgador") or ""
    assuntos = proc.get("assuntos") or []
    assunto_principal = assuntos[0] if assuntos else ""
    assuntos_codigos = proc.get("assuntos_codigos") or []

    # 2. Buscar processos similares (mesma vara + mesmo assunto)
    similares_items = []

    # 2a. Por vara + assunto (mais específico)
    if orgao and assuntos_codigos:
        try:
            must = [{"match": {"orgaoJulgador.nome": orgao}}]
            should_ass = [{"match": {"assuntos.codigo": c}} for c in assuntos_codigos[:3]]
            must.append({"bool": {"should": should_ass, "minimum_should_match": 1}})
            r = DJ.search(alias, {"bool": {"must": must}}, size=50)
            similares_items = [s for s in DJ.sources(r) if norm_num(s.get("numeroProcesso","")) != norm_num(numero)]
        except:
            pass

    # 2b. Se poucos resultados, busca só por vara
    if len(similares_items) < 10 and orgao:
        try:
            r2 = DJ.search(alias, {"match_phrase": {"orgaoJulgador.nome": orgao}}, size=50)
            existing_nums = {norm_num(s.get("numeroProcesso","")) for s in similares_items}
            for s in DJ.sources(r2):
                if norm_num(s.get("numeroProcesso","")) not in existing_nums and norm_num(s.get("numeroProcesso","")) != norm_num(numero):
                    similares_items.append(s)
                    existing_nums.add(norm_num(s.get("numeroProcesso","")))
        except:
            pass

    # 2c. Se ainda poucos, busca por assunto no tribunal
    if len(similares_items) < 10 and assunto_principal:
        try:
            r3 = DJ.search(alias, {"match_phrase": {"assuntos.nome": assunto_principal}}, size=30)
            existing_nums = {norm_num(s.get("numeroProcesso","")) for s in similares_items}
            for s in DJ.sources(r3):
                if norm_num(s.get("numeroProcesso","")) not in existing_nums and norm_num(s.get("numeroProcesso","")) != norm_num(numero):
                    similares_items.append(s)
                    existing_nums.add(norm_num(s.get("numeroProcesso","")))
        except:
            pass

    # 3. Analisar similares
    resultados = {"procedente": 0, "improcedente": 0, "parcial": 0, "acordo": 0, "arquivado": 0, "extinto": 0, "indeterminado": 0}
    duracoes = []
    valores = []
    por_ano = {}
    processos_analisados = []

    for item in similares_items[:50]:
        p = DJ.normalize(item)
        resultado = _classificar_processo(p)
        resultados[resultado] = resultados.get(resultado, 0) + 1

        duracao = _calcular_duracao_dias(p)
        if duracao and duracao > 0:
            duracoes.append(duracao)

        vc = p.get("valor_causa")
        if isinstance(vc, dict):
            vc = vc.get("valor")
        if isinstance(vc, str):
            try:
                vc = float(re.sub(r'[^\d.,]', '', vc).replace(',', '.'))
            except:
                vc = None
        if vc and isinstance(vc, (int, float)) and vc > 0:
            valores.append(float(vc))

        ano_date = _parse_datajud_date(p.get("data_ajuizamento"))
        ano = ano_date[:4] if ano_date else ""
        if ano and ano.isdigit():
            if ano not in por_ano:
                por_ano[ano] = {"total": 0, "procedente": 0, "improcedente": 0, "parcial": 0, "acordo": 0}
            por_ano[ano]["total"] += 1
            if resultado in por_ano[ano]:
                por_ano[ano][resultado] += 1

        processos_analisados.append({
            "numero": _format_cnj(p.get("numero_processo")),
            "resultado": resultado,
            "duracao_dias": duracao,
            "valor_causa": vc,
            "vara": p.get("orgao_julgador") or "",
            "data_ajuizamento": _format_date_br(_parse_datajud_date(p.get("data_ajuizamento"))),
            "assuntos": p.get("assuntos", []),
            "magistrado": p.get("magistrado"),
        })

    total = len(similares_items[:50])
    favoraveis = resultados["procedente"] + resultados["parcial"] + resultados["acordo"]
    taxa_exito = round(favoraveis / total * 100, 1) if total > 0 else 0

    # Probabilidade calculada
    desfavoraveis = resultados["improcedente"]
    determinados = total - resultados["indeterminado"]
    prob_favoravel = round(favoraveis / determinados * 100, 1) if determinados > 0 else None
    prob_desfavoravel = round(desfavoraveis / determinados * 100, 1) if determinados > 0 else None

    return {
        "processo": {
            "numero": _format_cnj(proc.get("numero_processo")),
            "classe": proc.get("classe_nome"),
            "vara": orgao,
            "tribunal": alias_nome(alias),
            "assuntos": assuntos,
            "magistrado": proc.get("magistrado"),
            "polo_ativo": proc.get("polo_ativo", []),
            "polo_passivo": proc.get("polo_passivo", []),
            "data_ajuizamento": _format_date_br(_parse_datajud_date(proc.get("data_ajuizamento"))),
            "valor_causa": proc.get("valor_causa"),
            "movimentos_total": proc.get("movimentos_total", 0),
            "ultima_movimentacao": proc.get("ultima_movimentacao_nome"),
            "ultima_data": _format_date_br(_parse_datajud_date(proc.get("ultima_movimentacao_data"))),
        },
        "analise": {
            "total_similares": total,
            "resultados": resultados,
            "taxa_exito_geral": taxa_exito,
            "probabilidade_favoravel": prob_favoravel,
            "probabilidade_desfavoravel": prob_desfavoravel,
            "duracao": {
                "media_dias": round(sum(duracoes) / len(duracoes)) if duracoes else None,
                "mediana_dias": sorted(duracoes)[len(duracoes)//2] if duracoes else None,
                "total_com_duracao": len(duracoes),
            },
            "valores_causa": {
                "medio": round(sum(valores) / len(valores), 2) if valores else None,
                "total_com_valor": len(valores),
            },
            "por_ano": dict(sorted(por_ano.items())),
        },
        "processos_similares": processos_analisados[:20],
    }


@app.get("/jurimetria")
def serve_jurimetria():
    from fastapi.responses import FileResponse, HTMLResponse
    import os
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "static", "jurimetria.html")
    if os.path.exists(path):
        return FileResponse(path, media_type="text/html")
    return HTMLResponse("<h1>jurimetria.html não encontrado em /static/</h1>", status_code=404)


# ═══════════════════════════════════════════════════════
# ADMIN ROUTES
# ═══════════════════════════════════════════════════════
ADMIN_EMAILS = {"eduardo.salles1991@gmail.com", "paxelbr177@gmail.com"}

def is_admin(authorization: Optional[str]) -> bool:
    if not SUPABASE_OK or not authorization: return False
    user_id = get_user_from_request(authorization)
    if not user_id: return False
    try:
        perfil = SB.DB.get_perfil(user_id) or {}
        return perfil.get("email","") in ADMIN_EMAILS or perfil.get("is_admin", False)
    except: return False

@app.get("/admin/stats")
def admin_stats(authorization: Optional[str] = Header(default=None)):
    if not is_admin(authorization):
        raise HTTPException(403, "Acesso negado")
    try:
        stats = SB.DB.get_stats() or {}
        return {"ok": True, **stats}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/admin/usuarios")
def admin_usuarios(
    limit: int = 100,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None)
):
    if not is_admin(authorization):
        raise HTTPException(403, "Acesso negado")
    try:
        usuarios = SB.DB.listar_clientes(limit=limit, offset=offset) or []
        return {"ok": True, "usuarios": usuarios, "total": len(usuarios)}
    except Exception as e:
        raise HTTPException(500, str(e))

class AtualizarPlanoIn(BaseModel):
    user_id: str
    plano_slug: str
    status: str = "ativa"
    tokens_mes: Optional[int] = None

@app.post("/admin/atualizar-plano")
def admin_atualizar_plano(
    body: AtualizarPlanoIn,
    authorization: Optional[str] = Header(default=None)
):
    if not is_admin(authorization):
        raise HTTPException(403, "Acesso negado")
    try:
        ok = SB.atualizar_plano_usuario(
            user_id=body.user_id,
            plano_slug=body.plano_slug,
            tokens_mes=body.tokens_mes,
        )
        return {"ok": ok}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/speech-to-text")
async def speech_to_text(
    audio: UploadFile = File(...),
    mime_type: str = Form(default="audio/webm"),
    x_demo_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    import base64, json, os
    
    GOOGLE_CREDS = os.getenv("GOOGLE_VISION_CREDENTIALS")
    if not GOOGLE_CREDS:
        raise HTTPException(503, "Google Cloud não configurado")
    
    try:
        audio_data = await audio.read()
        audio_b64 = base64.b64encode(audio_data).decode()
        
        if OCR_OK and OCR_MOD:
            token = OCR_MOD._get_access_token()
        else:
            import time, json as _json
            import jwt as _jwt
            creds = _json.loads(GOOGLE_CREDS)
            now = int(time.time())
            payload = {
                "iss": creds["client_email"],
                "scope": "https://www.googleapis.com/auth/cloud-platform",
                "aud": "https://oauth2.googleapis.com/token",
                "iat": now, "exp": now + 3600,
            }
            signed = _jwt.encode(payload, creds["private_key"], algorithm="RS256")
            r = requests.post("https://oauth2.googleapis.com/token", data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": signed,
            }, timeout=10)
            token = r.json()["access_token"]

        encoding_map = {
            "audio/webm": "WEBM_OPUS",
            "audio/webm;codecs=opus": "WEBM_OPUS",
            "audio/ogg": "OGG_OPUS",
            "audio/ogg;codecs=opus": "OGG_OPUS",
            "audio/mp4": "MP4",
            "audio/mpeg": "MP3",
            "audio/wav": "LINEAR16",
            "audio/flac": "FLAC",
        }
        encoding = encoding_map.get(mime_type.split(";")[0].strip(), "WEBM_OPUS")

        payload = {
            "config": {
                "encoding": encoding,
                "languageCode": "pt-BR",
                "alternativeLanguageCodes": ["pt"],
                "enableAutomaticPunctuation": True,
                "model": "latest_long",
                "useEnhanced": True,
            },
            "audio": {"content": audio_b64}
        }

        r = requests.post(
            "https://speech.googleapis.com/v1/speech:recognize",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        r.raise_for_status()
        result = r.json()

        transcript = ""
        for res in result.get("results", []):
            alts = res.get("alternatives", [])
            if alts:
                transcript += alts[0].get("transcript", "") + " "

        transcript = transcript.strip()
        log.info(f"[STT] Transcrição: {transcript[:80]}...")
        return {"ok": True, "transcript": transcript}

    except Exception as e:
        log.error(f"[STT] Erro: {e}")
        raise HTTPException(500, f"Erro na transcrição: {str(e)}")

@app.get("/ping")
def ping(): return {"ok":True,"version":"8.0.0"}

@app.get("/painel")
def serve_painel():
    from fastapi.responses import FileResponse, HTMLResponse
    import os
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "static", "painel.html")
    if os.path.exists(path):
        return FileResponse(path, media_type="text/html")
    return HTMLResponse("<h1>painel.html não encontrado em /static/</h1>", status_code=404)

@app.get("/admin")
def serve_admin():
    from fastapi.responses import FileResponse, HTMLResponse
    import os
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "static", "admin.html")
    if os.path.exists(path):
        return FileResponse(path, media_type="text/html")
    return HTMLResponse("<h1>admin.html não encontrado em /static/</h1>", status_code=404)

@app.get("/chat-app")
def serve_chat():
    from fastapi.responses import FileResponse, HTMLResponse
    import os
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "static", "chat.html")
    if os.path.exists(path):
        return FileResponse(path, media_type="text/html")
    return HTMLResponse("<h1>chat.html não encontrado em /static/</h1>", status_code=404)

@app.get("/health")
def health():
    return {
        "ok":True,"version":"8.0.0","model":OPENAI_MODEL,
        "os_61_loaded":bool(OS_6_1_PROMPT),
        "datajud":{"enabled":DATAJUD_ENABLED,"alias":DATAJUD_DEFAULT_ALIAS},
        "mni":{"enabled":MNI_ENABLED},
        "pdf":PDF_AVAILABLE,
        "tribunais":len(ALIAS_MAP),
        "escavador":{"ok":ESCAVADOR_OK,"configured":bool(os.getenv("ESCAVADOR_API_KEY"))},
        "pje_scraper":{"ok":PJE_OK},
        "bacen":{"ok":BACEN_OK},
        "ocr":{"ok":OCR_OK,"configured":bool(os.getenv("GOOGLE_VISION_CREDENTIALS"))},
        "nl":{"ok":NL_OK},
        "supabase":{
            "ok": SUPABASE_OK,
            "url": SUPABASE_URL[:30]+"..." if SUPABASE_URL else "não configurado",
            "service_key": "✅ configurado" if os.getenv("SUPABASE_SERVICE_KEY") else "❌ faltando",
            "jwt_secret":  "✅ configurado" if os.getenv("SUPABASE_JWT_SECRET") else "❌ faltando",
        },
        "mercadopago":{"ok": MP_OK, "configured": bool(os.getenv("MP_ACCESS_TOKEN"))},
    }

@app.get("/tribunais")
def tribunais():
    return {"tribunais":[{"alias":v,"nome":alias_nome(v),"chave_cnj":k} for k,v in sorted(ALIAS_MAP.items())]}

@app.post("/session/new", response_model=SessionOut)
def session_new(x_demo_key:Optional[str]=Header(default=None)):
    auth401(x_demo_key)
    sid=str(uuid.uuid4())
    SESSIONS[sid]={"messages":[],"uploaded_contexts":[],"last_process":None,"last_process_numero":None,"last_alias":None}
    return SessionOut(session_id=sid,state={})

@app.post("/upload")
async def upload(
    session_id:str=Form(...),
    file:UploadFile=File(...),
    x_demo_key:Optional[str]=Header(default=None),
):
    auth401(x_demo_key)
    s=sess(session_id); data=await file.read()
    s["uploaded_contexts"].append({"filename":file.filename,"text":compact(extract_text(file,data),12000)})
    return {"ok":True,"message":f"Arquivo '{file.filename}' anexado.","filename":file.filename}

@app.post("/relatorio")
def gerar_relatorio(
    payload:RelatorioIn,
    x_demo_key:Optional[str]=Header(default=None),
):
    from fastapi.responses import Response
    auth401(x_demo_key)
    if not PDF_AVAILABLE:
        raise HTTPException(501,"relatorio_pdf.py não encontrado no repositório.")
    s=sess(payload.session_id)
    numero=payload.numero_processo or s.get("last_process_numero")
    if not numero:
        raise HTTPException(400,"Nenhum processo identificado na sessão.")
    alias=(payload.alias or infer_alias(numero) or s.get("last_alias") or DATAJUD_DEFAULT_ALIAS)
    try:
        raw=DJ.get_process(numero,alias); items=DJ.sources(raw)
        if not items:
            raise HTTPException(404,f"Processo {numero} não encontrado no {alias_nome(alias)}.")
        proc=DJ.normalize(items[0]); proc=enrich_with_mni(proc,numero)
        proc=enrich_with_escavador(proc,numero)
        if PJE_OK and PJE: proc=PJE.enrich_processo(proc,numero,alias)
        proc=enrich_with_tribunal(proc,numero)
        context=build_ctx(proc,"resumo",alias)
        instrucao=(
            "Gere análise OS 6.1 COMPLETA e ESTRUTURADA. Use EXATAMENTE estas seções:\n"
            "SÍNTESE\nANÁLISE TÉCNICA\nQUESTÃO JURÍDICA\nFORÇA DA TESE\nCONFIABILIDADE\n"
            "SCORES (Viabilidade: XX, Risco: XX, Rentabilidade: XX, Urgência: XX, Prioridade: XX, Composto: XX)\n"
            "RISCOS (com nível: CRÍTICO/ALTO/MÉDIO/BAIXO)\n"
            "RED TEAM\nATAQUES\nPONTO MAIS VULNERÁVEL\nMEDIDAS PREVENTIVAS\n"
            "ESTRATÉGIA\nLINHA PRINCIPAL\nLINHAS SUBSIDIÁRIAS\nAÇÕES PRIORITÁRIAS\n"
            "PENDÊNCIAS\nALERTAS\nResposta em português formal."
        )
        sys_p=build_system_prompt("os61",context)
        analise_texto=call_openai([{"role":"system","content":sys_p},{"role":"user","content":instrucao}],temperature=0.1)
        analise_dict=parse_analise_gpt(analise_texto)
        pdf_bytes=build_relatorio_pdf(processo=proc,analise_os61=analise_dict,mandataria_nome=MANDATARIA_NOME,mandataria_oab=MANDATARIA_OAB)
        s["last_analise"]=analise_dict; s["last_analise_texto"]=analise_texto
        numero_safe=re.sub(r"[^\w\-]","_",numero)
        return Response(content=pdf_bytes,media_type="application/pdf",
                        headers={"Content-Disposition":f'attachment; filename="relatorio_{numero_safe}.pdf"'})
    except HTTPException: raise
    except DataJudError as e: raise HTTPException(502,f"Erro DataJud: {e}")
    except Exception as e: log.exception("Erro relatório"); raise HTTPException(500,f"Erro interno: {e}")

@app.post("/chat", response_model=ChatOut)
def chat(payload:ChatIn, x_demo_key:Optional[str]=Header(default=None), authorization:Optional[str]=Header(default=None)):
    auth401(x_demo_key)
    s=sess(payload.session_id)
    message=(payload.message or "").strip()
    state=payload.state or {}

    # ── Verificar e decrementar tokens ────────────────────────────────
    user_id = get_user_from_request(authorization) if authorization else None
    if user_id and SUPABASE_OK:
        tokens_reply = SB.verificar_e_decrementar_tokens(user_id, len(message))
        if tokens_reply and not tokens_reply.get("ok"):
            limite_msg = "Limite de tokens atingido! Faca upgrade em jurimetrix.com/pricing"
            return ChatOut(message=limite_msg, state=state, prompt_level="limite")

    s["messages"].append({"role":"user","content":message})
    s["messages"]=s["messages"][-20:]

    numbers=detect_numbers(message)
    has_proc=bool(numbers) or bool(s.get("last_process_numero"))
    plevel=classify_prompt(message,has_proc)

    # ── BACEN SGS — índices econômicos e cálculos trabalhistas ─────
    bacen_tipo = detect_bacen_intent(message) if BACEN_OK else None
    if bacen_tipo:
        try:
            bacen_dados = {}
            
            bacen_dados["indices"] = BACEN_MOD.BACEN.indices_atuais()
            
            valor_match = re.search(r'r\$\s*([\d.,]+)', message.lower())
            if valor_match:
                valor_str = valor_match.group(1).replace('.','').replace(',','.')
                valor = float(valor_str)
                
                data_match = re.search(r'(\d{2}/\d{2}/\d{4}|\d{2}/\d{4})', message)
                if data_match:
                    data_raw = data_match.group(1)
                    if len(data_raw) == 7:
                        data_inicio = f"01/{data_raw}"
                    else:
                        data_inicio = data_raw
                    bacen_dados["correcao"] = BACEN_MOD.BACEN.calcular_correcao_inpc(valor, data_inicio)
                
                meses_match = re.search(r'(\d+)\s*m[eê]s', message.lower())
                if meses_match:
                    meses = int(meses_match.group(1))
                    bacen_dados["juros"] = BACEN_MOD.BACEN.calcular_juros_mora(valor, meses)

            ctx_bacen = BACEN_MOD.BACEN.build_context({
                **bacen_dados.get("indices", {}),
                "correcao": bacen_dados.get("correcao"),
                "juros": bacen_dados.get("juros"),
            })
            
            sys_p = build_system_prompt("juridico", ctx_bacen)
            msgs_bacen = [{"role":"system","content":sys_p}]
            for item in s["messages"][-6:]:
                if item["role"] in {"user","assistant"}: msgs_bacen.append(item)
            msgs_bacen[-1] = {"role":"user","content":f"{message}\n\n[Use os dados econômicos fornecidos no contexto. Apresente os valores de forma direta e objetiva — não cite a fonte dos dados. Se algum índice não estiver disponível, informe que o dado está temporariamente indisponível. Responda em português, de forma concisa.]"}
            reply = call_openai(msgs_bacen, 0.1, max_tokens=MAX_TOKENS_PER_LEVEL["juridico"])
            s["messages"].append({"role":"assistant","content":reply})
            return ChatOut(message=reply, state=state, prompt_level="juridico")
        except Exception as _bacen_err:
            log.warning(f"BACEN erro: {_bacen_err}")

    # ── Escavador — busca por pessoa/empresa/advogado ───────────────
    esc_tipo = detect_escavador_intent(message)
    if esc_tipo and ESCAVADOR_OK:
        try:
            q = extract_escavador_query(message, esc_tipo)
            esc_result = None
            if esc_tipo == "processos":
                esc_result = ESC.ESCAVADOR.processos_por_envolvido(
                    nome=q.get("nome",""), cpf_cnpj=q.get("cpf") or q.get("cnpj")
                )
            elif esc_tipo == "pessoa":
                esc_result = ESC.ESCAVADOR.buscar_pessoa(
                    nome=q.get("nome"), cpf=q.get("cpf")
                )
            elif esc_tipo == "empresa":
                esc_result = ESC.ESCAVADOR.buscar_empresa(
                    nome=q.get("nome"), cnpj=q.get("cnpj")
                )
            elif esc_tipo == "advogado":
                esc_result = ESC.ESCAVADOR.buscar_advogado(
                    nome=q.get("nome"), oab=q.get("oab")
                )
            if esc_result:
                esc_ctx = ESC.ESCAVADOR.build_context(esc_result, esc_tipo)
                sys_p = build_system_prompt("juridico", esc_ctx)
                msgs = [{"role":"system","content":sys_p}]
                for item in s["messages"][-6:]:
                    if item["role"] in {"user","assistant"}: msgs.append(item)
                msgs[-1] = {"role":"user","content":f"{message}\n\n[Use os dados fornecidos no contexto para responder com precisão. Nunca mencione a fonte dos dados nem o nome 'Escavador'. Apresente as informações como se fossem do próprio sistema Jurimetrix. Responda em português.]"}
                reply = call_openai(msgs, 0.15, max_tokens=MAX_TOKENS_PER_LEVEL["juridico"])
                s["messages"].append({"role":"assistant","content":reply})
                return ChatOut(message=reply,state=state,prompt_level="juridico")
        except Exception as _esc_err:
            log.warning(f"Escavador erro: {_esc_err}")

    # ── PDF auto-generation — detecta pedido de relatório ──────────
    if detect_pdf_intent(message) and PDF_AVAILABLE:
        numero = numbers[0] if numbers else s.get("last_process_numero")
        if numero:
            alias_pdf = (infer_alias(numero) if numero else None) or s.get("last_alias") or DATAJUD_DEFAULT_ALIAS
            try:
                import re as _re  # safety import for PDF block
                raw=DJ.get_process(numero, alias_pdf); items=DJ.sources(raw)
                if items:
                    proc=DJ.normalize(items[0]); proc=enrich_with_mni(proc, numero)
                    proc=enrich_with_escavador(proc, numero)
                    if PJE_OK and PJE: proc=PJE.enrich_processo(proc, numero, alias_pdf)
                    proc=enrich_with_tribunal(proc, numero)
                    context=build_ctx(proc,"resumo",alias_pdf)
                    instrucao=(
                        "Gere análise OS 6.1 COMPLETA e ESTRUTURADA. Use EXATAMENTE estas seções:\n"
                        "SÍNTESE\nANÁLISE TÉCNICA\nQUESTÃO JURÍDICA\nFORÇA DA TESE\nCONFIABILIDADE\n"
                        "SCORES (Viabilidade: XX, Risco: XX, Rentabilidade: XX, Urgência: XX, Prioridade: XX, Composto: XX)\n"
                        "RISCOS (com nível: CRÍTICO/ALTO/MÉDIO/BAIXO)\n"
                        "RED TEAM\nATAQUES\nPONTO MAIS VULNERÁVEL\nMEDIDAS PREVENTIVAS\n"
                        "ESTRATÉGIA\nLINHA PRINCIPAL\nLINHAS SUBSIDIÁRIAS\nAÇÕES PRIORITÁRIAS\n"
                        "PENDÊNCIAS\nALERTAS\nResposta em português formal."
                    )
                    sys_p=build_system_prompt("os61",context)
                    analise_texto=call_openai([{"role":"system","content":sys_p},{"role":"user","content":instrucao}],temperature=0.1)
                    analise_dict=parse_analise_gpt(analise_texto)
                    pdf_bytes=build_relatorio_pdf(processo=proc,analise_os61=analise_dict,mandataria_nome=MANDATARIA_NOME,mandataria_oab=MANDATARIA_OAB)
                    # Save PDF
                    numero_safe=_re.sub(r"[^\w\-]","_",numero)
                    pdf_filename = f"relatorio_{numero_safe}_{uuid.uuid4().hex[:8]}.pdf"
                    pdf_path = os.path.join(_reports_dir, pdf_filename)
                    with open(pdf_path, "wb") as f:
                        f.write(pdf_bytes)
                    pdf_url = f"/reports/{pdf_filename}"
                    reply = f"✅ **Relatório PDF gerado com sucesso!**\n\n📄 [Clique aqui para baixar o relatório]({pdf_url})\n\nProcesso: **{numero}** | Tribunal: **{alias_nome(alias_pdf)}**"
                    s["messages"].append({"role":"assistant","content":reply})
                    if user_id and SUPABASE_OK:
                        SB.registrar_tokens_resposta(user_id, len(analise_texto))
                    return ChatOut(message=reply, state=state, prompt_level="os61", conversa_id=payload.conversa_id)
            except Exception as e:
                import traceback
                log.error(f"PDF auto-gen failed: {e}\n{traceback.format_exc()}")
                reply = f"Erro ao gerar o relatório PDF: {str(e)}"
                s["messages"].append({"role":"assistant","content":reply})
                return ChatOut(message=reply, state=state, prompt_level="erro")
        else:
            reply = "Para gerar o relatório PDF, primeiro informe o número do processo (formato CNJ)."
            s["messages"].append({"role":"assistant","content":reply})
            return ChatOut(message=reply, state=state, prompt_level="direto")

    # ── Rota processo (DataJud + MNI) ────────────────────────────────
    if DATAJUD_ENABLED and (
        numbers or (s.get("last_process_numero") and
            any(k in message.lower() for k in
                INT_FULL+INT_MAG+INT_PART+INT_BANCO+INT_TEM+INT_CL+
                ["processo","andamento","resumo","partes","magistrado"]))
    ):
        numero=numbers[0] if numbers else s.get("last_process_numero")
        intent=detect_intent(message)
        alias=(infer_alias(numero) if numero else None) or trib_from_msg(message) or s.get("last_alias") or DATAJUD_DEFAULT_ALIAS

        if numero:
            try:
                raw=DJ.get_process(numero,alias); items=DJ.sources(raw)
                if not items:
                    tn=alias_nome(alias)
                    reply=(f"Não encontrei o processo **{numero}** no **{tn}**.\n\n"
                           f"Se for de outro tribunal, informe qual. "
                           f'Exemplo: *"Processo {numero} no TRT3"*')
                    s["messages"].append({"role":"assistant","content":reply})
                    return ChatOut(message=reply,state=state,prompt_level="direto")

                proc=DJ.normalize(items[0]); proc=enrich_with_mni(proc,numero)
                proc=enrich_with_escavador(proc,numero)
                if PJE_OK and PJE: proc=PJE.enrich_processo(proc,numero,alias)

                # Fire tribunal async ANTES do GPT (não espera)
                _trib_async_id = None
                if not proc.get("magistrado"):
                    _trib_async_id = fire_tribunal_async(numero)

                s["last_process"]=proc; s["last_process_numero"]=numero; s["last_alias"]=alias

                extra=""
                if intent=="banco_decisoes":
                    orgao=proc.get("orgao_julgador") or ""; ast=(proc.get("assuntos") or [""])[0]
                    ac=(proc.get("assuntos_codigos") or [None])[0]
                    must=[{"match":{"orgaoJulgador.nome":orgao}}]
                    if ac: must.append({"match":{"assuntos.codigo":ac}})
                    try:
                        r2=DJ.search(alias,{"bool":{"must":must}},size=8)
                        s2=[f for f in DJ.sources(r2) if norm_num(f.get("numeroProcesso",""))!=norm_num(numero)][:6]
                        extra=ctx_banco(s2,orgao,ast)
                        # NL API — análise automática das sentenças
                        if NL_OK and NL_MOD and s2:
                            try:
                                decisoes_para_nl = []
                                for src in s2:
                                    p_tmp = DJ.normalize(src)
                                    for sent in (p_tmp.get("sentencas") or [])[:3]:
                                        # Combinar todos os campos de texto disponíveis
                                        partes_texto = []
                                        partes_texto.append(sent.get("nome") or "")
                                        # Complementos podem ter texto mais rico
                                        for comp in (sent.get("complementosTabelados") or []):
                                            partes_texto.append(comp.get("descricao") or comp.get("nome") or "")
                                        for comp in (sent.get("complementos") or []):
                                            if isinstance(comp, str):
                                                partes_texto.append(comp)
                                            elif isinstance(comp, dict):
                                                partes_texto.append(comp.get("descricao") or comp.get("valor") or comp.get("nome") or "")
                                        texto_completo = " | ".join(t for t in partes_texto if t and len(t) > 2)
                                        if texto_completo:
                                            decisoes_para_nl.append({
                                                "numero_processo": p_tmp.get("numero_processo",""),
                                                "texto": texto_completo,
                                            })
                                    # Também inclui última movimentação como contexto
                                    ult = p_tmp.get("ultima_movimentacao_nome") or ""
                                    if ult and len(ult) > 5:
                                        decisoes_para_nl.append({
                                            "numero_processo": p_tmp.get("numero_processo",""),
                                            "texto": ult,
                                        })
                                if decisoes_para_nl:
                                    nl_analise = NL_MOD.NL.analisar_lote(decisoes_para_nl)
                                    extra += "\n\n" + NL_MOD.NL.build_context(nl_analise)
                            except Exception as _nl_err:
                                log.warning(f"NL banco_decisoes: {_nl_err}")
                    except: pass
                elif intent=="tematico":
                    ml=message.lower()
                    codigos,tema=((ASS_PERI,"Adicional de Periculosidade") if "periculosidade" in ml else
                                  (ASS_INS,"Adicional de Insalubridade") if "insalubridade" in ml else
                                  (ASS_HE,"Horas Extras"))
                    try:
                        sh=[{"match":{"assuntos.codigo":c}} for c in codigos]
                        r2=DJ.search(alias,{"bool":{"should":sh,"minimum_should_match":1}},size=12)
                        extra=ctx_tematico(DJ.sources(r2),tema)
                    except: pass

                ctx=build_ctx(proc,intent,alias)+extra

                # Verificar tribunal async ANTES do GPT
                # O async teve tempo de processar enquanto fazíamos banco/NL/temático
                if _trib_async_id and not proc.get("magistrado"):
                    import time as _time
                    # Poll rápido: até 12 segundos esperando o resultado
                    for _poll in range(4):
                        _time.sleep(3)
                        proc = check_tribunal_result(proc, _trib_async_id)
                        if proc.get("magistrado"):
                            # Rebuild context com magistrado
                            ctx = build_ctx(proc, intent, alias) + extra
                            s["last_process"]["magistrado"] = proc["magistrado"]
                            log.info(f"[Tribunal] Magistrado injetado no contexto: {proc['magistrado']}")
                            break

                eff_lvl="os61" if plevel=="os61" else "juridico"
                instruc=INSTRUCAO.get(intent,INSTRUCAO["resumo"])
                fmt_hint=_FMT.get(plevel,_FMT["juridico"])
                sys_p=build_system_prompt(eff_lvl,ctx)
                msgs=[{"role":"system","content":sys_p}]
                for item in s["messages"][-6:]:
                    if item["role"] in {"user","assistant"}: msgs.append(item)
                msgs[-1]={"role":"user","content":f"{message}\n\n[INSTRUÇÃO: {instruc} {fmt_hint}]"}
                reply=call_openai(msgs, 0.15, max_tokens=MAX_TOKENS_PER_LEVEL.get(eff_lvl, 1500))

                s["messages"].append({"role":"assistant","content":reply})
                if user_id and SUPABASE_OK:
                    SB.registrar_tokens_resposta(user_id, len(reply))
                return ChatOut(message=reply,state=state,prompt_level=eff_lvl)

            except DataJudError as e:
                reply=f"Erro DataJud: {str(e)}"
                s["messages"].append({"role":"assistant","content":reply})
                return ChatOut(message=reply,state=state,prompt_level="erro")

    # ── Chat livre ────────────────────────────────────────────────────
    up_ctx="\n\n".join(
        f"[{x.get('filename')}]\n{compact(x.get('text') or '',4000)}"
        for x in s.get("uploaded_contexts",[])[-3:] if x.get("text")
    ).strip()

    sys_p=build_system_prompt(plevel,up_ctx)
    msgs=[{"role":"system","content":sys_p}]
    for item in s["messages"][-12:]:
        if item["role"] in {"user","assistant"}: msgs.append(item)

    fmt_hint=_FMT.get(plevel,"")
    if fmt_hint and msgs and msgs[-1]["role"]=="user":
        msgs[-1]={"role":"user","content":f"{msgs[-1]['content']}\n\n[FORMATO: {fmt_hint}]"}

    temp=0.1 if plevel=="os61" else (0.2 if plevel in ("juridico","doc_medio") else 0.35)
    try:
        reply=call_openai(msgs, temp, max_tokens=MAX_TOKENS_PER_LEVEL.get(plevel, 1200))
    except Exception as e:
        reply=f"Erro: {str(e)}"

    s["messages"].append({"role":"assistant","content":reply})
    s["messages"]=s["messages"][-20:]
    if user_id and SUPABASE_OK:
        SB.registrar_tokens_resposta(user_id, len(reply))
    return ChatOut(message=reply,state=state,prompt_level=plevel)
