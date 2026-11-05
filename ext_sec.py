"""وحدة ملحقة: الأمان المتقدم — المصادقة الثنائية (TOTP RFC 6238)، سجل إجراءات غير قابل للتعديل (محفّزات قاعدة البيانات)،
تسجيل الاطلاع ومحاولات الوصول غير المصرح بها، رموز JWT (OAuth2 client-credentials)، والتحكم بالوصول بالسمات (ABAC) الصارم،
ووثيقة OpenAPI المولَّدة من المسارات الفعلية.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import struct
import threading
import time
import urllib.parse
from datetime import datetime, timedelta

TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT,'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT,'audit_log is append-only'); END;
"""

ISSUER = "acp"


def b32_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_at(secret, counter, digits=6):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits).zfill(digits)


def totp_verify(secret, code, t=None, window=1, step=30):
    """يُرجع العدّاد الذي طابق (لمنع إعادة استخدام الرمز) أو None."""
    if not code or not re.fullmatch(r"\d{6}", str(code).strip()):
        return None
    c0 = int((t if t is not None else time.time()) // step)
    for off in range(-window, window + 1):
        if hmac.compare_digest(totp_at(secret, c0 + off), str(code).strip()):
            return c0 + off
    return None


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def install(S):
    route, ApiError = S.route, S.ApiError
    f_text, now, audit = S.f_text, S.now, S.audit
    S.MIGRATIONS.setdefault("user_prefs", {})
    S.EXT_INIT.append(lambda conn: conn.executescript(TRIGGERS))
    S.SETTING_DEFAULTS.update({"abac_strict_secret": "0", "mfa_required_roles": "admin,manager"})
    S.SETTING_TEXT_KEYS.add("mfa_required_roles")
    tickets, used_counters = {}, {}
    lock = threading.Lock()

    # ───────────── ABAC الصارم: يُقرأ مرة لكل طلب ─────────────
    def enrich(conn, u):
        r = conn.execute("SELECT v FROM settings WHERE k='abac_strict_secret'").fetchone()
        u["strict_secret"] = bool(r and r[0] == "1")
    S.EXT_ENRICH.append(enrich)

    # ───────────── المصادقة الثنائية ─────────────
    def second_factor_ok(user, code):
        """يُستخدم عند التوقيع/الإتلاف: من فعّل المصادقة الثنائية لزمه رمز TOTP أو رمز استرداد."""
        if not user.get("mfa_enabled"):
            return True
        return bool(check_code(S.connect(), user, code, consume_recovery=False))

    def check_code(conn, u, code, consume_recovery=True):
        counter = totp_verify(u["totp_secret"], code)
        with lock:
            if counter is not None:
                if used_counters.get(u["id"], -1) >= counter:
                    return False  # الرمز استُخدم سابقًا (منع إعادة التشغيل)
                used_counters[u["id"]] = counter
                return True
        if consume_recovery and code and u["mfa_recovery"]:
            hashes = json.loads(u["mfa_recovery"])
            h = S.sha(str(code).strip().lower())
            if h in hashes:
                hashes.remove(h)
                conn.execute("UPDATE users SET mfa_recovery=? WHERE id=?", (json.dumps(hashes), u["id"]))
                return True
        return False

    def require_second_factor(ctx):
        if not ctx.user.get("mfa_enabled"):
            return
        otp = ctx.body.get("otp")
        counter = totp_verify(ctx.user["totp_secret"], otp)
        with lock:
            if counter is None or used_counters.get(ctx.user["id"], -1) >= counter:
                raise ApiError(400, "يلزم رمز المصادقة الثنائية (TOTP) الصحيح لتأكيد هذا الإجراء")
            used_counters[ctx.user["id"]] = counter
    S.require_second_factor = require_second_factor

    def login_hook(ctx, u):
        if not u["mfa_enabled"]:
            return None
        ticket = secrets.token_urlsafe(24)
        with lock:
            for k in [k for k, v in tickets.items() if v["exp"] < time.time()]:
                tickets.pop(k, None)
            tickets[ticket] = {"uid": u["id"], "exp": time.time() + 300, "tries": 0}
        return 200, {"mfa_required": True, "ticket": ticket}
    S.EXT_LOGIN.append(login_hook)

    @route("POST", r"/api/login/mfa", auth=False)
    def login_mfa(ctx):
        t = ctx.body.get("ticket")
        with lock:
            tk = tickets.get(t)
            if not tk or tk["exp"] < time.time():
                raise ApiError(401, "انتهت صلاحية خطوة التحقق، سجّل الدخول من جديد")
            tk["tries"] += 1
            if tk["tries"] > 5:
                tickets.pop(t, None)
                raise ApiError(429, "محاولات كثيرة، سجّل الدخول من جديد")
        u = ctx.conn.execute("SELECT * FROM users WHERE id=? AND active=1", (tk["uid"],)).fetchone()
        ctx.user = {"username": u["username"] if u else None, "id": u["id"] if u else None}
        if not u or not check_code(ctx.conn, dict(u), ctx.body.get("code")):
            audit(ctx, "mfa_failed")
            raise ApiError(401, "رمز التحقق غير صحيح")
        with lock:
            tickets.pop(t, None)
        return S.issue_session(ctx, u)

    def required_for(conn, role):
        r = conn.execute("SELECT v FROM settings WHERE k='mfa_required_roles'").fetchone()
        roles = [x.strip() for x in (r[0] if r else S.SETTING_DEFAULTS["mfa_required_roles"]).split(",") if x.strip()]
        return role in roles

    @route("GET", r"/api/me/mfa")
    def mfa_status(ctx):
        u = ctx.user
        return 200, {"enabled": bool(u.get("mfa_enabled")), "required": required_for(ctx.conn, u["role"]),
                     "recovery_left": len(json.loads(u["mfa_recovery"] or "[]"))}

    @route("POST", r"/api/me/mfa/setup")
    def mfa_setup(ctx):
        if ctx.user.get("mfa_enabled"):
            raise ApiError(400, "المصادقة الثنائية مفعّلة بالفعل")
        secret = b32_secret()
        ctx.conn.execute("UPDATE users SET totp_secret=? WHERE id=?", (secret, ctx.user["id"]))
        uri = f"otpauth://totp/{urllib.parse.quote(ISSUER)}:{urllib.parse.quote(ctx.user['username'])}?secret={secret}&issuer={ISSUER}&digits=6&period=30"
        return 200, {"secret": secret, "uri": uri, "qr_svg": S.qr_svg(uri) if hasattr(S, "qr_svg") else None}

    @route("POST", r"/api/me/mfa/enable")
    def mfa_enable(ctx):
        u = ctx.conn.execute("SELECT * FROM users WHERE id=?", (ctx.user["id"],)).fetchone()
        if u["mfa_enabled"] or not u["totp_secret"]:
            raise ApiError(400, "ابدأ الإعداد أولًا")
        counter = totp_verify(u["totp_secret"], ctx.body.get("code"))
        if counter is None:
            raise ApiError(400, "الرمز غير صحيح — تأكد من ضبط الوقت في هاتفك")
        codes = [secrets.token_hex(4) for _ in range(8)]
        ctx.conn.execute("UPDATE users SET mfa_enabled=1, mfa_recovery=? WHERE id=?", (json.dumps([S.sha(c) for c in codes]), u["id"]))
        with lock:
            used_counters[u["id"]] = counter
        audit(ctx, "mfa_enabled", "user", u["id"])
        return 200, {"recovery_codes": codes}

    @route("POST", r"/api/me/mfa/disable")
    def mfa_disable(ctx):
        u = ctx.conn.execute("SELECT * FROM users WHERE id=?", (ctx.user["id"],)).fetchone()
        if not u["mfa_enabled"]:
            raise ApiError(400, "المصادقة الثنائية غير مفعّلة")
        if not S.verify_password(str(ctx.body.get("password", "")), u["password_hash"]) or not check_code(ctx.conn, dict(u), ctx.body.get("code")):
            raise ApiError(400, "كلمة المرور أو الرمز غير صحيح")
        ctx.conn.execute("UPDATE users SET mfa_enabled=0, totp_secret=NULL, mfa_recovery=NULL WHERE id=?", (u["id"],))
        audit(ctx, "mfa_disabled", "user", u["id"])
        return 200, {"ok": True}

    @route("POST", r"/api/admin/users/(?P<id>\d+)/mfa/reset", roles=("admin",))
    def mfa_reset(ctx):
        uid = int(ctx.params["id"])
        ctx.conn.execute("UPDATE users SET mfa_enabled=0, totp_secret=NULL, mfa_recovery=NULL WHERE id=?", (uid,))
        audit(ctx, "mfa_reset", "user", uid, "إعادة ضبط المصادقة الثنائية بواسطة المدير")
        return 200, {"ok": True}

    # ───────────── تسجيل الاطلاع (من فتح، متى، من أي جهاز) ─────────────
    def view_logger(ctx, c):
        since = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        if ctx.conn.execute("SELECT 1 FROM audit_log WHERE action='view' AND user_id=? AND entity_id=? AND at>=? LIMIT 1",
                            (ctx.user["id"], c["id"], since)).fetchone():
            return  # لا نكرر سجل الاطلاع نفسه خلال 10 دقائق
        ua = (ctx.h.headers.get("User-Agent") or "")[:100]
        audit(ctx, "view", "correspondence", c["id"], f"{c['ref_no']} — {ua}")
    S.EXT_DETAIL.append(view_logger)

    # ───────────── JWT (OAuth2 client-credentials) ─────────────
    def jwt_key():
        return hmac.new(S.signing_key() if hasattr(S, "signing_key") else b"k", b"acp-jwt", hashlib.sha256).digest()

    def jwt_make(payload):
        head = b64u(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
        body = b64u(json.dumps(payload, separators=(",", ":")).encode())
        sig = b64u(hmac.new(jwt_key(), f"{head}.{body}".encode(), hashlib.sha256).digest())
        return f"{head}.{body}.{sig}"

    def jwt_parse(tok):
        try:
            head, body, sig = tok.split(".")
            good = b64u(hmac.new(jwt_key(), f"{head}.{body}".encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(good, sig) or json.loads(b64u_dec(head)).get("alg") != "HS256":
                return None
            p = json.loads(b64u_dec(body))
            return p if p.get("exp", 0) > time.time() else None
        except Exception:
            return None

    @route("POST", r"/api/oauth/token", auth=False)
    def oauth_token(ctx):
        b = ctx.body
        if b.get("grant_type") != "client_credentials":
            raise ApiError(400, "unsupported_grant_type")
        r = ctx.conn.execute("SELECT k.name AS key_name, u.* FROM api_keys k JOIN users u ON u.id=k.user_id "
                             "WHERE k.key_hash=? AND k.active=1 AND u.active=1", (S.sha(str(b.get("client_secret", ""))),)).fetchone()
        if not r:
            raise ApiError(401, "invalid_client")
        exp = int(time.time()) + 3600
        ctx.user = {"username": r["username"], "id": r["id"], "via": "OAuth:" + r["key_name"]}
        audit(ctx, "oauth_token", "user", r["id"])
        return 200, {"access_token": jwt_make({"sub": r["id"], "kn": r["key_name"], "iat": int(time.time()), "exp": exp}), "token_type": "Bearer", "expires_in": 3600}

    def jwt_auth(handler, conn):
        a = handler.headers.get("Authorization") or ""
        if not a.startswith("Bearer ") or a.count(".") != 2:
            return None
        p = jwt_parse(a[7:])
        if not p:
            return None
        r = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (p["sub"],)).fetchone()
        if not r:
            return None
        u = dict(r)
        u["via"] = "JWT:" + str(p.get("kn", ""))
        return u
    S.EXT_AUTH.append(jwt_auth)

    # ───────────── OpenAPI (مولَّدة من المسارات الفعلية) ─────────────
    @route("GET", r"/api/openapi.json", auth=False)
    def openapi(ctx):
        paths = {}
        for method, rx, fn, auth, roles in S.ROUTES:
            path = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", rx.pattern)
            if "(" in path or "|" in path:
                path = re.sub(r"\(\?P<kind>[^)]*\)", "{kind}", path)
            params = [{"name": n, "in": "path", "required": True, "schema": {"type": "string"}} for n in re.findall(r"\{(\w+)\}", path)]
            op = {"summary": (fn.__doc__ or fn.__name__).strip().split("\n")[0], "operationId": f"{method.lower()}_{fn.__name__}",
                  "parameters": params, "responses": {"200": {"description": "نجاح"}, "401": {"description": "يلزم تسجيل الدخول"}, "403": {"description": "لا صلاحية"}}}
            if auth:
                op["security"] = [{"bearer": []}, {"apikey": []}]
            if roles:
                op["x-roles"] = list(roles)
            paths.setdefault(path, {})[method.lower()] = op
        return 200, {"openapi": "3.0.3", "info": {"title": "منصة الاتصالات الإدارية — API", "version": "2.1",
                     "description": "مولَّدة آليًا من المسارات الفعلية. المصادقة: جلسة Bearer، أو مفتاح X-API-Key، أو JWT من /api/oauth/token."},
                     "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}, "apikey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}},
                     "paths": dict(sorted(paths.items()))}
