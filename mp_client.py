"""
Mercado Pago — Integração Jurimetrix
PIX + Cartão via Checkout Bricks + Assinaturas recorrentes
"""
import os, requests, logging
log = logging.getLogger("mp")

MP_ACCESS_TOKEN = (os.getenv("MP_ACCESS_TOKEN") or "").strip()
MP_PUBLIC_KEY   = (os.getenv("MP_PUBLIC_KEY") or "").strip()
MP_BASE         = "https://api.mercadopago.com"

PLANOS = {
    "plus":      {"nome": "Plus",      "valor": 109.99, "tokens": 3_000_000,  "periodo": "monthly"},
    "pro":       {"nome": "Pro",       "valor": 549.99, "tokens": 15_000_000, "periodo": "monthly"},
    "unlimited": {"nome": "Unlimited", "valor": 999.99, "tokens": None,       "periodo": "monthly"},
}

def is_configured():
    return bool(MP_ACCESS_TOKEN)

def _headers():
    return {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": os.urandom(16).hex(),
    }

def criar_preferencia(plano_slug: str, user_id: str, user_email: str, back_url: str = "https://jurimetrix.com") -> dict:
    """Cria preferência de pagamento no Mercado Pago."""
    plano = PLANOS.get(plano_slug)
    if not plano:
        raise ValueError(f"Plano inválido: {plano_slug}")

    payload = {
        "items": [{
            "id": plano_slug,
            "title": f"Jurimetrix {plano['nome']} — Mensal",
            "description": f"Plano {plano['nome']} — acesso mensal à plataforma Jurimetrix",
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": plano["valor"],
        }],
        "payer": {"email": user_email},
        "external_reference": f"{user_id}|{plano_slug}",
        "back_urls": {
            "success": f"{back_url}/painel-do-cliente/?pagamento=sucesso&plano={plano_slug}",
            "failure": f"{back_url}/pricing/?pagamento=erro",
            "pending": f"{back_url}/painel-do-cliente/?pagamento=pendente",
        },
        "auto_return": "approved",
        "notification_url": "https://sm-os-demo-backend.onrender.com/mp/webhook",
        "statement_descriptor": "JURIMETRIX",
        "expires": False,
        "payment_methods": {
            "excluded_payment_types": [],
            "installments": 12,
        },
    }

    r = requests.post(f"{MP_BASE}/checkout/preferences", json=payload, headers=_headers(), timeout=15)
    r.raise_for_status()
    data = r.json()
    log.info(f"[MP] Preferência criada: {data.get('id')} para {user_email} plano={plano_slug}")
    return {
        "preference_id": data["id"],
        "init_point": data["init_point"],
        "sandbox_init_point": data.get("sandbox_init_point"),
        "plano": plano,
    }

def criar_assinatura(plano_slug: str, user_email: str, user_id: str, card_token: str = None) -> dict:
    """Cria plano de assinatura recorrente."""
    plano = PLANOS.get(plano_slug)
    if not plano:
        raise ValueError(f"Plano inválido: {plano_slug}")

    # Primeiro cria o plano de assinatura
    plan_payload = {
        "reason": f"Jurimetrix {plano['nome']}",
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": plano["valor"],
            "currency_id": "BRL",
        },
        "payment_methods_allowed": {
            "payment_types": [{"id": "credit_card"}, {"id": "debit_card"}],
        },
        "back_url": "https://jurimetrix.com/painel-do-cliente/",
    }

    r = requests.post(f"{MP_BASE}/preapproval_plan", json=plan_payload, headers=_headers(), timeout=15)
    r.raise_for_status()
    plan_data = r.json()

    return {
        "plan_id": plan_data["id"],
        "init_point": plan_data["init_point"],
        "plano": plano,
    }

def verificar_pagamento(payment_id: str) -> dict:
    """Verifica status de um pagamento."""
    r = requests.get(f"{MP_BASE}/v1/payments/{payment_id}", headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json()

def verificar_preferencia(preference_id: str) -> dict:
    """Busca pagamentos de uma preferência."""
    r = requests.get(
        f"{MP_BASE}/v1/payments/search",
        params={"preference_id": preference_id},
        headers=_headers(),
        timeout=15
    )
    r.raise_for_status()
    return r.json()
