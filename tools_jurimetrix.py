"""
tools_jurimetrix.py — Tools para tool use do JurimetrixIA.
=========================================================

Cada tool é uma função Python que recebe argumentos (vindos do Claude),
consulta uma fonte (Postgres / Escavador / Web) e retorna dict serializável.

Schemas Anthropic (formato 2024-10-22+) ficam em TOOLS_SCHEMA.

Princípio:
  1. Postgres SEMPRE primeiro (rápido, sem custo, sem rate limit).
  2. Escavador para enriquecimento quando dado faltar ou estiver stale.
  3. Web search para perguntas factuais sobre a "vida real" do direito
     (notícias, mudanças legislativas, súmulas recentes).

Side effects desejado: tudo que vem do Escavador é GRAVADO no Postgres
(tabelas processos_base, jurisprudencia, magistrados_oficial) para
crescimento orgânico — definido em persistir_escavador().

Author: Eduardo + Jurimetrix engine · 02/05/2026
"""

from __future__ import annotations
import logging
import time
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger("tools_jurimetrix")

# ═══════════════════════════════════════════════════════════════════
# TOOL SCHEMAS (Anthropic format)
# ═══════════════════════════════════════════════════════════════════

TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "name": "consultar_processo_cnj",
        "description": (
            "Consulta um processo judicial pelo número CNJ (formato "
            "NNNNNNN-DD.AAAA.J.TR.OOOO ou só dígitos). Retorna dados "
            "estruturados: classe, assunto, partes, magistrado, valor, "
            "movimentações. Tenta Postgres primeiro; se não encontrar ou "
            "se 'force_refresh=true', enriquece via Escavador e GRAVA no "
            "banco para crescimento orgânico. Use sempre que o usuário "
            "mencionar um número de processo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cnj": {
                    "type": "string",
                    "description": "Número CNJ do processo. Aceita com ou sem máscara.",
                },
                "force_refresh": {
                    "type": "boolean",
                    "description": "Se true, ignora cache do Postgres e busca direto no Escavador. Default: false.",
                    "default": False,
                },
            },
            "required": ["cnj"],
        },
    },
    {
        "name": "buscar_jurisprudencia",
        "description": (
            "Busca decisões e acórdãos na base de jurisprudência (2,7M decisões "
            "indexadas: STJ, TJSP, TJMG, TRT1, TJDF, TRF2 e outros). Retorna "
            "ementas + tribunal + relator + data, ordenadas por relevância. "
            "Use quando o usuário perguntar 'qual o entendimento sobre...', "
            "'há jurisprudência sobre...', 'precedentes para...'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Termos de busca em linguagem natural (ex: 'dano moral inscrição indevida SPC').",
                },
                "tribunal": {
                    "type": "string",
                    "description": "Filtro opcional por sigla (ex: 'STJ', 'TJSP', 'TJMG'). Omitir para buscar em todos.",
                },
                "limite": {
                    "type": "integer",
                    "description": "Quantos resultados retornar. Default: 5, max: 15.",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "consultar_magistrado",
        "description": (
            "Consulta dados oficiais e jurimétricos de um magistrado brasileiro. "
            "Retorna: cargo, vara, tempo de magistratura, taxa de procedência, "
            "classes mais julgadas, vocabulário recorrente, padrão pró/contra. "
            "Cobertura: 3.491 magistrados TJSP (DadosJusBr + CNJ). "
            "Use quando perguntarem 'como o juiz X decide', 'padrão decisório de Y'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nome": {
                    "type": "string",
                    "description": "Nome do magistrado. Aceita variações (busca fuzzy via trigram).",
                },
            },
            "required": ["nome"],
        },
    },
    {
        "name": "buscar_cnpj",
        "description": (
            "Consulta dados de uma empresa pelo CNPJ na base da Receita Federal "
            "(24,2M empresas). Cruza com base de processos para retornar "
            "score de litigiosidade: total de processos, taxa de derrota, "
            "% de acordos. Use quando o usuário citar CNPJ ou mencionar "
            "'a parte contrária', 'o réu (empresa)', 'a reclamada'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cnpj": {
                    "type": "string",
                    "description": "CNPJ com ou sem máscara (14 dígitos).",
                },
            },
            "required": ["cnpj"],
        },
    },
    {
        "name": "enriquecer_escavador",
        "description": (
            "Força enriquecimento via API Escavador para um processo CNJ. "
            "USE APENAS quando 'consultar_processo_cnj' retornar dados "
            "incompletos ou desatualizados E o usuário confirmar que quer "
            "buscar atualização real-time. É operação cara (5-15s) e gasta "
            "créditos da API. Resultado é gravado no banco automaticamente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cnj": {
                    "type": "string",
                    "description": "Número CNJ do processo a enriquecer.",
                },
            },
            "required": ["cnj"],
        },
    },
    {
        "name": "buscar_web",
        "description": (
            "Busca na web por informação atualizada que NÃO está no banco "
            "Jurimetrix. Use para: súmulas/temas STF/STJ recentes (após 2025-Q3), "
            "alterações legislativas novas, notícias jurídicas, casos públicos "
            "recentes. NÃO use para jurisprudência geral (use buscar_jurisprudencia) "
            "nem dados de processo (use consultar_processo_cnj)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Pergunta de busca, em linguagem natural.",
                },
            },
            "required": ["query"],
        },
    },
]


# ═══════════════════════════════════════════════════════════════════
# UTILITÁRIOS
# ═══════════════════════════════════════════════════════════════════

def _normalizar_cnj(cnj: str) -> str:
    """'0123456-78.2024.5.02.0001' -> '01234567820245020001' (20 dígitos)."""
    return re.sub(r"[^\d]", "", cnj or "")


def _normalizar_cnpj(cnpj: str) -> str:
    """'12.345.678/0001-90' -> '12345678000190' (14 dígitos)."""
    return re.sub(r"[^\d]", "", cnpj or "")


def _err(msg: str, **extras) -> Dict[str, Any]:
    return {"ok": False, "erro": msg, **extras}


def _ok(data: Any = None, **extras) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": True, **extras}
    if data is not None:
        out["data"] = data
    return out


# ═══════════════════════════════════════════════════════════════════
# IMPLEMENTAÇÕES
# ═══════════════════════════════════════════════════════════════════

def consultar_processo_cnj(
    cnj: str,
    force_refresh: bool = False,
    *,
    SB=None,
    ESC=None,
) -> Dict[str, Any]:
    """
    Consulta processo no banco. Se não achar OU force_refresh, enriquece
    via Escavador e GRAVA no banco. Retorna sempre dict serializável.
    """
    cnj_norm = _normalizar_cnj(cnj)
    if len(cnj_norm) != 20:
        return _err(f"CNJ inválido: '{cnj}' (precisa 20 dígitos, veio {len(cnj_norm)})")

    fonte = "postgres"
    proc: Optional[Dict[str, Any]] = None

    # 1. Postgres primeiro (sempre, exceto se force_refresh)
    if not force_refresh and SB is not None:
        try:
            proc = SB.DB.get_processo_by_cnj(cnj_norm)
            if proc:
                log.info(f"[tool consultar_processo_cnj] Hit Postgres: {cnj_norm}")
        except Exception as e:
            log.warning(f"[tool consultar_processo_cnj] erro Postgres: {e}")

    # 2. Se vazio ou force_refresh: Escavador
    if (not proc or force_refresh) and ESC is not None:
        try:
            log.info(f"[tool consultar_processo_cnj] Buscando Escavador: {cnj_norm}")
            esc_result = ESC.ESCAVADOR.buscar_processo_por_numero(cnj_norm)
            if esc_result and esc_result.get("ok"):
                proc = esc_result.get("processo") or esc_result.get("data") or esc_result
                fonte = "escavador"
                # Persistir!
                try:
                    persistir_escavador_processo(SB, cnj_norm, proc)
                except Exception as e:
                    log.error(f"[tool persistir] falhou: {e}")
        except Exception as e:
            log.error(f"[tool consultar_processo_cnj] Escavador falhou: {e}")
            return _err(f"Escavador indisponível: {e}", cnj=cnj_norm)

    if not proc:
        return _err(f"Processo {cnj_norm} não encontrado em nenhuma fonte.", cnj=cnj_norm)

    # Truncar campos verbosos para Claude não estourar tokens
    return _ok(
        data={
            "cnj": cnj_norm,
            "classe": proc.get("classe") or proc.get("classe_processual"),
            "assunto": proc.get("assunto"),
            "tribunal": proc.get("tribunal") or proc.get("sigla_tribunal"),
            "vara": proc.get("vara") or proc.get("orgao_julgador"),
            "magistrado": proc.get("magistrado") or proc.get("juiz"),
            "valor_causa": proc.get("valor_causa") or proc.get("valor"),
            "data_distribuicao": proc.get("data_distribuicao"),
            "ultimo_movimento": proc.get("ultimo_movimento"),
            "movimentacoes_count": proc.get("movimentacoes_count") or proc.get("total_movimentos"),
            "partes": (proc.get("partes") or [])[:6],  # máx 6 partes
            "resultado": proc.get("resultado"),
        },
        fonte=fonte,
    )


def buscar_jurisprudencia(
    query: str,
    tribunal: Optional[str] = None,
    limite: int = 5,
    *,
    SB=None,
) -> Dict[str, Any]:
    """Busca semântica/textual em jurisprudencia (2,7M ementas)."""
    if not query or len(query.strip()) < 3:
        return _err("Query muito curta (mínimo 3 caracteres).")

    limite = max(1, min(limite, 15))

    if SB is None or not hasattr(SB, "DB"):
        return _err("Banco indisponível.")

    try:
        rows = SB.DB.search_jurisprudencia(
            query=query.strip(),
            tribunal=(tribunal or "").upper().strip() or None,
            limit=limite,
        )
    except AttributeError:
        return _err("Função search_jurisprudencia não implementada em SB.DB. Adicionar no sb_client.py.")
    except Exception as e:
        log.error(f"[tool buscar_jurisprudencia] {e}")
        return _err(f"Erro na busca: {e}")

    if not rows:
        return _ok(data=[], total=0)

    # Truncar ementas pra não estourar tokens
    resultados = []
    for r in rows:
        ementa = r.get("ementa") or r.get("texto") or ""
        if len(ementa) > 600:
            ementa = ementa[:600] + "…"
        resultados.append({
            "tribunal": r.get("tribunal") or r.get("sigla"),
            "relator": r.get("relator") or r.get("magistrado"),
            "data": r.get("data_publicacao") or r.get("data"),
            "classe": r.get("classe"),
            "ementa_preview": ementa,
            "id": r.get("id"),
        })

    return _ok(data=resultados, total=len(resultados))


def consultar_magistrado(nome: str, *, SB=None) -> Dict[str, Any]:
    """Dados oficiais + jurimétricos de magistrado (TJSP atualmente)."""
    if not nome or len(nome.strip()) < 3:
        return _err("Nome muito curto.")

    if SB is None or not hasattr(SB, "DB"):
        return _err("Banco indisponível.")

    try:
        m = SB.DB.search_magistrado(nome.strip())
    except AttributeError:
        return _err("Função search_magistrado não implementada em SB.DB. Adicionar no sb_client.py.")
    except Exception as e:
        log.error(f"[tool consultar_magistrado] {e}")
        return _err(f"Erro: {e}")

    if not m:
        return _err(f"Magistrado '{nome}' não encontrado na base oficial.", sugestao="Apenas TJSP coberto até o momento.")

    return _ok(data={
        "nome": m.get("nome"),
        "cargo": m.get("cargo"),
        "vara": m.get("vara") or m.get("orgao"),
        "tribunal": m.get("tribunal") or "TJSP",
        "matricula": m.get("matricula"),
        "anos_magistratura": m.get("anos_magistratura"),
        "total_julgados": m.get("total_julgados"),
        "taxa_procedencia": m.get("taxa_procedencia"),
        "tempo_medio_sentenca_dias": m.get("tempo_medio_sentenca"),
        "classes_top5": (m.get("classes_top") or [])[:5],
        "padrao_textual": m.get("padrao_textual") or m.get("vocabulario"),
    })


def buscar_cnpj(cnpj: str, *, SB=None) -> Dict[str, Any]:
    """Dados Receita + score de litigiosidade."""
    cnpj_norm = _normalizar_cnpj(cnpj)
    if len(cnpj_norm) != 14:
        return _err(f"CNPJ inválido: '{cnpj}'.")

    if SB is None or not hasattr(SB, "DB"):
        return _err("Banco indisponível.")

    try:
        emp = SB.DB.get_empresa_by_cnpj(cnpj_norm)
    except AttributeError:
        return _err("Função get_empresa_by_cnpj não implementada em SB.DB.")
    except Exception as e:
        return _err(f"Erro: {e}")

    if not emp:
        return _err(f"CNPJ {cnpj_norm} não encontrado na Receita.")

    # Score de litigiosidade
    try:
        score = SB.DB.get_score_litigiosidade(cnpj_norm) or {}
    except Exception:
        score = {}

    return _ok(data={
        "cnpj": cnpj_norm,
        "razao_social": emp.get("razao_social") or emp.get("nome"),
        "nome_fantasia": emp.get("nome_fantasia"),
        "uf": emp.get("uf"),
        "porte": emp.get("porte"),
        "atividade_principal": emp.get("cnae_principal") or emp.get("atividade"),
        "situacao": emp.get("situacao_cadastral"),
        "litigiosidade": {
            "score": score.get("score"),
            "total_processos": score.get("total_processos"),
            "taxa_derrota": score.get("taxa_derrota"),
            "taxa_acordo": score.get("taxa_acordo"),
            "interpretacao": score.get("interpretacao"),  # ex: "litigante hostil"
        },
    })


def enriquecer_escavador(cnj: str, *, SB=None, ESC=None) -> Dict[str, Any]:
    """Força refresh via Escavador. Operação cara, usar com critério."""
    return consultar_processo_cnj(cnj, force_refresh=True, SB=SB, ESC=ESC)


def buscar_web(query: str, *, web_search_fn=None) -> Dict[str, Any]:
    """
    Busca web. Em produção, conecta com Anthropic web_search nativo
    (parâmetro `tools=[{"type":"web_search_20241211", ...}]` no API call)
    OU com Brave/Tavily/Serper API.

    Aqui devolvemos placeholder caso não haja web_search_fn — Claude
    decide se precisa, e tu pode habilitar a tool nativa Anthropic
    direto no tool schema (ver USAGE no fim deste arquivo).
    """
    if web_search_fn is None:
        return _err(
            "Web search não configurada neste handler. "
            "Use a tool nativa Anthropic web_search_20241211 no schema, "
            "ou plugue Brave/Tavily/Serper aqui.",
            sugestao="Adicione web_search_20241211 ao TOOLS_SCHEMA do call_claude_with_tools.",
        )
    try:
        results = web_search_fn(query)
        return _ok(data=results)
    except Exception as e:
        return _err(f"Web search falhou: {e}")


# ═══════════════════════════════════════════════════════════════════
# PERSISTÊNCIA — crescimento orgânico
# ═══════════════════════════════════════════════════════════════════

def persistir_escavador_processo(SB, cnj: str, dados: Dict[str, Any]) -> None:
    """
    Grava em processos_base o que veio do Escavador.
    Campos esperados (idempotente via UPSERT por cnj_normalizado).
    """
    if not SB or not hasattr(SB, "DB"):
        return
    try:
        if hasattr(SB.DB, "upsert_processo_from_escavador"):
            SB.DB.upsert_processo_from_escavador(cnj, dados)
            log.info(f"[persistir] processo {cnj} gravado em processos_base")
        else:
            log.warning(
                "[persistir] SB.DB.upsert_processo_from_escavador não existe. "
                "Sprint 2 pendente: adicionar essa função em sb_client.py."
            )
    except Exception as e:
        log.error(f"[persistir] falhou: {e}")


# ═══════════════════════════════════════════════════════════════════
# DISPATCHER
# ═══════════════════════════════════════════════════════════════════

def execute_tool(
    name: str,
    args: Dict[str, Any],
    *,
    SB=None,
    ESC=None,
    web_search_fn=None,
) -> Dict[str, Any]:
    """
    Recebe nome da tool + args (já vindos do Claude tool_use block) e executa.
    Retorna dict serializável que volta pro Claude como tool_result.
    """
    t0 = time.time()
    log.info(f"[execute_tool] → {name}({list(args.keys())})")

    try:
        if name == "consultar_processo_cnj":
            result = consultar_processo_cnj(
                cnj=args.get("cnj", ""),
                force_refresh=bool(args.get("force_refresh", False)),
                SB=SB, ESC=ESC,
            )
        elif name == "buscar_jurisprudencia":
            result = buscar_jurisprudencia(
                query=args.get("query", ""),
                tribunal=args.get("tribunal"),
                limite=int(args.get("limite", 5)),
                SB=SB,
            )
        elif name == "consultar_magistrado":
            result = consultar_magistrado(nome=args.get("nome", ""), SB=SB)
        elif name == "buscar_cnpj":
            result = buscar_cnpj(cnpj=args.get("cnpj", ""), SB=SB)
        elif name == "enriquecer_escavador":
            result = enriquecer_escavador(cnj=args.get("cnj", ""), SB=SB, ESC=ESC)
        elif name == "buscar_web":
            result = buscar_web(query=args.get("query", ""), web_search_fn=web_search_fn)
        else:
            result = _err(f"Tool desconhecida: {name}")
    except Exception as e:
        log.exception(f"[execute_tool] {name} threw")
        result = _err(f"Exception em {name}: {e}")

    elapsed = time.time() - t0
    log.info(f"[execute_tool] ← {name} ok={result.get('ok')} {elapsed*1000:.0f}ms")
    return result


# ═══════════════════════════════════════════════════════════════════
# USAGE no main.py
# ═══════════════════════════════════════════════════════════════════
#
# from tools_jurimetrix import TOOLS_SCHEMA, execute_tool
#
# def chat_with_tools(user_message, history, system_prompt, max_iter=5):
#     messages = history + [{"role": "user", "content": user_message}]
#
#     # Adiciona web_search nativa Anthropic ao schema (opcional)
#     tools_full = TOOLS_SCHEMA + [{"type": "web_search_20241211", "name": "web_search"}]
#
#     for i in range(max_iter):
#         resp = call_claude_with_tools(
#             system=system_prompt,
#             messages=messages,
#             tools=tools_full,
#         )
#
#         # Se Claude pediu tool, executa
#         tool_uses = [b for b in resp["content"] if b.get("type") == "tool_use"]
#         if not tool_uses:
#             # Resposta final em texto
#             return resp
#
#         # Adiciona response do assistant ao histórico
#         messages.append({"role": "assistant", "content": resp["content"]})
#
#         # Executa cada tool e devolve
#         tool_results = []
#         for tu in tool_uses:
#             out = execute_tool(tu["name"], tu["input"], SB=SB, ESC=ESC)
#             tool_results.append({
#                 "type": "tool_result",
#                 "tool_use_id": tu["id"],
#                 "content": json.dumps(out, ensure_ascii=False),
#             })
#         messages.append({"role": "user", "content": tool_results})
#
#     return resp  # max_iter atingido, retorna última
