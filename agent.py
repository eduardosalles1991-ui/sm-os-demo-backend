"""
Jurimetrix Claude Agent — tool use sobre Supabase
═══════════════════════════════════════════════════════
v1.0 — 28/abr/26
Permite que Claude consulte processos_base e jurisprudencia
diretamente via tool use (function calling).
"""
import os
import json
import logging
from typing import List, Dict, Any, Optional

import requests

log = logging.getLogger("smos.agent")

# ═══════════════════════════════════════════════════════
# CREDENCIAIS (lê do ambiente — independente do main.py)
# ═══════════════════════════════════════════════════════
ANTHROPIC_API_KEY = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
ANTHROPIC_MODEL   = (os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-6").strip()
ANTHROPIC_VERSION = (os.getenv("ANTHROPIC_VERSION") or "2023-06-01").strip()
SUPABASE_URL      = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SUPABASE_KEY      = (os.getenv("SUPABASE_SERVICE_KEY") or "").strip()


def _sb_headers() -> Dict[str, str]:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def is_configured() -> bool:
    return bool(ANTHROPIC_API_KEY and SUPABASE_URL and SUPABASE_KEY)


# ═══════════════════════════════════════════════════════
# DEFINIÇÕES DAS TOOLS (visíveis pelo Claude)
# ═══════════════════════════════════════════════════════
AGENT_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "query_processos_base",
        "description": (
            "Consulta a base de processos enriquecidos do Jurimetrix (~284 mil processos brasileiros). "
            "Cada registro contém número CNJ, tribunal, vara, magistrado, data de ajuizamento, "
            "valor da causa, assuntos (lista) e resultado do julgamento. "
            "Use esta ferramenta sempre que o usuário pedir lista de processos por critério "
            "(tribunal, juiz, vara, assunto, período). Retorna até 100 processos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tribunal": {
                    "type": "string",
                    "description": "Sigla do tribunal exatamente como armazenado: TJSP, TRT2-SP, TRT1-RJ, TJMG, TJRS, STJ, STF, TST, etc."
                },
                "magistrado": {
                    "type": "string",
                    "description": "Nome (parcial) do juiz/magistrado. Busca case-insensitive com wildcard."
                },
                "assunto": {
                    "type": "string",
                    "description": "Assunto exato como aparece no DataJud, ex: 'Horas Extras', 'Adicional de Insalubridade', 'Dano Moral'. Filtro de array contains."
                },
                "vara": {
                    "type": "string",
                    "description": "Nome (parcial) da vara/órgão julgador. Ex: '1ª Vara do Trabalho de São Paulo'."
                },
                "ano_min": {"type": "integer", "description": "Ano mínimo de ajuizamento (ex: 2020)"},
                "ano_max": {"type": "integer", "description": "Ano máximo de ajuizamento (ex: 2025)"},
                "resultado": {
                    "type": "string",
                    "enum": ["procedente", "improcedente", "parcial", "acordo", "extinto", "arquivado"],
                    "description": "Filtrar por resultado do julgamento"
                },
                "limit": {
                    "type": "integer",
                    "description": "Máximo de resultados (padrão 50, máximo 100)"
                }
            }
        }
    },
    {
        "name": "query_jurisprudencia",
        "description": (
            "Consulta a base de jurisprudência (~2 milhões de decisões: STJ 634K, TJMG 611K, TRT1 320K, "
            "TJSP 83K, TJDF, TRF2, TJRJ, TJPR, TJMT, TJPA, STF, TCU acórdãos, etc.). "
            "Use para encontrar precedentes, ementas, acórdãos sobre algum tema jurídico. "
            "A busca é por palavra na ementa (case-insensitive). Retorna até 50 decisões."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tribunal": {"type": "string", "description": "STJ, STF, TJSP, TJMG, TRT1, TCU, etc."},
                "tema": {
                    "type": "string",
                    "description": "Palavra ou expressão para buscar dentro da ementa. Ex: 'dano moral', 'horas extras', 'multa contratual'."
                },
                "ano_min": {"type": "integer"},
                "ano_max": {"type": "integer"},
                "limit": {"type": "integer", "description": "Máximo (padrão 20, máximo 50)"}
            }
        }
    },
    {
        "name": "aggregate_processos",
        "description": (
            "Agrega estatísticas sobre processos. Use para perguntas tipo: "
            "'qual magistrado é mais favorável a horas extras', "
            "'qual a taxa de êxito na 5ª vara', "
            "'quantos processos por tribunal'. "
            "Aplica filtros antes de agrupar e ordena resultados por volume. "
            "Retorna total, favoráveis (procedente+parcial+acordo) e taxa de êxito por grupo. "
            "Limite: amostra de até 1000 processos para a agregação."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "group_by": {
                    "type": "string",
                    "enum": ["tribunal", "magistrado", "orgao_julgador", "ano", "resultado"],
                    "description": "Campo de agrupamento. 'orgao_julgador' = vara. 'ano' = ano de ajuizamento."
                },
                "tribunal": {"type": "string", "description": "Filtrar por tribunal antes de agregar"},
                "magistrado": {"type": "string", "description": "Filtrar por nome (parcial) do juiz antes de agregar"},
                "assunto": {"type": "string", "description": "Filtrar por assunto antes de agregar"},
                "vara": {"type": "string", "description": "Filtrar por vara antes de agregar"},
                "ano_min": {"type": "integer"},
                "ano_max": {"type": "integer"}
            },
            "required": ["group_by"]
        }
    }
]


# ═══════════════════════════════════════════════════════
# EXECUTORES DAS TOOLS
# ═══════════════════════════════════════════════════════
def _build_processos_filters(args: Dict[str, Any]) -> List[str]:
    filters = []
    if args.get("tribunal"):
        filters.append(f"tribunal=eq.{args['tribunal'].strip()}")
    if args.get("magistrado"):
        m = args["magistrado"].strip().replace("*", "").replace(",", " ")
        filters.append(f"magistrado=ilike.*{m}*")
    if args.get("assunto"):
        a = args["assunto"].strip()
        # PostgREST array contains: assuntos=cs.{"Horas Extras"}
        filters.append(f"assuntos=cs.{{{json.dumps(a, ensure_ascii=False)}}}")
    if args.get("vara"):
        v = args["vara"].strip().replace("*", "")
        filters.append(f"orgao_julgador=ilike.*{v}*")
    if args.get("ano_min"):
        filters.append(f"data_ajuizamento=gte.{int(args['ano_min'])}-01-01")
    if args.get("ano_max"):
        filters.append(f"data_ajuizamento=lte.{int(args['ano_max'])}-12-31")
    if args.get("resultado"):
        filters.append(f"resultado=eq.{args['resultado'].strip()}")
    return filters


def execute_query_processos(args: Dict[str, Any]) -> Dict[str, Any]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"error": "Supabase não configurado"}

    filters = _build_processos_filters(args)
    limit = max(1, min(int(args.get("limit") or 50), 100))

    fields = "numero_cnj,tribunal,orgao_julgador,magistrado,data_ajuizamento,valor_causa,assuntos,resultado,duracao_dias"
    qs = "&".join(filters + [f"select={fields}", f"limit={limit}", "order=data_ajuizamento.desc.nullslast"])
    url = f"{SUPABASE_URL}/rest/v1/processos_base?{qs}"

    try:
        r = requests.get(url, headers=_sb_headers(), timeout=15)
        if 200 <= r.status_code < 300:
            results = r.json()
            return {"ok": True, "count": len(results), "results": results}
        return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
    except Exception as e:
        return {"error": f"Erro ao consultar processos_base: {e}"}


def execute_query_jurisprudencia(args: Dict[str, Any]) -> Dict[str, Any]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"error": "Supabase não configurado"}

    filters = []
    if args.get("tribunal"):
        filters.append(f"tribunal=eq.{args['tribunal'].strip()}")
    if args.get("tema"):
        t = args["tema"].strip().replace("*", "")
        # Tenta primeiro em ementa; se schema usar outro nome, ajustar aqui
        filters.append(f"ementa=ilike.*{t}*")
    if args.get("ano_min"):
        filters.append(f"data_julgamento=gte.{int(args['ano_min'])}-01-01")
    if args.get("ano_max"):
        filters.append(f"data_julgamento=lte.{int(args['ano_max'])}-12-31")

    limit = max(1, min(int(args.get("limit") or 20), 50))
    qs = "&".join(filters + [f"limit={limit}", "order=data_julgamento.desc.nullslast"])
    url = f"{SUPABASE_URL}/rest/v1/jurisprudencia?{qs}"

    try:
        r = requests.get(url, headers=_sb_headers(), timeout=20)
        if 200 <= r.status_code < 300:
            results = r.json()
            # Truncar ementas longas para não saturar contexto do Claude
            for item in results:
                ementa = item.get("ementa")
                if ementa and isinstance(ementa, str) and len(ementa) > 600:
                    item["ementa"] = ementa[:600] + "..."
            return {"ok": True, "count": len(results), "results": results}
        return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
    except Exception as e:
        return {"error": f"Erro ao consultar jurisprudencia: {e}"}


def execute_aggregate(args: Dict[str, Any]) -> Dict[str, Any]:
    """Trae até 1000 processos com filtros e agrega em Python."""
    args_copy = dict(args)
    group_by = args_copy.pop("group_by", "tribunal")
    args_copy["limit"] = 1000

    raw = execute_query_processos(args_copy)
    if raw.get("error"):
        return raw

    rows = raw.get("results", []) or []
    if not rows:
        return {"ok": True, "group_by": group_by, "total_amostra": 0, "groups": []}

    agg: Dict[str, Dict[str, int]] = {}

    for row in rows:
        if group_by == "ano":
            data = row.get("data_ajuizamento") or ""
            data_str = str(data)
            key = data_str[:4] if len(data_str) >= 4 and data_str[:4].isdigit() else "n/d"
        else:
            key = row.get(group_by) or "não identificado"

        key_str = str(key) if key is not None else "não identificado"

        if key_str not in agg:
            agg[key_str] = {
                "total": 0, "procedente": 0, "improcedente": 0, "parcial": 0,
                "acordo": 0, "extinto": 0, "arquivado": 0, "indeterminado": 0,
            }

        agg[key_str]["total"] += 1
        res = row.get("resultado") or "indeterminado"
        if res in agg[key_str]:
            agg[key_str][res] += 1
        else:
            agg[key_str]["indeterminado"] += 1

    groups = []
    for k, v in agg.items():
        fav = v["procedente"] + v["parcial"] + v["acordo"]
        groups.append({
            "grupo": k,
            "total": v["total"],
            "favoraveis": fav,
            "taxa_exito": round(fav / v["total"] * 100, 1) if v["total"] > 0 else 0.0,
            **v,
        })
    groups.sort(key=lambda x: x["total"], reverse=True)

    return {
        "ok": True,
        "group_by": group_by,
        "total_amostra": len(rows),
        "groups": groups[:30],
    }


def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if name == "query_processos_base":
        return execute_query_processos(args)
    if name == "query_jurisprudencia":
        return execute_query_jurisprudencia(args)
    if name == "aggregate_processos":
        return execute_aggregate(args)
    return {"error": f"Tool desconhecido: {name}"}


# ═══════════════════════════════════════════════════════
# AGENT LOOP (Claude com tool use)
# ═══════════════════════════════════════════════════════
SYSTEM_PROMPT = (
    "Você é o assistente jurídico do Jurimetrix com acesso a uma base de dados real de processos brasileiros.\n\n"
    "FERRAMENTAS DISPONÍVEIS:\n"
    "1. query_processos_base — busca processos enriquecidos (~284 mil)\n"
    "2. query_jurisprudencia — busca decisões e ementas (~2 milhões)\n"
    "3. aggregate_processos — estatísticas agregadas (taxa de êxito, contagens, padrões)\n\n"
    "REGRAS DE USO:\n"
    "- Quando o usuário pergunta sobre dados, padrões, números, magistrados, varas, percentuais — USE as ferramentas. NUNCA invente números.\n"
    "- Para 'qual juiz é mais favorável a X', use aggregate_processos com group_by='magistrado' e filtro de assunto.\n"
    "- Para 'taxa de êxito em Y vara', use aggregate_processos com group_by='orgao_julgador' filtrando vara.\n"
    "- Para precedentes/jurisprudência, use query_jurisprudencia.\n"
    "- Você pode chamar várias ferramentas em sequência se precisar.\n"
    "- Se uma busca retorna vazia, tente reformular (sinônimos, sem filtro de ano, etc.) antes de desistir.\n\n"
    "QUANDO UMA FERRAMENTA RETORNA ERRO:\n"
    "- NUNCA improvise dados ou liste tribunais/magistrados/varas sem números reais.\n"
    "- Diga claramente ao usuário: 'Houve uma instabilidade técnica ao consultar a base. Erro: <error>'.\n"
    "- Sugira reformular a pergunta ou tentar novamente.\n"
    "- É melhor admitir falha do que apresentar dados inventados.\n\n"
    "RESPOSTA:\n"
    "- Sintetize em português jurídico claro e objetivo.\n"
    "- Cite os números reais retornados pelas ferramentas.\n"
    "- Se a base não tiver dados, diga isso claramente — não invente.\n"
    "- Para conversas casuais (oi, obrigado, tchau), responda direto sem usar ferramentas.\n"
    "- Nunca prometa resultados processuais.\n"
    "- Apresente os dados como informações do próprio Jurimetrix, não cite 'Supabase' ou 'base externa'."
)

MAX_AGENT_ITERATIONS = 6
MAX_TOOL_RESULT_CHARS = 30000  # truncar resultados muito grandes para não estourar contexto


def run_agent(
    user_message: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Executa loop do agente Claude com tool use.

    Args:
        user_message: nova mensagem do usuário
        conversation_history: histórico anterior (lista de {role, content})

    Returns:
        dict com keys: message, tool_calls, tokens, iterations, error
    """
    if not ANTHROPIC_API_KEY:
        return {"message": "ANTHROPIC_API_KEY não configurado", "error": True, "tool_calls": [], "iterations": 0}
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"message": "Supabase não configurado", "error": True, "tool_calls": [], "iterations": 0}

    # Construir conversation
    conversation: List[Dict[str, Any]] = []
    for m in (conversation_history or []):
        if m.get("role") in ("user", "assistant") and m.get("content"):
            conversation.append({"role": m["role"], "content": m["content"]})

    if user_message and user_message.strip():
        conversation.append({"role": "user", "content": user_message.strip()})

    if not conversation or conversation[-1].get("role") != "user":
        return {"message": "Nenhuma mensagem do usuário", "error": True, "tool_calls": [], "iterations": 0}

    tool_calls_log: List[Dict[str, Any]] = []
    total_tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
    final_text = ""
    iterations_done = 0

    for iteration in range(MAX_AGENT_ITERATIONS):
        iterations_done = iteration + 1

        body = {
            "model": ANTHROPIC_MODEL,
            "max_tokens": 4000,
            "system": [{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            "tools": AGENT_TOOLS,
            "messages": conversation,
        }

        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json=body,
                timeout=120,
            )
            r.raise_for_status()
        except requests.HTTPError as e:
            err = (e.response.text if e.response else "")[:400]
            log.error(f"[Agent iter={iteration}] HTTP error: {err}")
            return {"message": f"Erro Claude: {err}", "error": True, "tool_calls": tool_calls_log, "iterations": iterations_done}
        except Exception as e:
            log.error(f"[Agent iter={iteration}] Erro: {e}")
            return {"message": f"Erro: {e}", "error": True, "tool_calls": tool_calls_log, "iterations": iterations_done}

        data = r.json()
        usage = data.get("usage", {}) or {}
        total_tokens["input"] += usage.get("input_tokens", 0)
        total_tokens["output"] += usage.get("output_tokens", 0)
        total_tokens["cache_read"] += usage.get("cache_read_input_tokens", 0)
        total_tokens["cache_create"] += usage.get("cache_creation_input_tokens", 0)

        log.info(
            f"[Agent iter={iteration}] tokens: input={usage.get('input_tokens',0)} "
            f"output={usage.get('output_tokens',0)} cache_read={usage.get('cache_read_input_tokens',0)}"
        )

        stop_reason = data.get("stop_reason", "")
        content_blocks = data.get("content", []) or []

        # Resposta final do Claude
        if stop_reason == "end_turn":
            text_parts = [
                b.get("text", "")
                for b in content_blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            final_text = "\n".join(p for p in text_parts if p).strip()
            break

        # Claude pediu tool use
        if stop_reason == "tool_use":
            # Adiciona resposta do assistant com os tool_use blocks
            conversation.append({"role": "assistant", "content": content_blocks})

            # Executa cada tool_use
            tool_results_content = []
            for block in content_blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tname = block.get("name", "")
                targs = block.get("input", {}) or {}
                tid = block.get("id", "")

                log.info(f"[Agent] Tool call: {tname} args={json.dumps(targs, ensure_ascii=False)[:200]}")
                try:
                    tresult = execute_tool(tname, targs)
                except Exception as e:
                    tresult = {"error": f"Erro ao executar {tname}: {e}"}

                count_log = tresult.get("count")
                if count_log is None:
                    count_log = len(tresult.get("groups", []) or [])

                log.info(
                    f"[Agent] Tool {tname} → ok={tresult.get('ok')} count={count_log} "
                    f"error={(tresult.get('error') or '')[:120]}"
                )

                tool_calls_log.append({
                    "name": tname,
                    "args": targs,
                    "count": count_log,
                    "error": tresult.get("error"),
                })

                # Truncar resultado muito grande para não estourar contexto
                tresult_str = json.dumps(tresult, ensure_ascii=False, default=str)
                if len(tresult_str) > MAX_TOOL_RESULT_CHARS:
                    if "results" in tresult and isinstance(tresult["results"], list):
                        tresult["results"] = tresult["results"][:30]
                        tresult["truncated"] = True
                    if "groups" in tresult and isinstance(tresult["groups"], list):
                        tresult["groups"] = tresult["groups"][:30]
                    tresult_str = json.dumps(tresult, ensure_ascii=False, default=str)[:MAX_TOOL_RESULT_CHARS]

                tool_results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "content": tresult_str,
                })

            # Adiciona tool_results como user message
            conversation.append({"role": "user", "content": tool_results_content})
            continue

        # Outro stop_reason (max_tokens, stop_sequence, refusal, etc.)
        text_parts = [
            b.get("text", "")
            for b in content_blocks
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        final_text = "\n".join(p for p in text_parts if p).strip() or f"Resposta interrompida: {stop_reason}"
        break

    if not final_text:
        final_text = (
            f"Limite de iterações atingido ({MAX_AGENT_ITERATIONS}). "
            "Tente reformular a pergunta de forma mais específica."
        )

    log.info(
        f"[Agent] DONE iters={iterations_done} input={total_tokens['input']} "
        f"output={total_tokens['output']} cache_read={total_tokens['cache_read']} "
        f"tool_calls={len(tool_calls_log)}"
    )

    return {
        "message": final_text,
        "tool_calls": tool_calls_log,
        "tokens": total_tokens,
        "iterations": iterations_done,
        "error": False,
    }
