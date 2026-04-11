"""
bacen_client.py — Integração com API BACEN SGS
Índices trabalhistas: SELIC, INPC, IPCA, TR, TJLP
API pública, sem autenticação: https://api.bcb.gov.br
"""
import requests
import logging
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("smos")

# ── Códigos das séries BACEN SGS ────────────────────────────────────
SERIES = {
    "selic":      11,    # Taxa SELIC diária
    "selic_meta": 432,   # Meta SELIC
    "inpc":       188,   # INPC mensal
    "ipca":       433,   # IPCA mensal
    "tr":         226,   # TR mensal
    "tjlp":       256,   # TJLP trimestral
    "inpc_acum":  253,   # INPC acumulado 12 meses
    "ipca_acum":  13522, # IPCA acumulado 12 meses
    "cdi":        4389,  # CDI diário
    "igpm":       189,   # IGP-M mensal
}

BACEN_BASE = "https://api.bcb.gov.br/dados/serie/bcdata.sgs"
TIMEOUT = 20

# Headers necessários — BACEN bloqueia requests sem User-Agent
HEADERS = {
    "User-Agent": "Jurimetrix/1.0 (legal-analytics)",
    "Accept": "application/json",
}


class BacenClient:
    def _get(self, serie: int, start: str = None, end: str = None, ultimos: int = None):
        """Busca dados de uma série no BACEN SGS."""
        try:
            if ultimos:
                url = f"{BACEN_BASE}.{serie}/dados/ultimos/{ultimos}?formato=json"
            else:
                url = f"{BACEN_BASE}.{serie}/dados?formato=json"
                if start: url += f"&dataInicial={start}"
                if end:   url += f"&dataFinal={end}"

            log.info(f"[BACEN] GET {url}")
            r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
            log.info(f"[BACEN] Status: {r.status_code} | Size: {len(r.text)} bytes")
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data:
                return data
            log.warning(f"[BACEN] série {serie}: resposta vazia ou inválida")
            return []
        except requests.Timeout:
            log.warning(f"[BACEN] série {serie}: timeout ({TIMEOUT}s)")
            return []
        except requests.ConnectionError as e:
            log.warning(f"[BACEN] série {serie}: erro de conexão: {e}")
            return []
        except requests.HTTPError as e:
            log.warning(f"[BACEN] série {serie}: HTTP {r.status_code}")
            return []
        except Exception as e:
            log.warning(f"[BACEN] série {serie}: erro inesperado: {e}")
            return []

    def ultima_selic(self) -> dict:
        """Retorna a taxa SELIC meta atual."""
        dados = self._get(SERIES["selic_meta"], ultimos=1)
        if dados:
            return {"taxa": float(dados[-1]["valor"]), "data": dados[-1]["data"]}
        return {}

    def ultimo_inpc(self) -> dict:
        """Retorna o INPC do último mês disponível."""
        dados = self._get(SERIES["inpc"], ultimos=1)
        if dados:
            return {"taxa": float(dados[-1]["valor"]), "data": dados[-1]["data"]}
        return {}

    def ultimo_ipca(self) -> dict:
        """Retorna o IPCA do último mês disponível."""
        dados = self._get(SERIES["ipca"], ultimos=1)
        if dados:
            return {"taxa": float(dados[-1]["valor"]), "data": dados[-1]["data"]}
        return {}

    def ultima_tr(self) -> dict:
        """Retorna a TR do último mês disponível."""
        dados = self._get(SERIES["tr"], ultimos=1)
        if dados:
            return {"taxa": float(dados[-1]["valor"]), "data": dados[-1]["data"]}
        return {}

    def inpc_acumulado_12m(self) -> dict:
        """Retorna o INPC acumulado 12 meses."""
        dados = self._get(SERIES["inpc_acum"], ultimos=1)
        if dados:
            return {"taxa": float(dados[-1]["valor"]), "data": dados[-1]["data"]}
        return {}

    def ipca_acumulado_12m(self) -> dict:
        """Retorna o IPCA acumulado 12 meses."""
        dados = self._get(SERIES["ipca_acum"], ultimos=1)
        if dados:
            return {"taxa": float(dados[-1]["valor"]), "data": dados[-1]["data"]}
        return {}

    def indices_atuais(self) -> dict:
        """Retorna todos os índices atuais de uma vez."""
        result = {
            "selic_meta":   self.ultima_selic(),
            "inpc":         self.ultimo_inpc(),
            "ipca":         self.ultimo_ipca(),
            "tr":           self.ultima_tr(),
            "inpc_acum_12m": self.inpc_acumulado_12m(),
            "ipca_acum_12m": self.ipca_acumulado_12m(),
        }
        # Log summary
        encontrados = sum(1 for v in result.values() if v)
        log.info(f"[BACEN] indices_atuais: {encontrados}/{len(result)} índices obtidos")
        return result

    def inpc_periodo(self, start: str, end: str) -> list:
        """Retorna INPC de um período (dd/mm/aaaa)."""
        return self._get(SERIES["inpc"], start=start, end=end)

    def ipca_periodo(self, start: str, end: str) -> list:
        """Retorna IPCA de um período (dd/mm/aaaa)."""
        return self._get(SERIES["ipca"], start=start, end=end)

    def calcular_correcao_inpc(self, valor: float, data_inicio: str, data_fim: str = None) -> dict:
        """
        Calcula correção monetária pelo INPC.
        data_inicio/data_fim: formato dd/mm/aaaa
        """
        if not data_fim:
            data_fim = datetime.now().strftime("%d/%m/%Y")

        dados = self.inpc_periodo(data_inicio, data_fim)
        if not dados:
            return {"erro": f"Não foi possível obter dados do INPC para o período {data_inicio} a {data_fim}. A API do Banco Central pode estar indisponível."}

        fator = 1.0
        for d in dados:
            taxa = float(d["valor"]) / 100
            fator *= (1 + taxa)

        valor_corrigido = valor * fator
        return {
            "valor_original": valor,
            "valor_corrigido": round(valor_corrigido, 2),
            "fator": round(fator, 6),
            "variacao_pct": round((fator - 1) * 100, 2),
            "periodo": f"{data_inicio} a {data_fim}",
            "indice": "INPC",
            "meses": len(dados),
        }

    def calcular_juros_mora(self, valor: float, meses: int, taxa_mensal: float = 1.0) -> dict:
        """
        Calcula juros moratórios simples (padrão trabalhista: 1% a.m.).
        CLT art. 883 — juros de mora de 1% ao mês.
        """
        juros = valor * (taxa_mensal / 100) * meses
        return {
            "valor_original": valor,
            "juros": round(juros, 2),
            "valor_total": round(valor + juros, 2),
            "taxa_mensal": taxa_mensal,
            "meses": meses,
            "base_legal": "CLT art. 883 — 1% a.m.",
        }

    def build_context(self, dados: dict) -> str:
        """Formata dados do BACEN para contexto da IA."""
        lines = ["\n═══ ÍNDICES ECONÔMICOS (BACEN SGS) ═══"]
        tem_dados = False

        if dados.get("selic_meta") and dados["selic_meta"].get("taxa") is not None:
            s = dados["selic_meta"]
            lines.append(f"SELIC Meta: {s['taxa']}% a.a. (ref: {s.get('data','?')})")
            tem_dados = True

        if dados.get("inpc") and dados["inpc"].get("taxa") is not None:
            n = dados["inpc"]
            lines.append(f"INPC (último mês): {n['taxa']}% (ref: {n.get('data','?')})")
            tem_dados = True

        if dados.get("ipca") and dados["ipca"].get("taxa") is not None:
            p = dados["ipca"]
            lines.append(f"IPCA (último mês): {p['taxa']}% (ref: {p.get('data','?')})")
            tem_dados = True

        if dados.get("tr") and dados["tr"].get("taxa") is not None:
            t = dados["tr"]
            lines.append(f"TR (último mês): {t['taxa']}% (ref: {t.get('data','?')})")
            tem_dados = True

        if dados.get("inpc_acum_12m") and dados["inpc_acum_12m"].get("taxa") is not None:
            a = dados["inpc_acum_12m"]
            lines.append(f"INPC acumulado 12 meses: {a['taxa']}% (ref: {a.get('data','?')})")
            tem_dados = True

        if dados.get("ipca_acum_12m") and dados["ipca_acum_12m"].get("taxa") is not None:
            a2 = dados["ipca_acum_12m"]
            lines.append(f"IPCA acumulado 12 meses: {a2['taxa']}% (ref: {a2.get('data','?')})")
            tem_dados = True

        if not tem_dados:
            lines.append("⚠️ API do Banco Central temporariamente indisponível. Índices não puderam ser obtidos em tempo real.")

        if dados.get("correcao"):
            c = dados["correcao"]
            if c.get("erro"):
                lines.append(f"\nCORREÇÃO MONETÁRIA: {c['erro']}")
            else:
                lines.append(f"\nCORREÇÃO MONETÁRIA ({c.get('indice','INPC')}):")
                lines.append(f"  Valor original: R$ {c.get('valor_original',0):,.2f}")
                lines.append(f"  Valor corrigido: R$ {c.get('valor_corrigido',0):,.2f}")
                lines.append(f"  Variação: {c.get('variacao_pct','?')}%")
                lines.append(f"  Fator acumulado: {c.get('fator','?')}")
                lines.append(f"  Período: {c.get('periodo','?')} ({c.get('meses','?')} meses)")

        if dados.get("juros"):
            j = dados["juros"]
            lines.append(f"\nJUROS MORATÓRIOS ({j.get('base_legal','CLT art. 883')}):")
            lines.append(f"  Valor original: R$ {j.get('valor_original',0):,.2f}")
            lines.append(f"  Juros ({j.get('taxa_mensal','1')}% x {j.get('meses','?')} meses): R$ {j.get('juros',0):,.2f}")
            lines.append(f"  Total: R$ {j.get('valor_total',0):,.2f}")

        lines.append("═══════════════════════════════════════")
        return "\n".join(lines)


BACEN = BacenClient()

def is_configured():
    """BACEN é sempre disponível — API pública sem chave."""
    return True
