"""
nl_client.py — Google Cloud Natural Language API
═══════════════════════════════════════════════════
Analisa decisões judiciais: classificação de sentimento,
extração de entidades, e categorização automática.
Usa as mesmas credenciais do Google Vision (GOOGLE_VISION_CREDENTIALS).
═══════════════════════════════════════════════════
"""
import os
import json
import time
import logging
import requests
from typing import Any, Dict, List, Optional

log = logging.getLogger("nl_client")

# ── Credenciais (reusa GOOGLE_VISION_CREDENTIALS) ────────────────────
_CREDS_JSON = os.getenv("GOOGLE_VISION_CREDENTIALS", "")
_token_cache = {"token": None, "expires": 0}


def is_configured() -> bool:
    """Verifica se as credenciais do Google Cloud estão disponíveis."""
    return bool(_CREDS_JSON)


def _get_access_token() -> str:
    """Obtém access token OAuth2 (com cache)."""
    now = int(time.time())
    if _token_cache["token"] and _token_cache["expires"] > now + 60:
        return _token_cache["token"]

    try:
        import jwt as _jwt
    except ImportError:
        import subprocess
        subprocess.check_call(["pip", "install", "PyJWT", "cryptography", "--break-system-packages", "-q"])
        import jwt as _jwt

    creds = json.loads(_CREDS_JSON)
    payload = {
        "iss": creds["client_email"],
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }
    signed = _jwt.encode(payload, creds["private_key"], algorithm="RS256")
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": signed,
        },
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires"] = now + data.get("expires_in", 3600)
    return _token_cache["token"]


# ═══════════════════════════════════════════════════════
# NATURAL LANGUAGE API — FUNÇÕES PRINCIPAIS
# ═══════════════════════════════════════════════════════

NL_API_URL = "https://language.googleapis.com/v1/documents"


def _nl_request(endpoint: str, document: dict, features: dict = None) -> dict:
    """Faz request genérico para a NL API."""
    token = _get_access_token()
    url = f"{NL_API_URL}:{endpoint}"
    body = {"document": document}
    if features:
        body["features"] = features
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def analyze_sentiment(text: str) -> dict:
    """
    Analisa sentimento do texto.
    Retorna score (-1.0 a 1.0) e magnitude (intensidade).
    
    Para decisões judiciais:
    - Score positivo alto → linguagem favorável ao reclamante (procedente)
    - Score negativo → linguagem desfavorável (improcedente)
    - Magnitude alta → decisão enfática
    """
    doc = {
        "type": "PLAIN_TEXT",
        "language": "pt-BR",
        "content": text[:5000],  # limite para economizar
    }
    result = _nl_request("analyzeSentiment", doc)
    sentiment = result.get("documentSentiment", {})
    sentences = result.get("sentences", [])

    return {
        "score": sentiment.get("score", 0),
        "magnitude": sentiment.get("magnitude", 0),
        "sentences_count": len(sentences),
        "top_sentences": [
            {
                "text": s.get("text", {}).get("content", "")[:200],
                "score": s.get("sentiment", {}).get("score", 0),
                "magnitude": s.get("sentiment", {}).get("magnitude", 0),
            }
            for s in sorted(
                sentences,
                key=lambda x: abs(x.get("sentiment", {}).get("score", 0)),
                reverse=True,
            )[:5]
        ],
    }


def analyze_entities(text: str) -> dict:
    """
    Extrai entidades do texto: pessoas, organizações, valores, datas, leis.
    Útil para extrair partes, juízes, valores de condenação, artigos citados.
    """
    doc = {
        "type": "PLAIN_TEXT",
        "language": "pt-BR",
        "content": text[:5000],
    }
    result = _nl_request("analyzeEntities", doc)
    entities = result.get("entities", [])

    # Agrupar por tipo
    grouped = {}
    for e in entities:
        tipo = e.get("type", "UNKNOWN")
        nome = e.get("name", "")
        salience = e.get("salience", 0)
        if tipo not in grouped:
            grouped[tipo] = []
        grouped[tipo].append({
            "name": nome,
            "salience": round(salience, 3),
            "mentions": len(e.get("mentions", [])),
        })

    # Ordenar cada grupo por salience
    for tipo in grouped:
        grouped[tipo] = sorted(grouped[tipo], key=lambda x: x["salience"], reverse=True)[:10]

    return {
        "total_entities": len(entities),
        "by_type": grouped,
        "pessoas": [e["name"] for e in grouped.get("PERSON", [])],
        "organizacoes": [e["name"] for e in grouped.get("ORGANIZATION", [])],
        "valores": [e["name"] for e in grouped.get("PRICE", []) + grouped.get("NUMBER", [])],
        "datas": [e["name"] for e in grouped.get("DATE", [])],
    }


def classify_text(text: str) -> dict:
    """
    Classifica o texto em categorias (requer mín. 20 palavras).
    Útil para categorizar tipo de ação/decisão automaticamente.
    """
    if len(text.split()) < 20:
        return {"categories": [], "error": "Texto muito curto para classificação"}

    doc = {
        "type": "PLAIN_TEXT",
        "language": "pt-BR",
        "content": text[:5000],
    }
    try:
        result = _nl_request("classifyText", doc)
        categories = result.get("categories", [])
        return {
            "categories": [
                {
                    "name": c.get("name", ""),
                    "confidence": round(c.get("confidence", 0), 3),
                }
                for c in categories
            ]
        }
    except Exception as e:
        return {"categories": [], "error": str(e)}


def analyze_syntax(text: str) -> dict:
    """
    Análise sintática — identifica estrutura gramatical.
    Útil para extrair termos jurídicos e verbos-chave (condenar, julgar, deferir, indeferir).
    """
    doc = {
        "type": "PLAIN_TEXT",
        "language": "pt-BR",
        "content": text[:3000],
    }
    result = _nl_request("analyzeSyntax", doc)
    tokens = result.get("tokens", [])

    # Extrair verbos-chave jurídicos
    verbos_juridicos = [
        "condenar", "julgar", "deferir", "indeferir", "procedente",
        "improcedente", "absolver", "reconhecer", "negar", "prover",
        "reformar", "manter", "anular", "homologar", "extinguir",
        "arquivar", "intimar", "determinar", "fixar", "arbitrar",
    ]
    verbos_encontrados = []
    for t in tokens:
        lemma = (t.get("lemma") or "").lower()
        if lemma in verbos_juridicos:
            verbos_encontrados.append({
                "verbo": lemma,
                "forma": t.get("text", {}).get("content", ""),
            })

    return {
        "total_tokens": len(tokens),
        "verbos_juridicos": verbos_encontrados,
    }


# ═══════════════════════════════════════════════════════
# ANÁLISE COMPLETA DE DECISÃO JUDICIAL
# ═══════════════════════════════════════════════════════

# Palavras-chave para classificação de resultado
KW_PROCEDENTE = [
    "procedente", "procedentes", "julgo procedente",
    "condeno", "condenar", "condenação",
    "dou provimento", "provido", "reformar",
    "deferido", "defiro", "acolho",
]
KW_IMPROCEDENTE = [
    "improcedente", "improcedentes", "julgo improcedente",
    "indefiro", "indeferido", "nego provimento",
    "não provido", "improvido", "rejeito",
    "absolvo", "extingo sem resolução",
]
KW_PARCIAL = [
    "parcialmente procedente", "em parte procedente",
    "procedente em parte", "parcial procedência",
    "condeno parcialmente", "dou parcial provimento",
]
KW_ACORDO = [
    "acordo", "homologação", "homologo",
    "conciliação", "transação", "composição amigável",
]


def classificar_resultado_decisao(texto: str) -> str:
    """
    Classifica o resultado de uma decisão judicial baseado em palavras-chave.
    Retorna: 'procedente', 'improcedente', 'parcialmente_procedente', 'acordo', 'indeterminado'
    """
    t = texto.lower()
    # Checar acordo primeiro (pode ter "procedente" junto)
    if any(kw in t for kw in KW_ACORDO):
        return "acordo"
    # Parcial antes de procedente (senão "parcialmente procedente" vira "procedente")
    if any(kw in t for kw in KW_PARCIAL):
        return "parcialmente_procedente"
    if any(kw in t for kw in KW_PROCEDENTE):
        # Verificar se não é "improcedente" disfarçado
        if any(kw in t for kw in KW_IMPROCEDENTE):
            # Ambos presentes — checar qual aparece mais
            proc_count = sum(1 for kw in KW_PROCEDENTE if kw in t)
            imp_count = sum(1 for kw in KW_IMPROCEDENTE if kw in t)
            return "procedente" if proc_count > imp_count else "improcedente"
        return "procedente"
    if any(kw in t for kw in KW_IMPROCEDENTE):
        return "improcedente"
    return "indeterminado"


def extrair_valor_condenacao(texto: str) -> Optional[float]:
    """Tenta extrair valor de condenação do texto da decisão."""
    import re
    # Padrões: R$ 10.000,00 ou R$10000 ou 10.000,00
    patterns = [
        r'conden\w+\s+(?:ao?\s+)?(?:pagamento\s+)?(?:de\s+)?R?\$?\s*([\d.,]+)',
        r'fix\w+\s+(?:em\s+)?R?\$?\s*([\d.,]+)',
        r'arbitr\w+\s+(?:em\s+)?R?\$?\s*([\d.,]+)',
        r'R\$\s*([\d]+(?:\.[\d]{3})*(?:,[\d]{2}))',
    ]
    for pat in patterns:
        m = re.search(pat, texto, re.IGNORECASE)
        if m:
            val_str = m.group(1).replace(".", "").replace(",", ".")
            try:
                val = float(val_str)
                if val > 100:  # filtrar valores muito baixos (provavelmente não são condenação)
                    return val
            except ValueError:
                continue
    return None


def analisar_decisao(texto: str) -> dict:
    """
    Análise completa de uma decisão judicial usando NL API + heurísticas.
    
    Retorna:
    - resultado: procedente/improcedente/parcial/acordo/indeterminado
    - sentimento: score e magnitude
    - entidades: pessoas, organizações, valores
    - valor_condenacao: valor extraído (se houver)
    - verbos_juridicos: ações jurídicas identificadas
    - confianca: nível de confiança da classificação
    """
    resultado = classificar_resultado_decisao(texto)

    # NL API — sentimento
    try:
        sentimento = analyze_sentiment(texto)
    except Exception as e:
        log.warning(f"NL sentiment error: {e}")
        sentimento = {"score": 0, "magnitude": 0}

    # NL API — entidades
    try:
        entidades = analyze_entities(texto)
    except Exception as e:
        log.warning(f"NL entities error: {e}")
        entidades = {"pessoas": [], "organizacoes": [], "valores": []}

    # Valor de condenação
    valor = extrair_valor_condenacao(texto)

    # Confiança da classificação
    confianca = "alta" if resultado != "indeterminado" else "baixa"
    if resultado != "indeterminado" and abs(sentimento.get("score", 0)) > 0.3:
        confianca = "alta"
    elif resultado != "indeterminado":
        confianca = "media"

    return {
        "resultado": resultado,
        "confianca": confianca,
        "sentimento": {
            "score": sentimento.get("score", 0),
            "magnitude": sentimento.get("magnitude", 0),
        },
        "entidades": {
            "pessoas": entidades.get("pessoas", [])[:8],
            "organizacoes": entidades.get("organizacoes", [])[:5],
            "valores_mencionados": entidades.get("valores", [])[:5],
        },
        "valor_condenacao": valor,
    }


def analisar_lote_decisoes(decisoes: List[dict]) -> dict:
    """
    Analisa um lote de decisões e retorna estatísticas consolidadas.
    
    Input: lista de dicts com pelo menos {"texto": "...", "numero_processo": "..."}
    Output: estatísticas consolidadas do banco de decisões.
    """
    resultados = {
        "procedente": 0,
        "improcedente": 0,
        "parcialmente_procedente": 0,
        "acordo": 0,
        "indeterminado": 0,
    }
    valores_condenacao = []
    analises = []

    for d in decisoes:
        texto = d.get("texto") or d.get("nome") or ""
        if not texto or len(texto) < 10:
            resultados["indeterminado"] += 1
            continue

        # Classificação rápida por keywords (sem API para economizar)
        resultado = classificar_resultado_decisao(texto)
        resultados[resultado] += 1

        valor = extrair_valor_condenacao(texto)
        if valor:
            valores_condenacao.append(valor)

        analises.append({
            "numero": d.get("numero_processo", "n/d"),
            "resultado": resultado,
            "valor_condenacao": valor,
            "trecho": texto[:150],
        })

    total = sum(resultados.values())
    taxa_procedencia = 0
    if total > 0:
        favoraveis = resultados["procedente"] + resultados["parcialmente_procedente"] + resultados["acordo"]
        taxa_procedencia = round(favoraveis / total * 100, 1)

    valor_medio = round(sum(valores_condenacao) / len(valores_condenacao), 2) if valores_condenacao else None
    valor_min = min(valores_condenacao) if valores_condenacao else None
    valor_max = max(valores_condenacao) if valores_condenacao else None

    return {
        "total_analisadas": total,
        "resultados": resultados,
        "taxa_procedencia": taxa_procedencia,
        "valores": {
            "encontrados": len(valores_condenacao),
            "medio": valor_medio,
            "minimo": valor_min,
            "maximo": valor_max,
        },
        "analises": analises[:20],  # limitar retorno
    }


def build_context(analise_lote: dict) -> str:
    """
    Constrói contexto textual para o GPT a partir da análise do lote.
    """
    r = analise_lote.get("resultados", {})
    v = analise_lote.get("valores", {})
    lines = [
        f"ANÁLISE AUTOMÁTICA DE {analise_lote.get('total_analisadas', 0)} DECISÕES (NL API)",
        "",
        "RESULTADOS:",
        f"  Procedentes:               {r.get('procedente', 0)}",
        f"  Parcialmente procedentes:   {r.get('parcialmente_procedente', 0)}",
        f"  Improcedentes:             {r.get('improcedente', 0)}",
        f"  Acordos:                   {r.get('acordo', 0)}",
        f"  Indeterminados:            {r.get('indeterminado', 0)}",
        "",
        f"TAXA DE PROCEDÊNCIA: {analise_lote.get('taxa_procedencia', 0)}%",
        "",
    ]
    if v.get("encontrados"):
        lines += [
            "VALORES DE CONDENAÇÃO:",
            f"  Encontrados: {v['encontrados']} decisões com valor",
            f"  Médio:   R$ {v['medio']:,.2f}" if v.get("medio") else "",
            f"  Mínimo:  R$ {v['minimo']:,.2f}" if v.get("minimo") else "",
            f"  Máximo:  R$ {v['maximo']:,.2f}" if v.get("maximo") else "",
            "",
        ]

    # Detalhes individuais
    analises = analise_lote.get("analises", [])
    if analises:
        lines.append("DETALHES POR PROCESSO:")
        for a in analises[:12]:
            val_str = f" | R$ {a['valor_condenacao']:,.2f}" if a.get("valor_condenacao") else ""
            lines.append(
                f"  • {a.get('numero', 'n/d')} → {a.get('resultado', '?').upper()}{val_str}"
            )
            if a.get("trecho"):
                lines.append(f"    Trecho: {a['trecho'][:100]}...")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# INSTÂNCIA GLOBAL
# ═══════════════════════════════════════════════════════
class NLClient:
    """Classe wrapper para facilitar importação e uso."""

    def is_configured(self) -> bool:
        return is_configured()

    def analyze_sentiment(self, text: str) -> dict:
        return analyze_sentiment(text)

    def analyze_entities(self, text: str) -> dict:
        return analyze_entities(text)

    def classify_text(self, text: str) -> dict:
        return classify_text(text)

    def analisar_decisao(self, text: str) -> dict:
        return analisar_decisao(text)

    def analisar_lote(self, decisoes: List[dict]) -> dict:
        return analisar_lote_decisoes(decisoes)

    def build_context(self, analise_lote: dict) -> str:
        return build_context(analise_lote)


NL = NLClient()
