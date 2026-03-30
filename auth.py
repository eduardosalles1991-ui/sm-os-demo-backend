"""
auth.py — Autenticação JWT + gerenciamento de usuários
SM OS v8
"""
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from database import (
    Usuario, Assinatura, UsoTokens, PlanoNome, AssinaturaStatus,
    PLANOS, get_db,
)

# ── Config ────────────────────────────────────────────────────────────
JWT_SECRET   = os.getenv("JWT_SECRET", "smos-jwt-secret-change-in-prod-" + uuid.uuid4().hex)
JWT_ALGO     = "HS256"
JWT_EXPIRE_H = int(os.getenv("JWT_EXPIRE_H", "720"))  # 30 dias

# ── Passlib + Jose ────────────────────────────────────────────────────
try:
    from passlib.context import CryptContext
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    def hash_senha(senha: str) -> str:
        return pwd_ctx.hash(senha)
    def verificar_senha(senha: str, hashed: str) -> bool:
        return pwd_ctx.verify(senha, hashed)
except ImportError:
    import hashlib, hmac
    _SALT = os.getenv("PWD_SALT", "smos-salt-2026")
    def hash_senha(senha: str) -> str:
        return hmac.new(_SALT.encode(), senha.encode(), hashlib.sha256).hexdigest()
    def verificar_senha(senha: str, hashed: str) -> bool:
        return hmac.new(_SALT.encode(), senha.encode(), hashlib.sha256).hexdigest() == hashed

try:
    from jose import jwt, JWTError
    def criar_token(user_id: str) -> str:
        exp = datetime.utcnow() + timedelta(hours=JWT_EXPIRE_H)
        return jwt.encode({"sub": user_id, "exp": exp}, JWT_SECRET, algorithm=JWT_ALGO)
    def decodificar_token(token: str) -> Optional[str]:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
            return payload.get("sub")
        except JWTError:
            return None
except ImportError:
    import base64, json, hmac as _hmac, hashlib as _hashlib
    def criar_token(user_id: str) -> str:
        exp = int((datetime.utcnow() + timedelta(hours=JWT_EXPIRE_H)).timestamp())
        payload = base64.urlsafe_b64encode(json.dumps({"sub": user_id, "exp": exp}).encode()).decode()
        sig = _hmac.new(JWT_SECRET.encode(), payload.encode(), _hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"
    def decodificar_token(token: str) -> Optional[str]:
        try:
            parts = token.split(".")
            if len(parts) != 2:
                return None
            payload_b64, sig = parts
            expected = _hmac.new(JWT_SECRET.encode(), payload_b64.encode(), _hashlib.sha256).hexdigest()
            if sig != expected:
                return None
            data = json.loads(base64.urlsafe_b64decode(payload_b64 + "==").decode())
            if data.get("exp", 0) < datetime.utcnow().timestamp():
                return None
            return data.get("sub")
        except Exception:
            return None

# ── Criar usuário ─────────────────────────────────────────────────────
def criar_usuario(db: Session, nome: str, email: str, senha: str,
                  oab: str = None, telefone: str = None) -> Usuario:
    if db.query(Usuario).filter(Usuario.email == email.lower()).first():
        raise HTTPException(status_code=409, detail="E-mail já cadastrado.")

    user = Usuario(
        id       = str(uuid.uuid4()),
        nome     = nome.strip(),
        email    = email.lower().strip(),
        senha_hash = hash_senha(senha),
        oab      = oab,
        telefone = telefone,
    )
    db.add(user)

    # Criar assinatura Free automaticamente
    assinatura = Assinatura(
        id             = str(uuid.uuid4()),
        usuario_id     = user.id,
        plano          = PlanoNome.free,
        status         = AssinaturaStatus.ativa,
        periodo        = "mensal",
        preco          = 0.0,
        tokens_mes     = PLANOS[PlanoNome.free]["tokens_mes"],
        tokens_usados_mes = 0,
        inicio         = datetime.utcnow(),
        fim            = None,
        renovacao_auto = True,
    )
    db.add(assinatura)
    db.commit()
    db.refresh(user)
    return user

# ── Login ─────────────────────────────────────────────────────────────
def autenticar_usuario(db: Session, email: str, senha: str) -> tuple[Usuario, str]:
    user = db.query(Usuario).filter(
        Usuario.email == email.lower().strip(),
        Usuario.is_ativo == True,
    ).first()
    if not user or not verificar_senha(senha, user.senha_hash):
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")
    user.ultimo_acesso = datetime.utcnow()
    db.commit()
    token = criar_token(user.id)
    return user, token

# ── Obter usuário pelo token ──────────────────────────────────────────
def get_usuario_atual(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Usuario:
    token = None
    if authorization:
        if authorization.startswith("Bearer "):
            token = authorization[7:]
        else:
            token = authorization

    if not token:
        raise HTTPException(status_code=401, detail="Token não fornecido.")

    user_id = decodificar_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado.")

    user = db.query(Usuario).filter(Usuario.id == user_id, Usuario.is_ativo == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuário não encontrado.")

    return user

def get_admin(user: Usuario = Depends(get_usuario_atual)) -> Usuario:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
    return user

# ── Verificar e debitar tokens ────────────────────────────────────────
def verificar_tokens(user: Usuario, tokens_estimados: int = 500) -> Assinatura:
    """Verifica se usuário tem tokens suficientes. Lança 402 se não tiver."""
    assinatura = user.assinatura_ativa
    if not assinatura:
        raise HTTPException(status_code=402, detail="Nenhuma assinatura ativa.")

    if assinatura.plano != PlanoNome.unlimited:
        if assinatura.tokens_disponiveis < tokens_estimados:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "tokens_insuficientes",
                    "message": "Você atingiu o limite de tokens do seu plano.",
                    "tokens_disponiveis": assinatura.tokens_disponiveis,
                    "plano_atual": assinatura.plano,
                    "upgrade_url": "/planos",
                }
            )
    return assinatura

def debitar_tokens(
    db: Session,
    user: Usuario,
    tokens_input: int,
    tokens_output: int,
    session_id: str = None,
    endpoint: str = "chat",
    modelo: str = None,
) -> int:
    """Debita tokens usados e registra o uso. Retorna total debitado."""
    total = tokens_input + tokens_output
    assinatura = user.assinatura_ativa
    if not assinatura:
        return total

    if assinatura.plano != PlanoNome.unlimited:
        assinatura.tokens_usados_mes = min(
            assinatura.tokens_usados_mes + total,
            assinatura.tokens_mes,
        )

    uso = UsoTokens(
        id            = str(uuid.uuid4()),
        usuario_id    = user.id,
        session_id    = session_id,
        tokens_input  = tokens_input,
        tokens_output = tokens_output,
        tokens_total  = total,
        modelo        = modelo,
        endpoint      = endpoint,
    )
    db.add(uso)
    db.commit()
    return total
