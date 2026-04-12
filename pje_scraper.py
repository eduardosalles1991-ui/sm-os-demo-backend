"""
pje_scraper.py — Consulta Processual Pública do PJe
═══════════════════════════════════════════════════════
Scraping da consulta pública para obter partes, magistrado
e movimentações detalhadas sem necessidade de autenticação.
Funciona para processos públicos (não sigilosos).
═══════════════════════════════════════════════════════
"""
import re
import json
import logging
import requests
from typing import Any, Dict, List, Optional

log = logging.getLogger("pje_scraper")

TIMEOUT = 20
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

# ── Mapa de tribunais → URL base da consulta processual ──────────
PJE_URLS = {
    # Justiça do Trabalho
    "api_publica_trt1":  "https://pje.trt1.jus.br/consultaprocessual",
    "api_publica_trt2":  "https://pje.trt2.jus.br/consultaprocessual",
    "api_publica_trt3":  "https://pje.trt3.jus.br/consultaprocessual",
    "api_publica_trt4":  "https://pje.trt4.jus.br/consultaprocessual",
    "api_publica_trt5":  "https://pje.trt5.jus.br/consultaprocessual",
    "api_publica_trt6":  "https://pje.trt6.jus.br/consultaprocessual",
    "api_publica_trt7":  "https://pje.trt7.jus.br/consultaprocessual",
    "api_publica_trt8":  "https://pje.trt8.jus.br/consultaprocessual",
    "api_publica_trt9":  "https://pje.trt9.jus.br/consultaprocessual",
    "api_publica_trt10": "https://pje.trt10.jus.br/consultaprocessual",
    "api_publica_trt11": "https://pje.trt11.jus.br/consultaprocessual",
    "api_publica_trt12": "https://pje.trt12.jus.br/consultaprocessual",
    "api_publica_trt13": "https://pje.trt13.jus.br/consultaprocessual",
    "api_publica_trt14": "https://pje.trt14.jus.br/consultaprocessual",
    "api_publica_trt15": "https://pje.trt15.jus.br/consultaprocessual",
    "api_publica_trt16": "https://pje.trt16.jus.br/consultaprocessual",
    "api_publica_trt17": "https://pje.trt17.jus.br/consultaprocessual",
    "api_publica_trt18": "https://pje.trt18.jus.br/consultaprocessual",
    "api_publica_trt19": "https://pje.trt19.jus.br/consultaprocessual",
    "api_publica_trt20": "https://pje.trt20.jus.br/consultaprocessual",
    "api_publica_trt21": "https://pje.trt21.jus.br/consultaprocessual",
    "api_publica_trt22": "https://pje.trt22.jus.br/consultaprocessual",
    "api_publica_trt23": "https://pje.trt23.jus.br/consultaprocessual",
    "api_publica_trt24": "https://pje.trt24.jus.br/consultaprocessual",
    "api_publica_tst":   "https://pje.tst.jus.br/consultaprocessual",
}


def _numero_limpo(numero: str) -> str:
    """Remove máscara CNJ, retorna só dígitos."""
    return re.sub(r'\D', '', numero or '')


def _get_base_url(alias: str) -> Optional[str]:
    """Retorna URL base da consulta processual para o tribunal."""
    return PJE_URLS.get(alias)


def consultar_processo(numero: str, alias: str = None) -> Dict[str, Any]:
    """
    Consulta processo na página pública do PJe.
    Tenta múltiplas estratégias:
      1. API interna JSON (se disponível)
      2. HTML scraping da página de detalhe
    
    Retorna dict com: partes, magistrado, advogados, movimentos, etc.
    """
    num_limpo = _numero_limpo(numero)
    if not num_limpo or len(num_limpo) != 20:
        return {"error": f"Número de processo inválido: {numero}"}

    base_url = _get_base_url(alias) if alias else None

    # Se não tem URL mapeada, tenta inferir do número CNJ
    if not base_url:
        # Extrai J.TR do número CNJ (posições 13-16)
        j = num_limpo[13]    # justiça (5 = trabalho)
        tr = num_limpo[14:16]  # tribunal
        tr_int = int(tr)
        if j == '5':
            if tr_int == 0:
                base_url = PJE_URLS.get("api_publica_tst")
            else:
                base_url = PJE_URLS.get(f"api_publica_trt{tr_int}")

    if not base_url:
        return {"error": f"Tribunal não mapeado para consulta pública: {alias or numero}"}

    result = {
        "numero": numero,
        "source": "pje_consulta_publica",
        "polo_ativo": [],
        "polo_passivo": [],
        "advogados": [],
        "magistrado": None,
        "orgao_julgador": None,
        "classe": None,
        "assuntos": [],
        "valor_causa": None,
        "movimentos": [],
        "error": None,
    }

    # ── Estratégia 1: API JSON interna ────────────────────────
    try:
        api_urls = [
            f"{base_url}/api/processos/{num_limpo}",
            f"{base_url}/api/processos/detalhe/{num_limpo}",
        ]
        for api_url in api_urls:
            try:
                r = requests.get(api_url, headers={**HEADERS, "Accept": "application/json"}, timeout=TIMEOUT)
                if r.ok and r.headers.get("content-type", "").startswith("application/json"):
                    data = r.json()
                    parsed = _parse_api_json(data)
                    if parsed.get("polo_ativo") or parsed.get("magistrado"):
                        result.update(parsed)
                        result["source"] = "pje_api_json"
                        log.info(f"[PJe] API JSON OK para {numero}: partes={bool(parsed.get('polo_ativo'))}, juiz={bool(parsed.get('magistrado'))}")
                        return result
            except (requests.RequestException, json.JSONDecodeError):
                continue
    except Exception as e:
        log.debug(f"[PJe] API JSON falhou: {e}")

    # ── Estratégia 2: HTML scraping ───────────────────────────
    try:
        page_url = f"{base_url}/detalhe-processo/{num_limpo}"
        log.info(f"[PJe] Scraping {page_url}")
        r = requests.get(page_url, headers=HEADERS, timeout=TIMEOUT)
        if r.ok:
            parsed = _parse_html(r.text, num_limpo)
            if parsed.get("polo_ativo") or parsed.get("magistrado") or parsed.get("movimentos"):
                result.update(parsed)
                result["source"] = "pje_html_scraping"
                log.info(f"[PJe] HTML OK para {numero}: partes={bool(parsed.get('polo_ativo'))}, juiz={bool(parsed.get('magistrado'))}")
                return result
            else:
                # Pode ser SPA — tentar extrair JSON embutido
                json_data = _extract_embedded_json(r.text)
                if json_data:
                    parsed2 = _parse_api_json(json_data)
                    if parsed2.get("polo_ativo") or parsed2.get("magistrado"):
                        result.update(parsed2)
                        result["source"] = "pje_embedded_json"
                        return result
        else:
            log.warning(f"[PJe] HTTP {r.status_code} para {page_url}")
            result["error"] = f"HTTP {r.status_code}"
    except requests.Timeout:
        log.warning(f"[PJe] Timeout para {numero}")
        result["error"] = "Timeout na consulta pública"
    except Exception as e:
        log.warning(f"[PJe] Scraping falhou para {numero}: {e}")
        result["error"] = str(e)

    return result


def _parse_api_json(data: dict) -> dict:
    """Parseia resposta JSON da API interna do PJe."""
    result = {
        "polo_ativo": [],
        "polo_passivo": [],
        "advogados": [],
        "magistrado": None,
        "orgao_julgador": None,
        "classe": None,
        "assuntos": [],
        "valor_causa": None,
        "movimentos": [],
    }

    if not data or not isinstance(data, dict):
        return result

    # Partes
    partes = data.get("partes") or data.get("polos") or []
    for parte in partes:
        if isinstance(parte, dict):
            nome = parte.get("nome") or parte.get("nomeCompleto") or ""
            tipo = (parte.get("tipo") or parte.get("polo") or parte.get("tipoParte") or "").lower()
            advs_parte = parte.get("advogados") or parte.get("representantes") or []

            if "ativo" in tipo or "autor" in tipo or "reclamante" in tipo:
                if nome: result["polo_ativo"].append(nome)
            elif "passivo" in tipo or "réu" in tipo or "reclamado" in tipo:
                if nome: result["polo_passivo"].append(nome)

            for adv in advs_parte:
                if isinstance(adv, dict):
                    adv_nome = adv.get("nome") or adv.get("nomeCompleto") or ""
                    adv_oab = adv.get("oab") or adv.get("numeroOAB") or ""
                    if adv_nome:
                        result["advogados"].append(f"{adv_nome} (OAB: {adv_oab})" if adv_oab else adv_nome)
                elif isinstance(adv, str):
                    result["advogados"].append(adv)

    # Magistrado
    result["magistrado"] = (
        data.get("magistrado") or
        data.get("juiz") or
        data.get("nomeMagistrado") or
        data.get("magistradoAtual") or
        None
    )
    if isinstance(result["magistrado"], dict):
        result["magistrado"] = result["magistrado"].get("nome") or result["magistrado"].get("nomeCompleto")

    # Órgão julgador
    orgao = data.get("orgaoJulgador") or data.get("vara") or data.get("unidadeJudiciaria") or {}
    if isinstance(orgao, dict):
        result["orgao_julgador"] = orgao.get("nome") or orgao.get("descricao")
    elif isinstance(orgao, str):
        result["orgao_julgador"] = orgao

    # Classe e assuntos
    classe = data.get("classe") or data.get("classeProcessual") or {}
    if isinstance(classe, dict):
        result["classe"] = classe.get("nome") or classe.get("descricao")
    elif isinstance(classe, str):
        result["classe"] = classe

    assuntos = data.get("assuntos") or []
    for a in assuntos:
        if isinstance(a, dict):
            nome = a.get("nome") or a.get("descricao") or ""
            if nome: result["assuntos"].append(nome)
        elif isinstance(a, str):
            result["assuntos"].append(a)

    # Valor da causa
    result["valor_causa"] = data.get("valorCausa") or data.get("valor_causa")

    # Movimentos
    movs = data.get("movimentos") or data.get("movimentacoes") or data.get("andamentos") or []
    for m in movs[:30]:
        if isinstance(m, dict):
            result["movimentos"].append({
                "data": (m.get("dataHora") or m.get("data") or "")[:10],
                "nome": m.get("nome") or m.get("descricao") or m.get("complemento") or "",
                "tipo": m.get("tipo") or "",
            })

    return result


def _parse_html(html: str, numero: str) -> dict:
    """Parseia HTML da consulta processual para extrair dados."""
    result = {
        "polo_ativo": [],
        "polo_passivo": [],
        "advogados": [],
        "magistrado": None,
        "orgao_julgador": None,
        "movimentos": [],
    }

    if not html:
        return result

    # ── Extrair magistrado ────────────────────────────────────
    mag_patterns = [
        r'magistrado["\s:]*[>]*([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+(?:\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇa-záàâãéêíóôõúç]+){1,5})',
        r'juiz["\s:]*[>]*([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+(?:\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇa-záàâãéêíóôõúç]+){1,5})',
        r'Magistrado.*?<[^>]*>([^<]{5,60})</[^>]*>',
        r'Juiz.*?<[^>]*>([^<]{5,60})</[^>]*>',
    ]
    for pat in mag_patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            mag_nome = m.group(1).strip()
            # Filtrar falsos positivos
            if len(mag_nome) > 5 and not any(kw in mag_nome.lower() for kw in ['undefined', 'null', 'nenhum', 'não']):
                result["magistrado"] = mag_nome
                break

    # ── Extrair partes ────────────────────────────────────────
    # Polo ativo (reclamante/autor)
    ativo_patterns = [
        r'polo\s*ativo.*?<[^>]*>([^<]{3,100})</[^>]*>',
        r'reclamante.*?<[^>]*>([^<]{3,100})</[^>]*>',
        r'autor.*?<[^>]*>([^<]{3,100})</[^>]*>',
        r'Polo Ativo[:\s]*([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][^<\n]{3,80})',
    ]
    for pat in ativo_patterns:
        matches = re.findall(pat, html, re.IGNORECASE | re.DOTALL)
        for nome in matches:
            nome = nome.strip()
            if nome and len(nome) > 3 and not any(kw in nome.lower() for kw in ['undefined', '<', '>', 'class=']):
                result["polo_ativo"].append(nome)
        if result["polo_ativo"]:
            break

    # Polo passivo (reclamado/réu)
    passivo_patterns = [
        r'polo\s*passivo.*?<[^>]*>([^<]{3,100})</[^>]*>',
        r'reclamado.*?<[^>]*>([^<]{3,100})</[^>]*>',
        r'r[eé]u.*?<[^>]*>([^<]{3,100})</[^>]*>',
        r'Polo Passivo[:\s]*([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][^<\n]{3,80})',
    ]
    for pat in passivo_patterns:
        matches = re.findall(pat, html, re.IGNORECASE | re.DOTALL)
        for nome in matches:
            nome = nome.strip()
            if nome and len(nome) > 3 and not any(kw in nome.lower() for kw in ['undefined', '<', '>', 'class=']):
                result["polo_passivo"].append(nome)
        if result["polo_passivo"]:
            break

    # ── Extrair advogados ─────────────────────────────────────
    adv_patterns = [
        r'advogado.*?<[^>]*>([^<]{5,80})</[^>]*>',
        r'OAB[/\s]*[A-Z]{2}[/\s]*\d[\d.]*.*?([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+(?:\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇa-záàâãéêíóôõúç]+){1,5})',
    ]
    for pat in adv_patterns:
        matches = re.findall(pat, html, re.IGNORECASE)
        for nome in matches[:5]:
            nome = nome.strip()
            if nome and len(nome) > 5:
                result["advogados"].append(nome)
        if result["advogados"]:
            break

    # ── Extrair órgão julgador ────────────────────────────────
    orgao_patterns = [
        r'(?:vara|[oó]rg[aã]o\s*julgador).*?<[^>]*>([^<]{5,80})</[^>]*>',
        r'Vara[:\s]+([^<\n]{5,80})',
    ]
    for pat in orgao_patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            result["orgao_julgador"] = m.group(1).strip()
            break

    # Deduplicate
    result["polo_ativo"] = list(dict.fromkeys(result["polo_ativo"]))[:5]
    result["polo_passivo"] = list(dict.fromkeys(result["polo_passivo"]))[:5]
    result["advogados"] = list(dict.fromkeys(result["advogados"]))[:10]

    return result


def _extract_embedded_json(html: str) -> Optional[dict]:
    """
    Tenta extrair JSON embutido em páginas SPA do PJe.
    Angular/React apps frequentemente embutem dados iniciais em <script> tags.
    """
    patterns = [
        r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
        r'window\.__DATA__\s*=\s*({.*?});',
        r'var\s+processo\s*=\s*({.*?});',
        r'JSON\.parse\([\'"]({.*?})[\'"]\)',
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    return None


# ── Função de enriquecimento para main.py ─────────────────────────
def enrich_processo(proc: dict, numero: str, alias: str = None) -> dict:
    """
    Enriquece dados do processo com informações da consulta pública do PJe.
    Só busca se faltam partes ou magistrado.
    """
    has_partes = bool(proc.get("polo_ativo")) or bool(proc.get("polo_passivo"))
    has_mag = bool(proc.get("magistrado"))

    if has_partes and has_mag:
        return proc  # já tem dados

    result = consultar_processo(numero, alias)

    if result.get("error"):
        log.debug(f"[PJe] Consulta pública não trouxe dados: {result['error']}")
        return proc

    # Preencher dados faltantes
    if not proc.get("polo_ativo") and result.get("polo_ativo"):
        proc["polo_ativo"] = result["polo_ativo"]
        proc["pje_enriched"] = True

    if not proc.get("polo_passivo") and result.get("polo_passivo"):
        proc["polo_passivo"] = result["polo_passivo"]
        proc["pje_enriched"] = True

    if not proc.get("advogados") and result.get("advogados"):
        proc["advogados"] = result["advogados"]
        proc["pje_enriched"] = True

    if not proc.get("magistrado") and result.get("magistrado"):
        proc["magistrado"] = result["magistrado"]
        proc["pje_enriched"] = True

    if not proc.get("orgao_julgador") and result.get("orgao_julgador"):
        proc["orgao_julgador"] = result["orgao_julgador"]

    if not proc.get("valor_causa") and result.get("valor_causa"):
        proc["valor_causa"] = result["valor_causa"]

    if proc.get("pje_enriched"):
        log.info(f"[PJe] Processo {numero} enriquecido via consulta pública")

    return proc


def is_configured() -> bool:
    """PJe consulta pública é sempre disponível — sem autenticação."""
    return True
