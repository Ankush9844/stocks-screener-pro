"""Login and Supabase storage, exercised against an in-memory stand-in for Supabase's Auth and Data APIs (no network)."""
import base64, json, time, uuid

import httpx, pytest
from fastapi.testclient import TestClient
from app import data, main, supa
from app.main import app

ANON, SERVICE = "anon-key", "service-key"


class FakeSupabase:
    """Just enough of Supabase: email/password accounts, rotating refresh tokens, and tables where a user's token only
    reaches that user's rows (what the policies in supabase/schema.sql do) while app_state needs the service key."""

    def __init__(self):
        self.confirm_email = False
        self.users, self.access, self.refresh = {}, {}, {}
        self.tables = {"watchlist": [], "holdings": [], "app_state": []}
        self.calls = []

    def token(self, uid, ttl=3600):
        body = base64.urlsafe_b64encode(json.dumps({"sub": uid, "exp": time.time() + ttl, "jti": uuid.uuid4().hex}).encode()).decode().rstrip("=")
        self.access[tok := f"hdr.{body}.sig"] = uid
        return tok

    def session(self, user):
        rt = uuid.uuid4().hex
        self.refresh[rt] = user["id"]
        return {"access_token": self.token(user["id"]), "refresh_token": rt, "expires_in": 3600, "user": {"id": user["id"], "email": user["email"]}}

    def handler(self, req: httpx.Request) -> httpx.Response:
        path, q = req.url.path, dict(req.url.params)
        body = json.loads(req.content) if req.content else {}
        bearer = req.headers["authorization"].removeprefix("Bearer ")
        self.calls.append((req.method, path))
        if req.headers["apikey"] not in (ANON, SERVICE):
            return httpx.Response(401, json={"message": "Invalid API key"})
        if path.startswith("/auth/v1/"):
            return self.auth(req.method, path[9:], q, body, bearer)
        return self.rest(req, path.removeprefix("/rest/v1/"), q, body, bearer)

    def auth(self, method, path, q, body, bearer):
        by_id = {u["id"]: u for u in self.users.values()}
        if path == "signup":
            user = self.users.setdefault(body["email"], {"id": str(uuid.uuid4()), "email": body["email"], "password": body["password"]})
            return httpx.Response(200, json={"id": user["id"], "email": user["email"]} if self.confirm_email else self.session(user))
        if path == "token" and q["grant_type"] == "password":
            user = self.users.get(body["email"])
            if not user or user["password"] != body["password"]:
                return httpx.Response(400, json={"code": 400, "error_code": "invalid_credentials", "msg": "Invalid login credentials"})
            return httpx.Response(200, json=self.session(user))
        if path == "token" and q["grant_type"] == "refresh_token":
            uid = self.refresh.pop(body["refresh_token"], None)
            if not uid:
                return httpx.Response(400, json={"error_code": "refresh_token_not_found", "msg": "Invalid Refresh Token: Refresh Token Not Found"})
            return httpx.Response(200, json=self.session(by_id[uid]))
        if path == "recover":
            return httpx.Response(200, json={})
        uid = self.access.get(bearer)
        if not uid:
            return httpx.Response(403, json={"error_code": "bad_jwt", "msg": "invalid JWT"})
        if path == "user" and method == "GET":
            return httpx.Response(200, json={"id": uid, "email": by_id[uid]["email"]})
        if path == "user" and method == "PUT":
            by_id[uid]["password"] = body["password"]
            return httpx.Response(200, json={"id": uid, "email": by_id[uid]["email"]})
        if path == "logout":
            return httpx.Response(204)
        return httpx.Response(404, json={"msg": "not found"})

    def rest(self, req, table, q, body, bearer):
        service, uid = bearer == SERVICE, self.access.get(bearer)
        if not service and not uid:
            return httpx.Response(401, json={"code": "PGRST301", "message": "JWT expired"})
        if table not in self.tables:
            return httpx.Response(404, json={"code": "PGRST205", "message": f"Could not find the table 'public.{table}' in the schema cache"})
        rows = self.tables[table]
        visible = lambda r: service or (table != "app_state" and r["user_id"] == uid)       # row-level security
        match = lambda r: visible(r) and all(str(r[k]) == v[3:] for k, v in q.items() if v.startswith("eq."))
        cols = [c.split(":") for c in q.get("select", "*").split(",")]
        shape = lambda r: r if cols == [["*"]] else {c[0]: r[c[-1]] for c in cols}
        prefer = req.headers.get("prefer", "")
        if req.method == "GET":
            return httpx.Response(200, json=[shape(r) for r in rows if match(r)])
        if req.method == "DELETE":
            self.tables[table] = [r for r in rows if not match(r)]
            return httpx.Response(204)
        if not visible(body):
            return httpx.Response(403, json={"code": "42501", "message": "new row violates row-level security policy"})
        if table == "holdings":
            body = {"id": str(uuid.uuid4()), **body}
        keys = q.get("on_conflict", "").split(",") if q.get("on_conflict") else []
        dup = next((r for r in rows if keys and all(r[k] == body[k] for k in keys)), None)
        if dup and "merge-duplicates" in prefer:
            dup.update(body)
        elif not dup:
            rows.append(body)
        return httpx.Response(201, json=[shape(body)]) if "return=representation" in prefer else httpx.Response(201)


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabase()
    monkeypatch.setattr(supa, "URL", "https://fake.supabase.co")
    monkeypatch.setattr(supa, "KEY", ANON)
    monkeypatch.setattr(supa, "SERVICE_KEY", SERVICE)
    monkeypatch.setattr(supa, "_client", httpx.Client(transport=httpx.MockTransport(f.handler)))
    monkeypatch.setattr(supa, "_users", {})
    for k in [k for k in main._mem if k.startswith("state:")]:
        main._mem.pop(k)
    return f


def signed_up(email, password="correct horse"):
    c = TestClient(app)
    r = c.post("/api/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 200 and r.json()["user"]["email"] == email
    return c


def test_app_is_open_without_supabase():
    c = TestClient(app)
    assert c.get("/api/status").json()["auth"] is False
    assert c.get("/api/watchlist").status_code == 200
    assert c.post("/api/auth/login", json={"email": "a@b.co", "password": "x"}).status_code == 404


def test_api_needs_a_signed_in_user(fake):
    c = TestClient(app)
    assert c.get("/api/status").json()["auth"] is True                       # the page needs this to know to show a login
    assert c.get("/").status_code == 200 and c.get("/static/app.js").status_code == 200
    for path in ("/api/screener", "/api/stock/TCS", "/api/watchlist", "/api/portfolio", "/api/search?q=tcs", "/api/auth/me"):
        assert c.get(path).status_code == 401, path
    assert c.post("/api/watchlist/TCS").status_code == 401 and fake.tables["watchlist"] == []
    c.cookies.set("sp_at", "not-a-token")
    assert c.get("/api/watchlist").status_code == 401


def test_signup_login_and_wrong_password(fake):
    c = signed_up("a@example.com")
    assert c.get("/api/auth/me").json()["user"]["email"] == "a@example.com"
    assert c.get("/api/stock/TCS").status_code == 200
    r = TestClient(app).post("/api/auth/login", json={"email": "a@example.com", "password": "wrong"})
    assert r.status_code == 400 and r.json()["detail"] == "Invalid login credentials" and "sp_at" not in r.cookies
    c2 = TestClient(app)
    r = c2.post("/api/auth/login", json={"email": "a@example.com", "password": "correct horse"})
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower() and c2.get("/api/watchlist").status_code == 200
    assert TestClient(app).post("/api/auth/login", json={"email": "nope", "password": ""}).status_code == 400


def test_signup_that_needs_email_confirmation(fake):
    fake.confirm_email = True
    c = TestClient(app)
    r = c.post("/api/auth/signup", json={"email": "new@example.com", "password": "correct horse"})
    assert r.json() == {"confirm": True} and "sp_at" not in c.cookies and c.get("/api/watchlist").status_code == 401


def test_watchlist_and_portfolio_are_stored_per_user(fake):
    a, b = signed_up("a@example.com"), signed_up("b@example.com")
    a.post("/api/watchlist/INFY"); a.post("/api/watchlist/INFY"); a.post("/api/watchlist/M&M")
    assert [r["symbol"] for r in a.get("/api/watchlist").json()] == ["INFY.NS", "M&M.NS"]
    assert b.get("/api/watchlist").json() == []                                # another account sees none of it
    assert a.delete("/api/watchlist/M&M").json() == ["INFY.NS"] and len(fake.tables["watchlist"]) == 1

    assert a.post("/api/portfolio", json={"symbol": "x", "qty": -1, "price": 1, "date": "2024-01-01"}).status_code == 400
    h = a.post("/api/portfolio", json={"symbol": "TCS", "qty": 10, "price": 3000, "date": "2024-01-15", "note": "thesis"}).json()
    assert h["symbol"] == "TCS.NS" and h["date"] == "2024-01-15" and uuid.UUID(h["id"])
    assert fake.tables["holdings"][0]["buy_date"] == "2024-01-15"
    p = a.get("/api/portfolio").json()
    assert p["totals"]["invested"] == 30000 and p["holdings"][0]["note"] == "thesis" and p["holdings"][0]["nifty_pct"] is not None
    assert b.get("/api/portfolio").json()["holdings"] == []
    assert b.delete("/api/portfolio/" + h["id"]).status_code == 200 and len(fake.tables["holdings"]) == 1   # not b's to delete
    assert a.delete("/api/portfolio/not-an-id").status_code == 404
    a.delete("/api/portfolio/" + h["id"])
    assert a.get("/api/portfolio").json()["holdings"] == [] and fake.tables["holdings"] == []


def test_supabase_mode_leaves_the_json_files_alone(fake):
    before = (data.HOME / "watchlist.json").read_text() if (data.HOME / "watchlist.json").exists() else None
    signed_up("a@example.com").post("/api/watchlist/WIPRO")
    after = (data.HOME / "watchlist.json").read_text() if (data.HOME / "watchlist.json").exists() else None
    assert before == after and fake.tables["watchlist"][0]["symbol"] == "WIPRO.NS"


def test_token_is_checked_with_supabase_once_then_cached(fake):
    c = signed_up("a@example.com")
    for _ in range(3):
        assert c.get("/api/search?q=tcs").status_code == 200
    assert fake.calls.count(("GET", "/auth/v1/user")) == 1


def test_expired_session_is_renewed_with_the_refresh_cookie(fake):
    c = signed_up("a@example.com")
    c.post("/api/watchlist/ITC")
    c.cookies.delete("sp_at")                                                  # what the browser does when the hour is up
    assert c.get("/api/watchlist").status_code == 401
    assert c.post("/api/auth/refresh").status_code == 200
    assert [r["symbol"] for r in c.get("/api/watchlist").json()] == ["ITC.NS"]
    expired = fake.token(next(iter(fake.users.values()))["id"], ttl=-5)
    stale = TestClient(app)
    stale.cookies.set("sp_at", expired)
    assert stale.get("/api/watchlist").status_code == 401                      # an out-of-date token is refused


def test_refresh_with_a_dead_token_ends_the_session(fake):
    c = signed_up("a@example.com")
    fake.refresh.clear()
    r = c.post("/api/auth/refresh")
    assert r.status_code == 401 and 'sp_at=""' in r.headers["set-cookie"] and "sp_at" not in c.cookies
    assert TestClient(app).post("/api/auth/refresh").status_code == 401        # no cookie at all


def test_logout(fake):
    c = signed_up("a@example.com")
    assert c.post("/api/auth/logout").status_code == 200 and ("POST", "/auth/v1/logout") in fake.calls
    assert "sp_at" not in c.cookies and c.get("/api/watchlist").status_code == 401


def test_emailed_link_session_and_new_password(fake):
    signed_up("a@example.com")
    s = fake.session(fake.users["a@example.com"])
    c = TestClient(app)
    assert c.post("/api/auth/session", json={"access_token": "junk", "refresh_token": "junk"}).status_code == 401
    assert c.post("/api/auth/session", json={"access_token": s["access_token"], "refresh_token": s["refresh_token"]}).status_code == 200
    assert c.get("/api/auth/me").json()["user"]["email"] == "a@example.com"
    assert c.post("/api/auth/password", json={"password": "a brand new one"}).status_code == 200
    assert TestClient(app).post("/api/auth/login", json={"email": "a@example.com", "password": "a brand new one"}).status_code == 200
    assert TestClient(app).post("/api/auth/password", json={"password": "x"}).status_code == 401
    assert TestClient(app).post("/api/auth/recover", json={"email": "a@example.com"}).json() == {"sent": True}


def test_signal_history_goes_to_supabase_with_a_service_key(fake, monkeypatch):
    rows = [{"symbol": "TCS.NS", "name": "TCS", "signals": {h: {"signal": "HOLD", "score": 50} for h in main.HORIZONS}}]
    assert main._record_signals(rows) == []
    rows[0]["signals"]["long"] = {"signal": "BUY", "score": 70}
    changes = main._record_signals(rows)
    assert changes[0]["from"] == "HOLD" and changes[0]["to"] == "BUY"
    stored = fake.tables["app_state"]
    assert len(stored) == 1 and stored[0]["key"] == f"signal_history_{data.mode()}" and stored[0]["data"]["last"]["TCS.NS"]["long"] == "BUY"
    assert main._symbol_changes("TCS.NS")[0]["to"] == "BUY"
    a = signed_up("a@example.com")
    assert a.get("/api/stock/TCS").json()["history"][0]["to"] == "BUY"
    assert fake.rest(httpx.Request("GET", "https://x/rest/v1/app_state"), "app_state", {}, {}, a.cookies["sp_at"]).json() == []   # users can't read it

    monkeypatch.setattr(supa, "SERVICE_KEY", "")                               # no service key: the file store is used, as before
    main._record_signals(rows)
    assert len(fake.tables["app_state"]) == 1 and (data.HOME / f"signal_history_{data.mode()}.json").exists()


def test_missing_tables_and_outages_give_a_clear_error(fake, monkeypatch):
    c = signed_up("a@example.com")
    del fake.tables["watchlist"]
    r = c.get("/api/watchlist")
    assert r.status_code == 500 and "supabase/schema.sql" in r.json()["detail"]

    def down(req):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(supa, "_client", httpx.Client(transport=httpx.MockTransport(down)))
    assert c.get("/api/portfolio").status_code == 502
    assert main._state_load("signal_history_x", {"fallback": True}) == {"fallback": True}     # a screener run survives an outage
