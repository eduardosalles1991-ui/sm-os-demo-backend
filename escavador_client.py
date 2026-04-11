"""
escavador_client.py — API Escavador
═══════════════════════════════════════════════════
Busca de pessoas, empresas, advogados e processos.
Variável de ambiente: ESCAVADOR_API_KEY
═══════════════════════════════════════════════════
"""
import os
import re
import logging
import requests
from typing import Any, Dict, List, Optional

log = logging.getLogger("escavador_client")

ESCAVADOR_API_KEY = (os.getenv("ESCAVADOR_API_KEY") or "").strip()
BASE_URL = "https://api.escavador.com/api/v1"
BASE_URL_V2 = "https://api.escavador.com/api/v2"


def is_configured() -> bool:
    return bool(ESCAVADOR_API_KEY)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {ESCAVADOR_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _get(endpoint: str, params: dict = None) -> dict:
    if not ESCAVADOR_API_KEY:
        raise RuntimeError("ESCAVADOR_API_KEY não configurado.")
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    log.info(f"[Escavador] GET {url} params={params}")
    r = requests.get(url, headers=_headers(), params=params, timeout=25)
    r.raise_for_status()
    return r.json()


def _post(endpoint: str, payload: dict = None) -> dict:
    if not ESCAVADOR_API_KEY:
        raise RuntimeError("ESCAVADOR_API_KEY não configurado.")
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    log.info(f"[Escavador] POST {url}")
    r = requests.post(url, headers=_headers(), json=payload or {}, timeout=25)
    r.raise_for_status()
    return r.json()


def _get_v2(endpoint: str, params: dict = None) -> dict:
    if not ESCAVADOR_API_KEY:
        raise RuntimeError("ESCAVADOR_API_KEY não configurado.")
    url = f"{BASE_URL_V2}/{endpoint.lstrip('/')}"
    log.info(f"[Escavador] GET V2 {url} params={params}")
    r = requests.get(url, headers=_headers(), params=params, timeout=25)
    r.raise_for_status()
    return r.json()


def parse_oab(oab_raw: str) -> dict:
    """
    Parseia string de OAB em estado e número.
    Aceita: 'OAB/SP 105.488', 'OAB SP 105488', 'SP 105.488', 'OAB/SP/105488'
    Retorna: {"estado": "SP", "numero": "105488"}
    """
    if not oab_raw:
        return {}
    s = oab_raw.strip().upper()
    s = re.sub(r'^OAB[/\s]*', '', s)
    m = re.match(r'([A-Z]{2})[/\s.,\-]*(\d[\d.]*)', s)
    if m:
        estado = m.group(1)
        numero = m.group(2).replace(".", "")
        return {"estado": estado, "numero": numero}
    m2 = re.search(r'(\d[\d.]+)', s)
    if m2:
        return {"numero": m2.group(1).replace(".", "")}
    return {}


class EscavadorClient:

    def buscar_pessoa(self, nome: str = None, cpf: str = None) -> dict:
        """Busca pessoa por nome ou CPF."""
        params = {}
        if cpf:
            params["cpf"] = cpf.replace(".", "").replace("-", "")
        elif nome:
            params["q"] = nome
        else:
            return {"error": "Nome ou CPF necessário"}
        return _get("pessoas", params)

    def buscar_empresa(self, nome: str = None, cnpj: str = None) -> dict:
        """Busca empresa por nome ou CNPJ."""
        params = {}
        if cnpj:
            params["cnpj"] = cnpj.replace(".", "").replace("-", "").replace("/", "")
        elif nome:
            params["q"] = nome
        else:
            return {"error": "Nome ou CNPJ necessário"}
        return _get("empresas", params)

    def buscar_advogado(self, nome: str = None, oab: str = None) -> dict:
        """
        Busca advogado por nome ou OAB.
        OAB aceita formatos: 'OAB/SP 105.488', 'SP 105488', etc.
        Usa API V2 para busca por OAB: /api/v2/advogado/processos?oab_numero=X&oab_estado=Y
        """
        if oab:
            parsed = parse_oab(oab)
            if parsed.get("estado") and parsed.get("numero"):
                # V2: busca processos do advogado por OAB
                try:
                    result = _get_v2("advogado/processos", {
                        "oab_numero": parsed["numero"],
                        "oab_estado": parsed["estado"],
                    })
                    return result
                except requests.HTTPError as e:
                    log.warning(f"[Escavador] V2 advogado OAB {parsed['estado']}/{parsed['numero']} falhou: {e}")
                # Fallback V1: busca por nome
                try:
                    return _get("pessoas", {"q": f"OAB {parsed['estado']} {parsed['numero']}"})
                except requests.HTTPError:
                    pass
            elif parsed.get("numero"):
                try:
                    return _get("pessoas", {"q": f"OAB {parsed['numero']}"})
                except requests.HTTPError:
                    pass
            return {"error": f"OAB não encontrada: {oab}"}
        elif nome:
            return _get("pessoas", {"q": nome})
        else:
            return {"error": "Nome ou OAB necessário"}

    def processos_por_envolvido(self, nome: str = None, cpf_cnpj: str = None) -> dict:
        """Lista processos de uma pessoa/empresa."""
        if cpf_cnpj:
            doc = cpf_cnpj.replace(".", "").replace("-", "").replace("/", "")
            # V2 primeiro
            try:
                return _get_v2("envolvido/processos", {"cpf_cnpj": doc})
            except requests.HTTPError:
                pass
            return _get("processos", {"cpf_cnpj": doc})
        elif nome:
            try:
                return _get_v2("envolvido/processos", {"nome": nome})
            except requests.HTTPError:
                pass
            return _get("processos", {"q": nome})
        else:
            return {"error": "Nome ou CPF/CNPJ necessário"}

    def detalhes_processo(self, processo_id: int) -> dict:
        """Retorna detalhes de um processo pelo ID do Escavador."""
        return _get(f"processos/{processo_id}")

    def build_context(self, data: dict, tipo: str) -> str:
        """Constrói contexto textual para o GPT a partir dos dados do Escavador."""
        if not data:
            return "Nenhum dado retornado pelo Escavador."

        if data.get("error"):
            return f"Erro Escavador: {data['error']}"

        # V2 format: {"envolvido": {...}, "items": [...]} or {"advogado_encontrado": {...}, ...}
        envolvido = data.get("envolvido") or data.get("envolvido_encontrado") or data.get("advogado_encontrado") or data.get("advogado") or {}
        items = data.get("items") or data.get("data") or data.get("results") or data.get("processos") or []
        if isinstance(data, dict) and not items:
            if data.get("nome") or data.get("name") or data.get("numero") or data.get("razao_social"):
                items = [data]

        lines = []

        # Se tem envolvido (V2), mostrar info do envolvido primeiro
        if envolvido:
            nome = envolvido.get("nome") or envolvido.get("name") or "n/d"
            tipo_env = envolvido.get("tipo") or tipo
            lines.append(f"DADOS DO ESCAVADOR — {tipo_env.upper()}: {nome}")
            if envolvido.get("oab_numero"):
                lines.append(f"OAB: {envolvido.get('oab_estado','')}/{envolvido.get('oab_numero','')}")
            if envolvido.get("cpf"):
                lines.append(f"CPF: {envolvido['cpf']}")
            if envolvido.get("cnpj"):
                lines.append(f"CNPJ: {envolvido['cnpj']}")
            lines.append("")
            if items:
                lines.append(f"PROCESSOS ENCONTRADOS: {len(items)}")
                lines.append("")
                for proc in items[:12]:
                    num = proc.get("numero_cnj") or proc.get("numero") or proc.get("number") or "n/d"
                    titulo_polo = proc.get("titulo_polo_ativo") or ""
                    titulo_passivo = proc.get("titulo_polo_passivo") or ""
                    tribunal = proc.get("fonte") or proc.get("tribunal") or proc.get("court") or ""
                    if isinstance(tribunal, dict):
                        tribunal = tribunal.get("nome") or tribunal.get("sigla") or ""
                    fontes = proc.get("fontes") or []
                    if fontes and isinstance(fontes[0], dict):
                        tribunal = fontes[0].get("nome") or fontes[0].get("sigla") or tribunal
                    data_inicio = proc.get("data_inicio") or ""
                    data_ult = proc.get("data_ultima_movimentacao") or ""
                    lines.append(f"── {num} | {tribunal}")
                    if titulo_polo or titulo_passivo:
                        lines.append(f"   {titulo_polo} x {titulo_passivo}")
                    if data_inicio:
                        lines.append(f"   Início: {data_inicio} | Última mov.: {data_ult}")
                    lines.append("")
            return "\n".join(lines)

        if not items:
            return f"Escavador: nenhum resultado encontrado para busca tipo '{tipo}'."

        lines.append(f"DADOS DO ESCAVADOR — Tipo: {tipo.upper()} | {len(items)} resultado(s)")
        lines.append("")

        if tipo == "pessoa":
            for p in items[:5]:
                nome = p.get("nome") or p.get("name") or "n/d"
                cpf = p.get("cpf") or "n/d"
                lines.append(f"── {nome}")
                if cpf != "n/d":
                    lines.append(f"   CPF: {cpf}")
                if p.get("data_nascimento"):
                    lines.append(f"   Nascimento: {p['data_nascimento']}")
                processos = p.get("processos") or p.get("lawsuits") or []
                if processos:
                    lines.append(f"   Processos: {len(processos)}")
                    for proc in processos[:5]:
                        num = proc.get("numero") or proc.get("number") or "n/d"
                        lines.append(f"     • {num}")
                lines.append("")

        elif tipo == "empresa":
            for e in items[:5]:
                nome = e.get("razao_social") or e.get("nome") or e.get("name") or "n/d"
                cnpj = e.get("cnpj") or "n/d"
                situacao = e.get("situacao_cadastral") or e.get("status") or "n/d"
                lines.append(f"── {nome}")
                lines.append(f"   CNPJ: {cnpj} | Situação: {situacao}")
                socios = e.get("socios") or e.get("partners") or []
                if socios:
                    lines.append(f"   Sócios: {', '.join(s.get('nome', s.get('name', '')) for s in socios[:5])}")
                lines.append("")

        elif tipo == "advogado":
            for a in items[:5]:
                nome = a.get("nome") or a.get("name") or "n/d"
                oab_val = a.get("oab") or a.get("inscricao") or a.get("numero_oab") or "n/d"
                estado_oab = a.get("estado_oab") or a.get("uf") or ""
                lines.append(f"── {nome}")
                lines.append(f"   OAB: {estado_oab} {oab_val}".strip())
                escritorio = a.get("escritorio") or a.get("office") or ""
                if escritorio:
                    lines.append(f"   Escritório: {escritorio}")
                processos = a.get("processos") or a.get("lawsuits") or []
                if processos:
                    lines.append(f"   Processos: {len(processos)}")
                    for proc in processos[:5]:
                        num = proc.get("numero") or proc.get("number") or "n/d"
                        lines.append(f"     • {num}")
                lines.append("")

        elif tipo == "processos":
            for proc in items[:10]:
                num = proc.get("numero") or proc.get("number") or "n/d"
                tribunal = proc.get("tribunal") or proc.get("court") or "n/d"
                classe = proc.get("classe") or proc.get("type") or "n/d"
                status = proc.get("status") or proc.get("situacao") or "n/d"
                lines.append(f"── {num} | {tribunal}")
                lines.append(f"   Classe: {classe} | Status: {status}")
                partes = proc.get("partes") or proc.get("parties") or []
                if partes:
                    nomes_partes = [p.get("nome", p.get("name", "")) for p in partes[:4]]
                    lines.append(f"   Partes: {', '.join(nomes_partes)}")
                lines.append("")

        else:
            lines.append(str(data)[:2000])

        return "\n".join(lines)


ESCAVADOR = EscavadorClient()
