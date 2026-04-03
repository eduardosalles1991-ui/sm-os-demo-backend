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
TIMEOUT = 15

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
            
            r = requests.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.warning(f"[BACEN] série {serie}: {e}")
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

    def indices_atuais(self) -> dict:
        """Retorna todos os índices atuais de uma vez."""
        return {
            "selic_meta": self.ultima_selic(),
            "inpc":       self.ultimo_inpc(),
            "ipca":       self.ultimo_ipca(),
            "tr":         self.ultima_tr(),
        }

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
            return {"erro": "Não foi possível obter dados do INPC para o período."}
        
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
        
        if dados.get("selic_meta"):
            s = dados["selic_meta"]
            lines.append(f"SELIC Meta: {s.get('taxa','?')}% a.a. (ref: {s.get('data','?')})")
        
        if dados.get("inpc"):
            n = dados["inpc"]
            lines.append(f"INPC (último mês): {n.get('taxa','?')}% (ref: {n.get('data','?')})")
        
        if dados.get("ipca"):
            p = dados["ipca"]
            lines.append(f"IPCA (último mês): {p.get('taxa','?')}% (ref: {p.get('data','?')})")
        
        if dados.get("tr"):
            t = dados["tr"]
            lines.append(f"TR (último mês): {t.get('taxa','?')}% (ref: {t.get('data','?')})")
        
        if dados.get("correcao"):
            c = dados["correcao"]
            lines.append(f"\nCORREÇÃO MONETÁRIA ({c.get('indice','INPC')}):")
            lines.append(f"  Valor original: R$ {c.get('valor_original','?'):.2f}")
            lines.append(f"  Valor corrigido: R$ {c.get('valor_corrigido','?'):.2f}")
            lines.append(f"  Variação: {c.get('variacao_pct','?')}%")
            lines.append(f"  Período: {c.get('periodo','?')} ({c.get('meses','?')} meses)")
        
        if dados.get("juros"):
            j = dados["juros"]
            lines.append(f"\nJUROS MORATÓRIOS ({j.get('base_legal','CLT art. 883')}):")
            lines.append(f"  Valor original: R$ {j.get('valor_original','?'):.2f}")
            lines.append(f"  Juros ({j.get('taxa_mensal','1')}% x {j.get('meses','?')} meses): R$ {j.get('juros','?'):.2f}")
            lines.append(f"  Total: R$ {j.get('valor_total','?'):.2f}")
        
        lines.append("═══════════════════════════════════════")
        return "\n".join(lines)


BACEN = BacenClient()

def is_configured():
    """BACEN é sempre disponível — API pública sem chave."""
    return True
