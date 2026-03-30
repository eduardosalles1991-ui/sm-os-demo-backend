"""
database.py — Modelos e engine do banco de dados
SM OS v8 — SQLite (dev) / PostgreSQL (prod via DATABASE_URL)
"""
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, String, Integer, BigInteger,
    Boolean, DateTime, Float, Text, ForeignKey, Enum as SAEnum,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import enum

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./smos.db")

# SQLite precisa de check_same_thread=False
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ── Enums ─────────────────────────────────────────────────────────────
class PlanoNome(str, enum.Enum):
    free       = "free"
    plus       = "plus"
    pro        = "pro"
    unlimited  = "unlimited"

class AssinaturaStatus(str, enum.Enum):
    ativa      = "ativa"
    pendente   = "pendente"
    cancelada  = "cancelada"
    expirada   = "expirada"

class PagamentoStatus(str, enum.Enum):
    pendente   = "pendente"
    confirmado = "confirmado"
    falhou     = "falhou"
    estornado  = "estornado"

# ── Planos ────────────────────────────────────────────────────────────
PLANOS = {
    PlanoNome.free: {
        "nome":          "Free",
        "tokens_mes":    1_000_000,
        "preco_mensal":  0.0,
        "preco_anual":   0.0,
        "features": [
            "1.000.000 tokens/mês",
            "Consulta de processos DataJud",
            "Chat jurídico básico",
        ],
    },
    PlanoNome.plus: {
        "nome":          "Plus",
        "tokens_mes":    3_000_000,
        "preco_mensal":  109.99,
        "preco_anual":   999.99,
        "features": [
            "3.000.000 tokens/mês",
            "Tudo do Free",
            "Relatório PDF completo",
            "Análise OS 6.1",
            "Banco de decisões",
        ],
    },
    PlanoNome.pro: {
        "nome":          "Pro",
        "tokens_mes":    15_000_000,
        "preco_mensal":  549.99,
        "preco_anual":   4_999.99,
        "features": [
            "15.000.000 tokens/mês",
            "Tudo do Plus",
            "Red Team completo",
            "Análise temática avançada",
            "Suporte prioritário",
        ],
    },
    PlanoNome.unlimited: {
        "nome":          "Unlimited",
        "tokens_mes":    999_999_999,
        "preco_mensal":  999.99,
        "preco_anual":   9_999.99,
        "features": [
            "Tokens ilimitados",
            "Tudo do Pro",
            "Acesso antecipado a novidades",
            "Onboarding dedicado",
        ],
    },
}

# ── Modelos ───────────────────────────────────────────────────────────
class Usuario(Base):
    __tablename__ = "usuarios"

    id              = Column(String(36), primary_key=True)
    nome            = Column(String(120), nullable=False)
    email           = Column(String(200), unique=True, nullable=False, index=True)
    senha_hash      = Column(String(200), nullable=False)
    oab             = Column(String(30), nullable=True)
    telefone        = Column(String(20), nullable=True)
    is_admin        = Column(Boolean, default=False)
    is_ativo        = Column(Boolean, default=True)
    criado_em       = Column(DateTime, default=datetime.utcnow)
    ultimo_acesso   = Column(DateTime, nullable=True)

    assinaturas     = relationship("Assinatura", back_populates="usuario", cascade="all, delete-orphan")
    uso_tokens      = relationship("UsoTokens",  back_populates="usuario", cascade="all, delete-orphan")

    @property
    def assinatura_ativa(self):
        for a in self.assinaturas:
            if a.status == AssinaturaStatus.ativa:
                return a
        return None

    @property
    def plano_atual(self) -> PlanoNome:
        a = self.assinatura_ativa
        return a.plano if a else PlanoNome.free

    @property
    def tokens_limite(self) -> int:
        return PLANOS[self.plano_atual]["tokens_mes"]


class Assinatura(Base):
    __tablename__ = "assinaturas"

    id                  = Column(String(36), primary_key=True)
    usuario_id          = Column(String(36), ForeignKey("usuarios.id"), nullable=False, index=True)
    plano               = Column(SAEnum(PlanoNome), nullable=False, default=PlanoNome.free)
    status              = Column(SAEnum(AssinaturaStatus), nullable=False, default=AssinaturaStatus.pendente)
    periodo             = Column(String(10), default="mensal")  # mensal | anual
    preco               = Column(Float, default=0.0)
    tokens_mes          = Column(BigInteger, default=1_000_000)
    tokens_usados_mes   = Column(BigInteger, default=0)
    inicio              = Column(DateTime, default=datetime.utcnow)
    fim                 = Column(DateTime, nullable=True)
    renovacao_auto      = Column(Boolean, default=True)
    asaas_sub_id        = Column(String(100), nullable=True)   # ID da assinatura no Asaas
    asaas_customer_id   = Column(String(100), nullable=True)   # ID do cliente no Asaas
    criado_em           = Column(DateTime, default=datetime.utcnow)
    atualizado_em       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    usuario             = relationship("Usuario", back_populates="assinaturas")
    pagamentos          = relationship("Pagamento", back_populates="assinatura", cascade="all, delete-orphan")

    @property
    def tokens_disponiveis(self) -> int:
        return max(0, self.tokens_mes - self.tokens_usados_mes)

    @property
    def percentual_uso(self) -> float:
        if self.tokens_mes == 0:
            return 0.0
        return round(self.tokens_usados_mes / self.tokens_mes * 100, 1)


class Pagamento(Base):
    __tablename__ = "pagamentos"

    id              = Column(String(36), primary_key=True)
    assinatura_id   = Column(String(36), ForeignKey("assinaturas.id"), nullable=False, index=True)
    asaas_id        = Column(String(100), nullable=True, index=True)  # ID do pagamento no Asaas
    valor           = Column(Float, nullable=False)
    status          = Column(SAEnum(PagamentoStatus), default=PagamentoStatus.pendente)
    metodo          = Column(String(30), nullable=True)  # pix | credit_card | boleto
    url_pagamento   = Column(Text, nullable=True)   # link do boleto/PIX
    pix_qrcode      = Column(Text, nullable=True)   # base64 do QR code
    pix_copia_cola  = Column(Text, nullable=True)   # string copia e cola
    vencimento      = Column(DateTime, nullable=True)
    pago_em         = Column(DateTime, nullable=True)
    criado_em       = Column(DateTime, default=datetime.utcnow)

    assinatura      = relationship("Assinatura", back_populates="pagamentos")


class UsoTokens(Base):
    __tablename__ = "uso_tokens"

    id              = Column(String(36), primary_key=True)
    usuario_id      = Column(String(36), ForeignKey("usuarios.id"), nullable=False, index=True)
    session_id      = Column(String(36), nullable=True)
    tokens_input    = Column(Integer, default=0)
    tokens_output   = Column(Integer, default=0)
    tokens_total    = Column(Integer, default=0)
    modelo          = Column(String(30), nullable=True)
    endpoint        = Column(String(30), nullable=True)  # chat | relatorio
    criado_em       = Column(DateTime, default=datetime.utcnow)

    usuario         = relationship("Usuario", back_populates="uso_tokens")


def criar_tabelas():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
