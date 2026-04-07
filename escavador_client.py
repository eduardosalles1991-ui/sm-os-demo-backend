"""
escavador_client.py — API Escavador
═══════════════════════════════════════════════════
Busca de pessoas, empresas, advogados e processos.
Variável de ambiente: ESCAVADOR_API_KEY
═══════════════════════════════════════════════════
"""
import os
import logging
import requests
from typing import Any, Dict, List, Optional

log = logging.getLogger("escavador_client")

ESCAVADOR_API_KEY = (os.getenv("ESCAVADOR_API_KEY") or "").strip()
BASE_URL = "https://api.escavador.com/v1"


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
    r = requests.get(url, headers=_headers(), params=params, timeout=25)
    r.raise_for_status()
    return r.json()


def _post(endpoint: str, payload: dict = None) -> dict:
    if not ESCAVADOR_API_KEY:
        raise RuntimeError("ESCAVADOR_API_KEY não configurado.")
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    r = requests.post(url, headers=_headers(), json=payload or {}, timeout=25)
    r.raise_for_status()
    return r.json()


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
        """Busca advogado por nome ou OAB."""
        params = {}
        if oab:
            params["oab"] = oab
        elif nome:
            params["q"] = nome
        else:
            return {"error": "Nome ou OAB necessário"}
        return _get("advogados", params)

    def processos_por_envolvido(self, nome: str = None, cpf_cnpj: str = None) -> dict:
        """Lista processos de uma pessoa/empresa."""
        params = {}
        if cpf_cnpj:
            doc = cpf_cnpj.replace(".", "").replace("-", "").replace("/", "")
            params["cpf_cnpj"] = doc
        elif nome:
            params["q"] = nome
        else:
            return {"error": "Nome ou CPF/CNPJ necessário"}
        return _get("processos", params)

    def detalhes_processo(self, processo_id: int) -> dict:
        """Retorna detalhes de um processo pelo ID do Escavador."""
        return _get(f"processos/{processo_id}")

    def build_context(self, data: dict, tipo: str) -> str:
        """Constrói contexto textual para o GPT a partir dos dados do Escavador."""
        if not data:
            return "Nenhum dado retornado pelo Escavador."

        if data.get("error"):
            return f"Erro Escavador: {data['error']}"

        items = data.get("items") or data.get("data") or data.get("results") or []
        if isinstance(data, dict) and not items:
            # Single result
            items = [data]

        if not items:
            return f"Escavador: nenhum resultado encontrado para busca tipo '{tipo}'."

        lines = [f"DADOS DO ESCAVADOR — Tipo: {tipo.upper()} | {len(items)} resultado(s)", ""]

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
                oab = a.get("oab") or a.get("inscricao") or "n/d"
                lines.append(f"── {nome}")
                lines.append(f"   OAB: {oab}")
                escritorio = a.get("escritorio") or a.get("office") or ""
                if escritorio:
                    lines.append(f"   Escritório: {escritorio}")
                processos = a.get("processos") or a.get("lawsuits") or []
                if processos:
                    lines.append(f"   Processos: {len(processos)}")
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
