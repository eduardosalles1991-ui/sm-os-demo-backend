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
            query="id,titulo,preview,tribunal,numero_processo,criado_em,atualizado_em",
            filters={"usuario_id": f"eq.{user_id}", "arquivada": "eq.false", "order": "atualizado_em.desc", "limit": str(limit)})

    def atualizar_conversa(self, conversa_id, data):
        rows = self.update("conversas", data, {"id": f"eq.{conversa_id}"})
        return rows[0] if rows else {}

    def deletar_conversa(self, conversa_id, user_id):
        self.delete("conversas", {"id": f"eq.{conversa_id}", "usuario_id": f"eq.{user_id}"})

    # ── Mensagens ───────────────────────────────────────────────
    def salvar_mensagem(self, conversa_id, role, conteudo, prompt_level=None, tokens_usados=0):
        data = {"conversa_id": conversa_id, "role": role, "conteudo": conteudo, "tokens_usados": tokens_usados}
        if prompt_level: data["prompt_level"] = prompt_level
        return self.insert("mensagens", data)

    def listar_mensagens(self, conversa_id, limit=50):
        return self.select("mensagens",
            query="id,role,conteudo,prompt_level,tokens_usados,criado_em",
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
    """Extrai user_id (sub) do JWT Supabase. Retorna None se inválido."""
    if not SUPABASE_JWT_SECRET or not token:
        return None
    try:
        import jwt as pyjwt
        payload = pyjwt.decode(
            token.replace("Bearer ", "").strip(),
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False}
        )
        return payload.get("sub")
    except Exception as e:
        log.debug(f"JWT inválido: {e}")
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

DB = SupabaseClient()
