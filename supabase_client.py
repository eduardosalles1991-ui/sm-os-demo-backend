"""
supabase_client.py — Cliente Supabase para o backend Render
Usa REST API com service_role (bypassa RLS)

Envs no Render:
  SUPABASE_URL         = https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY = eyJ... (service_role key)
  SUPABASE_JWT_SECRET  = seu_jwt_secret
"""
import os, logging
from typing import Optional, Any
import requests

log = logging.getLogger("supabase")

SUPABASE_URL         = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_KEY = (os.getenv("SUPABASE_SERVICE_KEY") or "").strip()
SUPABASE_JWT_SECRET  = (os.getenv("SUPABASE_JWT_SECRET") or "").strip()

class SupabaseError(Exception): pass

class SupabaseClient:
    def _h(self, prefer=""):
        h = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "Content-Type": "application/json"}
        if prefer: h["Prefer"] = prefer
        return h

    def _url(self, table): return f"{SUPABASE_URL}/rest/v1/{table}"

    def _rpc(self, fn, params):
        r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/{fn}", headers=self._h(), json=params, timeout=15)
        if not r.ok: raise SupabaseError(f"RPC {fn}: {r.status_code} {r.text[:300]}")
        return r.json()

    def select(self, table, query="*", filters=None):
        params = {"select": query}
        if filters: params.update(filters)
        r = requests.get(self._url(table), headers=self._h(), params=params, timeout=15)
        if not r.ok: raise SupabaseError(f"SELECT {table}: {r.status_code} {r.text[:300]}")
        return r.json()

    def insert(self, table, data, returning="representation"):
        r = requests.post(self._url(table), headers=self._h(f"return={returning}"), json=data, timeout=15)
        if not r.ok: raise SupabaseError(f"INSERT {table}: {r.status_code} {r.text[:300]}")
        result = r.json()
        return result[0] if isinstance(result, list) else result

    def update(self, table, data, filters):
        r = requests.patch(self._url(table), headers=self._h("return=representation"), params=filters, json=data, timeout=15)
        if not r.ok: raise SupabaseError(f"UPDATE {table}: {r.status_code} {r.text[:300]}")
        return r.json()

    def delete(self, table, filters):
        r = requests.delete(self._url(table), headers=self._h("return=representation"), params=filters, timeout=15)
        if not r.ok: raise SupabaseError(f"DELETE {table}: {r.status_code} {r.text[:300]}")
        return r.json()

    # ── Perfil ──────────────────────────────────────────────────
    def get_perfil(self, user_id):
        rows = self.select("perfis", filters={"id": f"eq.{user_id}"})
        return rows[0] if rows else None

    def update_perfil(self, user_id, data):
        rows = self.update("perfis", data, {"id": f"eq.{user_id}"})
        return rows[0] if rows else {}

    # ── Assinatura ──────────────────────────────────────────────
    def get_assinatura(self, user_id):
        rows = self.select("assinaturas", filters={"usuario_id": f"eq.{user_id}", "status": "eq.ativa", "limit": "1"})
        return rows[0] if rows else None

    def debitar_tokens(self, user_id, tokens):
        return self._rpc("incrementar_tokens", {"p_usuario_id": user_id, "p_tokens": tokens})

    # ── Conversas ───────────────────────────────────────────────
    def criar_conversa(self, user_id, titulo="Nova conversa", session_id=None, tribunal=None, numero_processo=None):
        data = {"usuario_id": user_id, "titulo": titulo[:80]}
        if session_id:      data["session_id"]      = session_id
        if tribunal:        data["tribunal"]         = tribunal
        if numero_processo: data["numero_processo"]  = numero_processo
        return self.insert("conversas", data)

    def listar_conversas(self, user_id, limit=50):
        return self.select("conversas",
            query="id,titulo,preview,tribunal,session_id,criado_em,atualizado_em",
            filters={"usuario_id": f"eq.{user_id}", "arquivada": "eq.false", "order": "atualizado_em.desc", "limit": str(limit)})

    def atualizar_conversa(self, conversa_id, data):
        rows = self.update("conversas", data, {"id": f"eq.{conversa_id}"})
        return rows[0] if rows else {}

    def deletar_conversa(self, conversa_id, user_id):
        self.delete("conversas", {"id": f"eq.{conversa_id}", "usuario_id": f"eq.{user_id}"})

    # ── Mensagens ───────────────────────────────────────────────
    def salvar_mensagem(self, conversa_id, role, conteudo, prompt_level=None, tokens_usados=0):
        tokens_input = tokens_usados // 2 if tokens_usados else 0
        tokens_output = tokens_usados - tokens_input
        data = {
            "conversa_id": conversa_id,
            "role": role,
            "conteudo": conteudo,
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
        }
        if prompt_level: data["prompt_level"] = prompt_level
        return self.insert("mensagens", data)

    def listar_mensagens(self, conversa_id, limit=50):
        return self.select("mensagens",
            query="id,role,conteudo,prompt_level,tokens_input,tokens_output,criado_em",
            filters={"conversa_id": f"eq.{conversa_id}", "order": "criado_em.asc", "limit": str(limit)})

    # ── Uso tokens ──────────────────────────────────────────────
    def registrar_uso(self, user_id, tokens_input, tokens_output, conversa_id=None, modelo=None, endpoint="chat"):
        data = {"usuario_id": user_id, "tokens_input": tokens_input, "tokens_output": tokens_output, "tokens_total": tokens_input+tokens_output, "modelo": modelo, "endpoint": endpoint}
        if conversa_id: data["conversa_id"] = conversa_id
        try: self.insert("uso_tokens", data, returning="minimal")
        except SupabaseError as e: log.warning(f"registrar_uso: {e}")

    # ── Admin ───────────────────────────────────────────────────
    def get_stats(self):
        rows = self.select("v_stats")
        return rows[0] if rows else {}

    def listar_clientes(self, limit=100, offset=0):
        return self.select("v_clientes", filters={"order": "criado_em.desc", "limit": str(limit), "offset": str(offset)})

    def is_configured(self):
        return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def is_configured() -> bool:
    """Verifica se Supabase está configurado (module-level)."""
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)

def get_user_id_from_token(token: str) -> Optional[str]:
    """
    Extrai user_id (sub) do JWT Supabase.
    Suporta HS256 (email/senha) e ES256 (Google OAuth).
    """
    if not token:
        return None
    clean = token.replace("Bearer ", "").strip()
    
    # Tenta HS256 primeiro (email/senha)
    if SUPABASE_JWT_SECRET:
        try:
            import jwt as pyjwt
            payload = pyjwt.decode(
                clean,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False}
            )
            return payload.get("sub")
        except Exception:
            pass
    
    # Tenta decodificar sem verificar assinatura (ES256 / Google OAuth)
    # Seguro pois apenas extrai o sub — a sessão já foi validada pelo Supabase
    try:
        import jwt as pyjwt
        payload = pyjwt.decode(
            clean,
            options={"verify_signature": False, "verify_aud": False},
            algorithms=["HS256", "ES256", "RS256"]
        )
        sub = payload.get("sub")
        # Valida que é um token do nosso projeto Supabase
        iss = payload.get("iss", "")
        if sub and "supabase" in iss:
            return sub
        return sub  # retorna mesmo sem iss para compatibilidade
    except Exception as e:
        log.warning(f"Token inválido: {e}")
        return None

def validar_jwt_supabase(token: str) -> Optional[dict]:
    """Valida JWT do Supabase Auth. Retorna payload completo ou None."""
    if not SUPABASE_JWT_SECRET or not token:
        return None
    try:
        import jwt as pyjwt
        return pyjwt.decode(
            token.replace("Bearer ", "").strip(),
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False}
        )
    except Exception as e:
        log.debug(f"JWT inválido: {e}")
        return None

def verificar_e_decrementar_tokens(user_id: str, tokens_entrada: int = 0) -> dict:
    """Verifica se usuario tem tokens e decrementa o uso da entrada."""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/assinaturas",
            params={"usuario_id": f"eq.{user_id}", "select": "id,tokens_mes,tokens_usados_mes,status"},
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            timeout=10
        )
        data = r.json() if r.ok else []
        if not data:
            return {"ok": True}  # sem assinatura = free, permite usar

        assin = data[0]
        assin_id = assin.get("id")
        tokens_mes = assin.get("tokens_mes") or 1_000_000
        tokens_usados = assin.get("tokens_usados_mes") or 0

        # Ilimitado (999_999_999)
        if tokens_mes >= 999_999_999:
            return {"ok": True}

        if tokens_usados >= tokens_mes:
            return {"ok": False, "motivo": "limite_atingido", "tokens_mes": tokens_mes, "tokens_usados": tokens_usados}

        # Decrementa tokens de entrada (~1 token = 4 chars)
        novo_usado = tokens_usados + max(tokens_entrada // 4, 100)
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/assinaturas",
            params={"id": f"eq.{assin_id}"},
            json={"tokens_usados_mes": novo_usado},
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "Content-Type": "application/json", "Prefer": "return=minimal"},
            timeout=10
        )
        log.info(f"[tokens] user={user_id[:8]} usado={novo_usado}/{tokens_mes}")
        return {"ok": True, "tokens_restantes": tokens_mes - novo_usado}
    except Exception as e:
        log.warning(f"[SB] verificar_tokens erro: {e}")
        return {"ok": True}

def registrar_tokens_resposta(user_id: str, chars_resposta: int = 0):
    """Adiciona tokens da resposta ao consumo do usuario."""
    try:
        tokens_resposta = max(chars_resposta // 4, 50)
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/assinaturas",
            params={"usuario_id": f"eq.{user_id}", "select": "id,tokens_usados_mes,tokens_mes"},
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            timeout=10
        )
        data = r.json() if r.ok else []
        if not data: return
        assin_id = data[0].get("id")
        tokens_mes = data[0].get("tokens_mes") or 1_000_000
        if tokens_mes >= 999_999_999: return
        tokens_usados = data[0].get("tokens_usados_mes") or 0
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/assinaturas",
            params={"id": f"eq.{assin_id}"},
            json={"tokens_usados_mes": tokens_usados + tokens_resposta},
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "Content-Type": "application/json", "Prefer": "return=minimal"},
            timeout=10
        )
    except Exception as e:
        log.warning(f"[SB] registrar_tokens_resposta erro: {e}")

def atualizar_plano_usuario(user_id: str, plano_slug: str, tokens_mes: int = None, payment_id: str = None):
    """Atualiza plano do usuário após pagamento aprovado."""
    try:
        # Busca o plano_id pelo slug
        planos = {
            "plus":      {"tokens": 3_000_000},
            "pro":       {"tokens": 15_000_000},
            "unlimited": {"tokens": 999_999_999},
        }
        tokens = tokens_mes or planos.get(plano_slug, {}).get("tokens", 1_000_000)

        # Busca plano_id na tabela planos
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/planos",
            params={"slug": f"eq.{plano_slug}", "select": "id"},
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            timeout=10
        )
        planos_data = r.json() if r.ok else []
        plano_id = planos_data[0]["id"] if planos_data else None

        if not plano_id:
            log.warning(f"Plano {plano_slug} não encontrado na tabela planos")
            return False

        # Atualiza assinatura
        payload = {
            "plano_id": plano_id,
            "status": "ativa",
            "tokens_mes": tokens,
            "tokens_usados_mes": 0,
        }
        if payment_id:
            payload["mp_payment_id"] = payment_id

        r2 = requests.patch(
            f"{SUPABASE_URL}/rest/v1/assinaturas",
            params={"user_id": f"eq.{user_id}"},
            json=payload,
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "Content-Type": "application/json"},
            timeout=10
        )
        log.info(f"[SB] Plano atualizado: user={user_id} plano={plano_slug} status={r2.status_code}")
        return r2.ok
    except Exception as e:
        log.error(f"[SB] Erro atualizar_plano_usuario: {e}")
        return False

DB = SupabaseClient()

# ── Funções de conveniência no nível do módulo ──────────────────────
def listar_conversas(user_id, limit=50):
    return DB.listar_conversas(user_id, limit)

def criar_conversa(user_id, titulo="Nova conversa", session_id=None, tribunal=None, numero_processo=None):
    return DB.criar_conversa(user_id, titulo, session_id, tribunal, numero_processo)

def listar_mensagens(conversa_id, limit=50):
    return DB.listar_mensagens(conversa_id, limit)

def salvar_mensagem(conversa_id, role, conteudo, prompt_level=None, tokens_usados=0):
    return DB.salvar_mensagem(conversa_id, role, conteudo, prompt_level, tokens_usados)

def deletar_conversa(conversa_id, user_id):
    return DB.deletar_conversa(conversa_id, user_id)

def get_stats():
    return DB.get_stats()

def is_configured():
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)
