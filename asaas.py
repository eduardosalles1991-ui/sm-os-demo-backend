"""
asaas.py — Integração com gateway Asaas
PIX + Cartão de crédito + Boleto
SM OS v8

Para ativar: adicionar nas envs do Render:
    ASAAS_API_KEY=<sua_chave>
    ASAAS_ENV=sandbox   (ou production)
"""
import os
import uuid
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import requests

log = logging.getLogger("asaas")

ASAAS_API_KEY = os.getenv("ASAAS_API_KEY", "").strip()
ASAAS_ENV     = os.getenv("ASAAS_ENV", "sandbox").strip()
ASAAS_BASE    = (
    "https://sandbox.asaas.com/api/v3"
    if ASAAS_ENV == "sandbox"
    else "https://api.asaas.com/api/v3"
)

class AsaasError(Exception):
    pass

class AsaasClient:
    def _headers(self) -> Dict[str, str]:
        return {
            "access_token": ASAAS_API_KEY,
            "Content-Type": "application/json",
            "User-Agent":   "SMOS/8.0",
        }

    def _post(self, path: str, data: dict) -> dict:
        if not ASAAS_API_KEY:
            raise AsaasError("ASAAS_API_KEY não configurado.")
        try:
            r = requests.post(
                f"{ASAAS_BASE}{path}",
                headers=self._headers(),
                json=data,
                timeout=30,
            )
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            body = e.response.text if e.response else ""
            raise AsaasError(f"Asaas HTTP {getattr(e.response,'status_code','?')}: {body[:400]}")
        except requests.RequestException as e:
            raise AsaasError(f"Asaas conexão: {e}")

    def _get(self, path: str) -> dict:
        if not ASAAS_API_KEY:
            raise AsaasError("ASAAS_API_KEY não configurado.")
        try:
            r = requests.get(f"{ASAAS_BASE}{path}", headers=self._headers(), timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise AsaasError(f"Asaas GET: {e}")

    # ── Clientes ──────────────────────────────────────────────────────
    def criar_cliente(self, nome: str, email: str, cpf_cnpj: str = None,
                      telefone: str = None) -> dict:
        """Cria ou atualiza cliente no Asaas."""
        data = {
            "name":         nome,
            "email":        email,
            "externalReference": email,
        }
        if cpf_cnpj:
            data["cpfCnpj"] = cpf_cnpj.replace(".", "").replace("-", "").replace("/", "")
        if telefone:
            data["mobilePhone"] = telefone
        return self._post("/customers", data)

    def buscar_cliente_por_email(self, email: str) -> Optional[str]:
        """Retorna ID do cliente Asaas se existir."""
        try:
            r = self._get(f"/customers?email={email}")
            items = r.get("data") or []
            return items[0]["id"] if items else None
        except Exception:
            return None

    def get_ou_criar_cliente(self, nome: str, email: str,
                             cpf_cnpj: str = None, telefone: str = None) -> str:
        """Retorna ID do cliente Asaas, criando se necessário."""
        existing = self.buscar_cliente_por_email(email)
        if existing:
            return existing
        result = self.criar_cliente(nome, email, cpf_cnpj, telefone)
        return result["id"]

    # ── Cobranças (pagamento único) ───────────────────────────────────
    def criar_cobranca(
        self,
        customer_id: str,
        valor: float,
        descricao: str,
        metodo: str = "PIX",           # PIX | CREDIT_CARD | BOLETO
        vencimento_dias: int = 3,
        cartao: dict = None,           # se CREDIT_CARD
        external_reference: str = None,
    ) -> dict:
        """
        Cria cobrança no Asaas.
        metodo: 'PIX' | 'CREDIT_CARD' | 'BOLETO'
        """
        vencimento = (datetime.utcnow() + timedelta(days=vencimento_dias)).strftime("%Y-%m-%d")
        data: dict = {
            "customer":          customer_id,
            "billingType":       metodo.upper(),
            "value":             round(valor, 2),
            "dueDate":           vencimento,
            "description":       descricao,
            "externalReference": external_reference or str(uuid.uuid4()),
        }
        if metodo.upper() == "CREDIT_CARD" and cartao:
            data["creditCard"] = cartao
        return self._post("/payments", data)

    def obter_pix(self, payment_id: str) -> dict:
        """Retorna QR Code e copia-e-cola do PIX."""
        return self._get(f"/payments/{payment_id}/pixQrCode")

    def obter_cobranca(self, payment_id: str) -> dict:
        return self._get(f"/payments/{payment_id}")

    # ── Assinaturas recorrentes ───────────────────────────────────────
    def criar_assinatura(
        self,
        customer_id: str,
        valor: float,
        descricao: str,
        ciclo: str = "MONTHLY",         # MONTHLY | YEARLY
        metodo: str = "PIX",
        external_reference: str = None,
    ) -> dict:
        """Cria assinatura recorrente no Asaas."""
        data = {
            "customer":          customer_id,
            "billingType":       metodo.upper(),
            "value":             round(valor, 2),
            "nextDueDate":       (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d"),
            "cycle":             ciclo,
            "description":       descricao,
            "externalReference": external_reference or str(uuid.uuid4()),
        }
        return self._post("/subscriptions", data)

    def cancelar_assinatura(self, sub_id: str) -> dict:
        try:
            r = requests.delete(
                f"{ASAAS_BASE}/subscriptions/{sub_id}",
                headers=self._headers(), timeout=20,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            raise AsaasError(f"Cancelar assinatura: {e}")

    # ── Webhook parser ────────────────────────────────────────────────
    def parse_webhook(self, payload: dict) -> dict:
        """
        Interpreta payload do webhook Asaas.
        Retorna dict com: event, payment_id, status, customer_id, valor
        """
        event      = payload.get("event", "")
        payment    = payload.get("payment") or {}
        sub        = payload.get("subscription") or {}

        return {
            "event":           event,
            "payment_id":      payment.get("id"),
            "subscription_id": payment.get("subscription") or sub.get("id"),
            "customer_id":     payment.get("customer"),
            "valor":           payment.get("value"),
            "status":          payment.get("status"),
            "external_ref":    payment.get("externalReference"),
            "metodo":          payment.get("billingType"),
            "pago_em":         payment.get("paymentDate"),
            "raw":             payload,
        }

    def is_configured(self) -> bool:
        return bool(ASAAS_API_KEY)

ASAAS = AsaasClient()
