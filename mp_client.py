"""
Mercado Pago — Integração Jurimetrix
PIX + Cartão via Checkout Bricks + Assinaturas
"""
import os, requests, logging
log = logging.getLogger("mp")

MP_ACCESS_TOKEN = (os.getenv("MP_ACCESS_TOKEN") or "").strip()
MP_PUBLIC_KEY   = (os.getenv("MP_PUBLIC_KEY") or "").strip()
MP_BASE         = "https://api.mercadopago.com"

PLANOS = {
    "plus":      {"nome": "Plus",      "tokens": 3_000_000},
    "pro":       {"nome": "Pro",       "tokens": 15_000_000},
    "unlimited": {"nome": "Unlimited", "tokens": None},
}

PRECOS = {
    "mensal": {"plus": 50.00, "pro": 549.99, "unlimited": 999.99},
    "anual":  {"plus": 999.99, "pro": 4999.99, "unlimited": 9999.99},
}

def is_configured():
    return bool(MP_ACCESS_TOKEN)

def _headers():
    return {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": os.urandom(16).hex(),
    }

def criar_preferencia(plano_slug: str, user_id: str, user_email: str, periodo: str = "mensal") -> dict:
    """Cria preferência de pagamento no Mercado Pago."""
    plano = PLANOS.get(plano_slug)
    if not plano:
        raise ValueError(f"Plano inválido: {plano_slug}")

    periodo = periodo if periodo in ("mensal", "anual") else "mensal"
    valor = PRECOS[periodo][plano_slug]
    titulo = f"Jurimetrix {plano['nome']} — {'Anual' if periodo == 'anual' else 'Mensal'}"

    payload = {
        "items": [{
            "id": f"{plano_slug}_{periodo}",
            "title": titulo,
            "description": f"Plano {plano['nome']} — acesso {'anual' if periodo == 'anual' else 'mensal'} à plataforma Jurimetrix",
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": valor,
        }],
        "payer": {"email": user_email},
        "external_reference": f"{user_id}|{plano_slug}|{periodo}",
        "back_urls": {
            "success": f"https://jurimetrix.com/pricing/?pagamento=sucesso&plano={plano_slug}",
            "failure": f"https://jurimetrix.com/pricing/?pagamento=erro",
            "pending": f"https://jurimetrix.com/pricing/?pagamento=pendente",
        },
        "auto_return": "approved",
        "notification_url": "https://sm-os-demo-backend.onrender.com/mp/webhook",
        "statement_descriptor": "JURIMETRIX",
        "payment_methods": {"installments": 1 if periodo == "anual" else 12},
    }

    r = requests.post(f"{MP_BASE}/checkout/preferences", json=payload, headers=_headers(), timeout=15)
    r.raise_for_status()
    data = r.json()
    log.info(f"[MP] Preferência criada: {data.get('id')} user={user_email} plano={plano_slug} periodo={periodo} valor=R${valor}")
    return {
        "preference_id": data["id"],
        "init_point": data["init_point"],
        "sandbox_init_point": data.get("sandbox_init_point"),
        "plano": plano,
        "valor": valor,
        "periodo": periodo,
    }

def verificar_pagamento(payment_id: str) -> dict:
    """Verifica status de um pagamento."""
    r = requests.get(f"{MP_BASE}/v1/payments/{payment_id}", headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json()
