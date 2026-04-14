"""
STF Jurisprudência Client — Jurimetrix
═══════════════════════════════════════
Consulta a API pública do STF para buscar acórdãos, súmulas e decisões.
Sem API key necessária.
"""
import re
import logging
import requests
from typing import Any, Dict, List, Optional

log = logging.getLogger("smos")

STF_API_URL = "https://jurisprudencia.stf.jus.br/api/search/search"
STF_TIMEOUT = 20

# Campos retornados pela API
STF_ACORDAO_FIELDS = [
    "id", "titulo", "processo_numero",
    "processo_classe_processual_unificada_classe_sigla",
    "julgamento_data", "publicacao_data",
    "relator_processo_nome", "relator_acordao_nome",
    "orgao_julgador", "ementa_texto",
    "inteiro_teor_url",
    "documental_tese_texto", "documental_tese_tema_texto",
    "documental_legislacao_citada_texto",
    "documental_indexacao_texto",
    "is_repercussao_geral",
]


def is_configured() -> bool:
    """STF API é pública, sempre disponível."""
    return True


def _clean_html(text: str) -> str:
    """Remove tags HTML e limpa texto."""
    if not text:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def search(
    query: str,
    base: str = "acordaos",
    page: int = 1,
    page_size: int = 10,
) -> Dict[str, Any]:
    """
    Busca jurisprudência no STF.
    
    Args:
        query: Texto de busca (ex: "dano moral", "horas extras")
        base: "acordaos", "sumulas", "monocraticas", "informativos"
        page: Página de resultados
        page_size: Itens por página (max 250)
    
    Returns:
        Dict com resultados estruturados
    """
    try:
        payload = {
            "query": query,
            "base": base,
            "page": page,
            "pageSize": min(page_size, 250),
        }
        
        r = requests.post(
            STF_API_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=STF_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        
        results = []
        hits = data.get("result", [])
        
        for hit in hits:
            doc = _parse_acordao(hit) if base == "acordaos" else _parse_generic(hit)
            if doc:
                results.append(doc)
        
        return {
            "ok": True,
            "total": data.get("total_count", len(results)),
            "page": page,
            "results": results,
        }
        
    except requests.RequestException as e:
        log.warning(f"[STF] Erro na busca: {e}")
        return {"ok": False, "total": 0, "results": [], "error": str(e)}
    except Exception as e:
        log.warning(f"[STF] Erro inesperado: {e}")
        return {"ok": False, "total": 0, "results": [], "error": str(e)}


def _parse_acordao(hit: dict) -> Optional[Dict[str, Any]]:
    """Parseia um acórdão do STF."""
    if not hit:
        return None
    
    ementa = _clean_html(hit.get("ementa_texto") or "")
    if not ementa:
        return None
    
    processo = hit.get("processo_numero") or ""
    classe = hit.get("processo_classe_processual_unificada_classe_sigla") or ""
    titulo = hit.get("titulo") or ""
    
    # Relator
    relator = (hit.get("relator_acordao_nome") or hit.get("relator_processo_nome") or "")
    
    # Tese e legislação
    tese = _clean_html(hit.get("documental_tese_texto") or "")
    tema = _clean_html(hit.get("documental_tese_tema_texto") or "")
    legislacao = _clean_html(hit.get("documental_legislacao_citada_texto") or "")
    indexacao = _clean_html(hit.get("documental_indexacao_texto") or "")
    
    # URL do inteiro teor
    url = hit.get("inteiro_teor_url") or ""
    if url and url.startswith("//"):
        url = "https:" + url
    
    return {
        "id": hit.get("id"),
        "processo": processo,
        "classe": classe,
        "titulo": titulo,
        "relator": relator,
        "orgao_julgador": hit.get("orgao_julgador") or "",
        "data_julgamento": hit.get("julgamento_data") or "",
        "data_publicacao": hit.get("publicacao_data") or "",
        "ementa": ementa[:2000],  # Limitar tamanho
        "tese": tese[:1000] if tese else "",
        "tema": tema,
        "legislacao_citada": legislacao[:500] if legislacao else "",
        "indexacao": indexacao[:500] if indexacao else "",
        "url": url,
        "repercussao_geral": bool(hit.get("is_repercussao_geral")),
        "tribunal": "STF",
        "tipo": "acordao",
    }


def _parse_generic(hit: dict) -> Optional[Dict[str, Any]]:
    """Parseia resultado genérico (súmulas, monocráticas, informativos)."""
    if not hit:
        return None
    
    texto = _clean_html(
        hit.get("ementa_texto") or 
        hit.get("decisao_texto") or 
        hit.get("informativo_resumo_texto") or ""
    )
    if not texto:
        return None
    
    return {
        "id": hit.get("id"),
        "processo": hit.get("processo_numero") or "",
        "classe": hit.get("processo_classe_processual_unificada_classe_sigla") or "",
        "titulo": hit.get("titulo") or hit.get("informativo_titulo") or "",
        "relator": hit.get("relator_processo_nome") or "",
        "orgao_julgador": hit.get("orgao_julgador") or "",
        "data_julgamento": hit.get("julgamento_data") or hit.get("informativo_data") or "",
        "data_publicacao": hit.get("publicacao_data") or "",
        "ementa": texto[:2000],
        "tese": "",
        "tema": "",
        "legislacao_citada": "",
        "indexacao": "",
        "url": hit.get("inteiro_teor_url") or "",
        "repercussao_geral": False,
        "tribunal": "STF",
        "tipo": "generico",
    }


def search_by_tema(tema: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Busca jurisprudência relevante por tema/assunto."""
    result = search(tema, base="acordaos", page_size=limit)
    return result.get("results", [])


def build_context(results: List[Dict[str, Any]], max_results: int = 5) -> str:
    """Constrói contexto textual para GPT a partir de resultados STF."""
    if not results:
        return ""
    
    lines = [f"\nJURISPRUDÊNCIA STF ({len(results[:max_results])} acórdãos relevantes):\n"]
    
    for i, r in enumerate(results[:max_results], 1):
        lines.append(f"── [{i}] {r.get('classe','')} {r.get('processo','')} | Rel. {r.get('relator','')}")
        lines.append(f"   Julgamento: {r.get('data_julgamento','')} | {r.get('orgao_julgador','')}")
        
        ementa = r.get("ementa", "")
        if len(ementa) > 400:
            ementa = ementa[:400] + "..."
        lines.append(f"   Ementa: {ementa}")
        
        if r.get("tese"):
            tese = r["tese"][:300]
            lines.append(f"   Tese: {tese}")
        
        if r.get("legislacao_citada"):
            lines.append(f"   Legislação: {r['legislacao_citada'][:200]}")
        
        if r.get("repercussao_geral"):
            lines.append(f"   ⚖️ REPERCUSSÃO GERAL")
        
        lines.append("")
    
    return "\n".join(lines)


# Singleton-like access
class STFClient:
    search = staticmethod(search)
    search_by_tema = staticmethod(search_by_tema)
    build_context = staticmethod(build_context)

STF = STFClient()
