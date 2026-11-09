"""Supabase: login (Auth) and per-user storage (Postgres through the Data API), called over plain HTTPS with httpx.

Enabled when SUPABASE_URL and SUPABASE_ANON_KEY are set; without them the app runs as before (no login, JSON files on disk).
SUPABASE_SERVICE_ROLE_KEY is optional: with it the shared signal history is kept in Supabase too.

The browser never sees a Supabase key or talks to Supabase: it holds HttpOnly session cookies and this module does the calls.
Watchlist and portfolio requests carry the signed-in user's own token, so the row-level-security policies in
supabase/schema.sql decide what each user can read and write. Only the service-role calls bypass those policies."""
from __future__ import annotations
import base64, json, os, time
from datetime import datetime, timezone

import httpx


def _env(*names: str) -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


URL = _env("SUPABASE_URL").rstrip("/")
KEY = _env("SUPABASE_ANON_KEY", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_KEY")       # public key: safe with row-level security on
SERVICE_KEY = _env("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY")            # bypasses row-level security: server only
HOLDING_COLS = "id,symbol,qty,price,date:buy_date,note"
_client = httpx.Client(timeout=10)
_users: dict = {}      # access token -> (valid until, user): one check with Supabase per token, not one per request


class SupaError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def enabled() -> bool:
    return bool(URL and KEY)


def shared() -> bool:
    """True when app-wide state (signal history) can be kept in Supabase."""
    return bool(URL and SERVICE_KEY)


# ------------------------------------------------------------------ transport
def _send(method: str, path: str, *, token: str | None = None, key: str | None = None, **kw) -> httpx.Response:
    key = key or KEY
    headers = {"apikey": key, "Authorization": f"Bearer {token or key}", **kw.pop("headers", {})}
    for attempt in (0, 1):
        try:
            return _client.request(method, URL + path, headers=headers, **kw)
        except httpx.TimeoutException:
            raise SupaError(504, "Supabase did not answer in time. Try again.")
        except httpx.TransportError as e:
            # A serverless instance is frozen between requests, so a pooled connection can be dead when it wakes: retry once.
            if attempt:
                raise SupaError(502, f"Could not reach Supabase ({type(e).__name__}).")
    raise AssertionError("unreachable")


def _message(r: httpx.Response) -> str:
    try:
        j = r.json()
        return str(j.get("msg") or j.get("error_description") or j.get("message") or j.get("error") or r.text)[:300]
    except Exception:
        return r.text[:300] or f"HTTP {r.status_code}"


def _auth(method: str, path: str, **kw) -> dict:
    r = _send(method, "/auth/v1" + path, **kw)
    if r.status_code >= 500:
        raise SupaError(502, f"Supabase login service error: {_message(r)}")
    if r.status_code >= 400:
        raise SupaError(r.status_code, _message(r))
    return r.json() if r.content else {}


def _rest(method: str, path: str, **kw):
    r = _send(method, "/rest/v1" + path, **kw)
    if r.status_code < 400:
        return r.json() if r.content else None
    msg = _message(r)
    if "API key" in msg:
        raise SupaError(500, f"Supabase rejected the API key: check SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY. ({msg})")
    if r.status_code == 401:
        raise SupaError(401, "Your session has expired. Sign in again.")
    if r.status_code == 404 or "schema cache" in msg or "does not exist" in msg:
        raise SupaError(500, f"Supabase tables are missing: run supabase/schema.sql in the Supabase SQL editor. ({msg})")
    raise SupaError(r.status_code if r.status_code < 500 else 502, f"Supabase storage error: {msg}")


# ------------------------------------------------------------------ login
def _redirect(redirect_to: str | None) -> dict:
    return {"redirect_to": redirect_to} if redirect_to else {}


def sign_in(email: str, password: str) -> dict:
    return _auth("POST", "/token", params={"grant_type": "password"}, json={"email": email, "password": password})


def sign_up(email: str, password: str, redirect_to: str | None = None) -> dict:
    """A session if the project auto-confirms sign-ups, otherwise a bare user: Supabase has emailed a confirmation link."""
    return _auth("POST", "/signup", params=_redirect(redirect_to), json={"email": email, "password": password})


def refresh(refresh_token: str) -> dict:
    return _auth("POST", "/token", params={"grant_type": "refresh_token"}, json={"refresh_token": refresh_token})


def recover(email: str, redirect_to: str | None = None) -> None:
    _auth("POST", "/recover", params=_redirect(redirect_to), json={"email": email})


def update_password(token: str, password: str) -> None:
    _auth("PUT", "/user", token=token, json={"password": password})


def sign_out(token: str) -> None:
    _users.pop(token, None)
    try:
        _auth("POST", "/logout", token=token, params={"scope": "local"})
    except SupaError:
        pass   # the cookies are cleared either way


def token_expiry(token: str) -> float:
    """The `exp` claim of a JWT, or 0. Read without verifying: it only decides how long a token Supabase has vouched for stays cached."""
    try:
        body = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))["exp"])
    except Exception:
        return 0.0


def user_for(token: str | None) -> dict | None:
    """The signed-in user behind an access token ({"id", "email", "token"}), or None if Supabase does not accept it."""
    if not token:
        return None
    now = time.time()
    hit = _users.get(token)
    if hit:
        if hit[0] > now:
            return hit[1]
        _users.pop(token, None)
    exp = token_expiry(token)
    if exp <= now:
        return None
    r = _send("GET", "/auth/v1/user", token=token)
    if r.status_code >= 500:
        raise SupaError(502, f"Supabase login service error: {_message(r)}")
    if r.status_code != 200:
        return None
    u = r.json()
    user = {"id": u["id"], "email": u.get("email"), "token": token}
    if len(_users) > 5000:
        for k in [k for k, v in _users.items() if v[0] <= now]:
            _users.pop(k, None)
    _users[token] = (exp - 30, user)
    return user


# ------------------------------------------------------------------ per-user storage (the user's own token: row-level security applies)
def watchlist(user: dict) -> list[str]:
    rows = _rest("GET", "/watchlist", token=user["token"], params={"select": "symbol", "user_id": f"eq.{user['id']}", "order": "created_at.asc"})
    return [r["symbol"] for r in rows]


def watch_add(user: dict, symbol: str) -> None:
    _rest("POST", "/watchlist", token=user["token"], params={"on_conflict": "user_id,symbol"},
          headers={"Prefer": "resolution=ignore-duplicates,return=minimal"}, json={"user_id": user["id"], "symbol": symbol})


def watch_del(user: dict, symbol: str) -> None:
    _rest("DELETE", "/watchlist", token=user["token"], params={"user_id": f"eq.{user['id']}", "symbol": f"eq.{symbol}"})


def holdings(user: dict) -> list[dict]:
    return _rest("GET", "/holdings", token=user["token"], params={"select": HOLDING_COLS, "user_id": f"eq.{user['id']}", "order": "created_at.asc"})


def holding_add(user: dict, h: dict) -> dict:
    row = {"user_id": user["id"], "symbol": h["symbol"], "qty": h["qty"], "price": h["price"], "buy_date": h["date"], "note": h.get("note", "")}
    return _rest("POST", "/holdings", token=user["token"], params={"select": HOLDING_COLS}, headers={"Prefer": "return=representation"}, json=row)[0]


def holding_del(user: dict, hid: str) -> None:
    _rest("DELETE", "/holdings", token=user["token"], params={"user_id": f"eq.{user['id']}", "id": f"eq.{hid}"})


# ------------------------------------------------------------------ app-wide state (service-role key, never a user's token)
def state_get(key: str, default):
    rows = _rest("GET", "/app_state", key=SERVICE_KEY, params={"select": "data", "key": f"eq.{key}"})
    return rows[0]["data"] if rows else default


def state_put(key: str, obj) -> None:
    _rest("POST", "/app_state", key=SERVICE_KEY, params={"on_conflict": "key"}, headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
          json={"key": key, "data": obj, "updated_at": datetime.now(timezone.utc).isoformat()})
