"""
rotas_auth_planos.py
════════════════════════════════════════════════════════════════
Rotas a adicionar ao main_v7.py:

AUTENTICAÇÃO
  POST /auth/register
  POST /auth/login
  GET  /auth/me
  PUT  /auth/me

PLANOS & ASSINATURA
  GET  /planos
  POST /assinatura/checkout
  POST /assinatura/cancelar
  GET  /assinatura/status

PAGAMENTOS
  POST /pagamento/webhook          ← Asaas chama esta rota
  GET  /pagamento/{id}

ADMIN
  GET  /admin/clientes
  GET  /admin/clientes/{id}
  PUT  /admin/clientes/{id}/plano
  GET  /admin/stats
════════════════════════════════════════════════════════════════

COMO INTEGRAR:
1. Copie este arquivo para a raiz do repositório
2. No main_v7.py, adicione no topo:
       from rotas_auth_planos import registrar_rotas
       from database import criar_tabelas
3. Após criar o app FastAPI:
       criar_tabelas()
       registrar_rotas(app)
4. No /chat, adicione validação de tokens (ver comentário no fim)
"""
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import (
    get_db, Usuario, Assinatura, Pagamento, UsoTokens,
    PlanoNome, AssinaturaStatus, PagamentoStatus, PLANOS, criar_tabelas,
)
from auth import (
    criar_usuario, autenticar_usuario, get_usuario_atual, get_admin,
    verificar_tokens, debitar_tokens,
)
from asaas import ASAAS, AsaasError

log = logging.getLogger("rotas")

# ═══════════════════════════════════════════════════════════════
# SCHEMAS (Pydantic)
# ═══════════════════════════════════════════════════════════════
class RegisterIn(BaseModel):
    nome:     str
    email:    str
    senha:    str
    oab:      Optional[str] = None
    telefone: Optional[str] = None

class LoginIn(BaseModel):
    email: str
    senha: str

class UpdatePerfilIn(BaseModel):
    nome:     Optional[str] = None
    telefone: Optional[str] = None
    oab:      Optional[str] = None

class CheckoutIn(BaseModel):
    plano:    PlanoNome
    periodo:  str = "mensal"          # mensal | anual
    metodo:   str = "PIX"             # PIX | CREDIT_CARD | BOLETO
    cpf_cnpj: Optional[str] = None   # para criar cliente no Asaas

class AdminUpdatePlanoIn(BaseModel):
    plano:  PlanoNome
    tokens_mes: Optional[int] = None

# ═══════════════════════════════════════════════════════════════
# HELPER — serializar usuário
# ═══════════════════════════════════════════════════════════════
def _user_dict(user: Usuario) -> dict:
    assin = user.assinatura_ativa
    plano = user.plano_atual
    plano_info = PLANOS.get(plano, {})
    return {
        "id":            user.id,
        "nome":          user.nome,
        "email":         user.email,
        "oab":           user.oab,
        "telefone":      user.telefone,
        "is_admin":      user.is_admin,
        "criado_em":     user.criado_em.isoformat() if user.criado_em else None,
        "plano": {
            "nome":              plano_info.get("nome", plano),
            "slug":              plano,
            "tokens_mes":        assin.tokens_mes if assin else plano_info.get("tokens_mes", 0),
            "tokens_usados":     assin.tokens_usados_mes if assin else 0,
            "tokens_disponiveis":assin.tokens_disponiveis if assin else 0,
            "percentual_uso":    assin.percentual_uso if assin else 0,
            "status":            assin.status if assin else "sem_assinatura",
            "periodo":           assin.periodo if assin else None,
            "fim":               assin.fim.isoformat() if (assin and assin.fim) else None,
            "features":          plano_info.get("features", []),
        },
    }

# ═══════════════════════════════════════════════════════════════
# REGISTRAR ROTAS
# ═══════════════════════════════════════════════════════════════
def registrar_rotas(app: FastAPI):

    # ─────────────────────────────────────────────────────────
    # AUTH
    # ─────────────────────────────────────────────────────────
    @app.post("/auth/register", tags=["auth"])
    def register(body: RegisterIn, db: Session = Depends(get_db)):
        """Cadastro de novo usuário. Cria plano Free automaticamente."""
        if len(body.senha) < 6:
            raise HTTPException(400, "Senha deve ter pelo menos 6 caracteres.")
        user = criar_usuario(
            db, body.nome, body.email, body.senha,
            body.oab, body.telefone,
        )
        from auth import criar_token
        token = criar_token(user.id)
        return {"ok": True, "token": token, "usuario": _user_dict(user)}

    @app.post("/auth/login", tags=["auth"])
    def login(body: LoginIn, db: Session = Depends(get_db)):
        """Login. Retorna JWT + dados do usuário."""
        user, token = autenticar_usuario(db, body.email, body.senha)
        return {"ok": True, "token": token, "usuario": _user_dict(user)}

    @app.get("/auth/me", tags=["auth"])
    def me(user: Usuario = Depends(get_usuario_atual)):
        """Retorna dados do usuário autenticado."""
        return {"ok": True, "usuario": _user_dict(user)}

    @app.put("/auth/me", tags=["auth"])
    def update_perfil(
        body: UpdatePerfilIn,
        user: Usuario = Depends(get_usuario_atual),
        db: Session = Depends(get_db),
    ):
        """Atualiza perfil do usuário."""
        if body.nome:     user.nome     = body.nome.strip()
        if body.telefone: user.telefone = body.telefone
        if body.oab:      user.oab      = body.oab
        db.commit()
        return {"ok": True, "usuario": _user_dict(user)}

    # ─────────────────────────────────────────────────────────
    # PLANOS
    # ─────────────────────────────────────────────────────────
    @app.get("https://jurimetrix.com/planos", tags=["planos"])
    def listar_planos():
        """Lista todos os planos disponíveis com preços e features."""
        result = []
        for slug, info in PLANOS.items():
            result.append({
                "slug":           slug,
                "nome":           info["nome"],
                "tokens_mes":     info["tokens_mes"],
                "tokens_fmt":     _fmt_tokens(info["tokens_mes"]),
                "preco_mensal":   info["preco_mensal"],
                "preco_anual":    info["preco_anual"],
                "desconto_anual": _desconto_anual(info),
                "features":       info["features"],
                "popular":        slug == PlanoNome.plus,
            })
        return {"ok": True, "planos": result}

    def _fmt_tokens(n: int) -> str:
        if n >= 999_000_000: return "Ilimitado"
        if n >= 1_000_000:   return f"{n//1_000_000}M"
        if n >= 1_000:       return f"{n//1_000}K"
        return str(n)

    def _desconto_anual(info: dict) -> str:
        if info["preco_mensal"] == 0: return ""
        anual_equiv = info["preco_anual"] / 12
        pct = round((1 - anual_equiv / info["preco_mensal"]) * 100)
        return f"{pct}% off" if pct > 0 else ""

    # ─────────────────────────────────────────────────────────
    # ASSINATURA / CHECKOUT
    # ─────────────────────────────────────────────────────────
    @app.post("/assinatura/checkout", tags=["assinatura"])
    def checkout(
        body: CheckoutIn,
        user: Usuario = Depends(get_usuario_atual),
        db: Session = Depends(get_db),
    ):
        """
        Inicia checkout de assinatura.
        Retorna link de pagamento PIX/boleto ou instrução para cartão.
        """
        plano_info = PLANOS.get(body.plano)
        if not plano_info:
            raise HTTPException(400, "Plano inválido.")

        preco = plano_info["preco_anual"] if body.periodo == "anual" else plano_info["preco_mensal"]

        # Plano free — ativa direto sem pagamento
        if preco == 0:
            _ativar_plano_free(db, user)
            return {"ok": True, "free": True, "message": "Plano Free ativado com sucesso."}

        if not ASAAS.is_configured():
            raise HTTPException(503, "Gateway de pagamento não configurado. Contate o suporte.")

        try:
            # Criar/obter cliente no Asaas
            customer_id = ASAAS.get_ou_criar_cliente(
                nome=user.nome, email=user.email,
                cpf_cnpj=body.cpf_cnpj, telefone=user.telefone,
            )

            descricao = f"S&M OS — Plano {plano_info['nome']} ({body.periodo})"
            ciclo     = "YEARLY" if body.periodo == "anual" else "MONTHLY"

            # Criar assinatura recorrente
            sub = ASAAS.criar_assinatura(
                customer_id=customer_id,
                valor=preco,
                descricao=descricao,
                ciclo=ciclo,
                metodo=body.metodo,
                external_reference=user.id,
            )
            sub_id = sub.get("id")

            # Salvar assinatura pendente no banco
            assin = _criar_ou_atualizar_assinatura(db, user, body, preco, customer_id, sub_id)

            # Buscar primeiro pagamento para obter PIX/boleto
            pagamento_info = _obter_primeiro_pagamento(sub_id, body.metodo, assin.id, db)

            return {
                "ok":             True,
                "assinatura_id":  assin.id,
                "asaas_sub_id":   sub_id,
                "metodo":         body.metodo,
                "valor":          preco,
                **pagamento_info,
            }

        except AsaasError as e:
            log.error(f"Asaas checkout error: {e}")
            raise HTTPException(502, f"Erro no gateway de pagamento: {str(e)}")

    def _ativar_plano_free(db: Session, user: Usuario):
        assin = user.assinatura_ativa
        if assin:
            assin.plano     = PlanoNome.free
            assin.status    = AssinaturaStatus.ativa
            assin.tokens_mes = PLANOS[PlanoNome.free]["tokens_mes"]
            assin.tokens_usados_mes = 0
            assin.fim       = None
        else:
            assin = Assinatura(
                id=str(uuid.uuid4()), usuario_id=user.id,
                plano=PlanoNome.free, status=AssinaturaStatus.ativa,
                periodo="mensal", preco=0.0,
                tokens_mes=PLANOS[PlanoNome.free]["tokens_mes"],
                tokens_usados_mes=0,
            )
            db.add(assin)
        db.commit()

    def _criar_ou_atualizar_assinatura(
        db, user, body: CheckoutIn, preco, customer_id, sub_id
    ) -> Assinatura:
        assin = user.assinatura_ativa
        if not assin:
            assin = Assinatura(id=str(uuid.uuid4()), usuario_id=user.id)
            db.add(assin)
        assin.plano             = body.plano
        assin.status            = AssinaturaStatus.pendente
        assin.periodo           = body.periodo
        assin.preco             = preco
        assin.tokens_mes        = PLANOS[body.plano]["tokens_mes"]
        assin.asaas_customer_id = customer_id
        assin.asaas_sub_id      = sub_id
        db.commit()
        db.refresh(assin)
        return assin

    def _obter_primeiro_pagamento(sub_id, metodo, assin_id, db) -> dict:
        try:
            r = ASAAS._get(f"/subscriptions/{sub_id}/payments?limit=1")
            payments = (r.get("data") or [])
            if not payments:
                return {"message": "Aguardando geração do pagamento."}
            pay = payments[0]
            pay_id = pay.get("id")

            pag = Pagamento(
                id=str(uuid.uuid4()), assinatura_id=assin_id,
                asaas_id=pay_id, valor=pay.get("value", 0),
                metodo=metodo,
                url_pagamento=pay.get("bankSlipUrl") or pay.get("invoiceUrl"),
            )

            if metodo == "PIX":
                pix = ASAAS.obter_pix(pay_id)
                pag.pix_qrcode     = pix.get("encodedImage")
                pag.pix_copia_cola = pix.get("payload")

            db.add(pag); db.commit()

            return {
                "payment_id":     pay_id,
                "url_pagamento":  pag.url_pagamento,
                "pix_copia_cola": pag.pix_copia_cola,
                "pix_qrcode":     pag.pix_qrcode,
                "vencimento":     pay.get("dueDate"),
            }
        except Exception as e:
            log.warning(f"Erro ao obter primeiro pagamento: {e}")
            return {"message": "Pagamento gerado. Verifique seu e-mail."}

    @app.get("/assinatura/status", tags=["assinatura"])
    def status_assinatura(user: Usuario = Depends(get_usuario_atual)):
        """Retorna status atual da assinatura e uso de tokens."""
        return {"ok": True, "usuario": _user_dict(user)}

    @app.post("/assinatura/cancelar", tags=["assinatura"])
    def cancelar(
        user: Usuario = Depends(get_usuario_atual),
        db: Session = Depends(get_db),
    ):
        """Cancela assinatura recorrente no Asaas."""
        assin = user.assinatura_ativa
        if not assin:
            raise HTTPException(404, "Nenhuma assinatura ativa.")
        if assin.plano == PlanoNome.free:
            raise HTTPException(400, "Plano Free não pode ser cancelado.")

        if assin.asaas_sub_id and ASAAS.is_configured():
            try:
                ASAAS.cancelar_assinatura(assin.asaas_sub_id)
            except AsaasError as e:
                log.warning(f"Asaas cancelar: {e}")

        assin.status       = AssinaturaStatus.cancelada
        assin.renovacao_auto = False
        db.commit()
        return {"ok": True, "message": "Assinatura cancelada. Acesso mantido até o fim do período."}

    # ─────────────────────────────────────────────────────────
    # WEBHOOK ASAAS
    # ─────────────────────────────────────────────────────────
    @app.post("/pagamento/webhook", tags=["pagamento"])
    async def webhook_asaas(request: Request, db: Session = Depends(get_db)):
        """
        Webhook do Asaas — chamado automaticamente quando pagamento é confirmado.
        Configurar no painel Asaas: https://www.asaas.com/config/notificacoes
        URL: https://seu-backend.onrender.com/pagamento/webhook
        """
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False}, status_code=400)

        log.info(f"[WEBHOOK] {payload.get('event')}")
        parsed = ASAAS.parse_webhook(payload)
        event  = parsed.get("event", "")

        if event in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
            _processar_pagamento_confirmado(db, parsed)
        elif event == "PAYMENT_OVERDUE":
            _processar_pagamento_vencido(db, parsed)

        return {"ok": True}

    def _processar_pagamento_confirmado(db: Session, parsed: dict):
        sub_id = parsed.get("subscription_id")
        if not sub_id:
            return
        assin = db.query(Assinatura).filter(Assinatura.asaas_sub_id == sub_id).first()
        if not assin:
            return
        assin.status    = AssinaturaStatus.ativa
        assin.tokens_usados_mes = 0  # reset mensal
        assin.fim       = datetime.utcnow() + (
            timedelta(days=365) if assin.periodo == "anual" else timedelta(days=31)
        )

        pay = db.query(Pagamento).filter(
            Pagamento.asaas_id == parsed.get("payment_id")
        ).first()
        if pay:
            pay.status  = PagamentoStatus.confirmado
            pay.pago_em = datetime.utcnow()
        db.commit()
        log.info(f"[WEBHOOK] Assinatura {assin.id} ativada — plano {assin.plano}")

    def _processar_pagamento_vencido(db: Session, parsed: dict):
        sub_id = parsed.get("subscription_id")
        if not sub_id:
            return
        assin = db.query(Assinatura).filter(Assinatura.asaas_sub_id == sub_id).first()
        if assin:
            assin.status = AssinaturaStatus.expirada
            db.commit()
            log.warning(f"[WEBHOOK] Assinatura {assin.id} expirada por falta de pagamento")

    @app.get("/pagamento/{payment_id}", tags=["pagamento"])
    def status_pagamento(
        payment_id: str,
        user: Usuario = Depends(get_usuario_atual),
        db: Session = Depends(get_db),
    ):
        """Verifica status de um pagamento específico."""
        pag = db.query(Pagamento).filter(Pagamento.asaas_id == payment_id).first()
        if not pag:
            raise HTTPException(404, "Pagamento não encontrado.")
        return {
            "ok":       True,
            "status":   pag.status,
            "valor":    pag.valor,
            "metodo":   pag.metodo,
            "pago_em":  pag.pago_em.isoformat() if pag.pago_em else None,
        }

    # ─────────────────────────────────────────────────────────
    # ADMIN
    # ─────────────────────────────────────────────────────────
    @app.get("/admin/clientes", tags=["admin"])
    def admin_clientes(
        page: int = 1, limit: int = 20,
        plano: Optional[str] = None,
        admin: Usuario = Depends(get_admin),
        db: Session = Depends(get_db),
    ):
        """Lista todos os clientes com filtros."""
        q = db.query(Usuario)
        if plano:
            q = q.join(Assinatura).filter(Assinatura.plano == plano, Assinatura.status == AssinaturaStatus.ativa)
        total  = q.count()
        users  = q.offset((page-1)*limit).limit(limit).all()
        return {
            "ok":    True,
            "total": total,
            "page":  page,
            "items": [_user_dict(u) for u in users],
        }

    @app.get("/admin/clientes/{user_id}", tags=["admin"])
    def admin_cliente_detalhe(
        user_id: str,
        admin: Usuario = Depends(get_admin),
        db: Session = Depends(get_db),
    ):
        """Detalhes de um cliente específico."""
        user = db.query(Usuario).filter(Usuario.id == user_id).first()
        if not user:
            raise HTTPException(404, "Usuário não encontrado.")
        usos = db.query(UsoTokens).filter(
            UsoTokens.usuario_id == user_id
        ).order_by(UsoTokens.criado_em.desc()).limit(20).all()
        return {
            "ok":      True,
            "usuario": _user_dict(user),
            "uso_recente": [{
                "tokens":    u.tokens_total,
                "endpoint":  u.endpoint,
                "criado_em": u.criado_em.isoformat(),
            } for u in usos],
        }

    @app.put("/admin/clientes/{user_id}/plano", tags=["admin"])
    def admin_update_plano(
        user_id: str,
        body: AdminUpdatePlanoIn,
        admin: Usuario = Depends(get_admin),
        db: Session = Depends(get_db),
    ):
        """Altera plano de um cliente manualmente."""
        user  = db.query(Usuario).filter(Usuario.id == user_id).first()
        if not user:
            raise HTTPException(404, "Usuário não encontrado.")
        assin = user.assinatura_ativa
        if not assin:
            assin = Assinatura(id=str(uuid.uuid4()), usuario_id=user.id)
            db.add(assin)
        assin.plano     = body.plano
        assin.status    = AssinaturaStatus.ativa
        assin.tokens_mes = body.tokens_mes or PLANOS[body.plano]["tokens_mes"]
        assin.tokens_usados_mes = 0
        db.commit()
        return {"ok": True, "usuario": _user_dict(user)}

    @app.get("/admin/stats", tags=["admin"])
    def admin_stats(
        admin: Usuario = Depends(get_admin),
        db: Session = Depends(get_db),
    ):
        """Estatísticas gerais do sistema."""
        total_users  = db.query(Usuario).count()
        total_ativos = db.query(Assinatura).filter(
            Assinatura.status == AssinaturaStatus.ativa
        ).count()
        por_plano = {}
        for plano in PlanoNome:
            count = db.query(Assinatura).filter(
                Assinatura.plano == plano,
                Assinatura.status == AssinaturaStatus.ativa,
            ).count()
            por_plano[plano] = count
        from sqlalchemy import func as sa_func
        tokens_total = db.query(sa_func.sum(UsoTokens.tokens_total)).scalar() or 0
        return {
            "ok":           True,
            "total_usuarios": total_users,
            "assinaturas_ativas": total_ativos,
            "por_plano":    por_plano,
            "tokens_consumidos_total": tokens_total,
        }
