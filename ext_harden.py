"""وحدة ملحقة: تحصين المرحلة 0 (انظر docs/SECURITY_PLAN.md).

كل ضابط يُضبط من إعدادات النظام وقيمته الافتراضية تحافظ على السلوك السابق، عدا ما هو أمان بحت:
  • سياسة كلمات مرور قابلة للضبط (طول أدنى، حظر الشائعة، حروف كبيرة/صغيرة)   pw_min_length / pw_block_common / pw_require_case
  • حدّ معدّل لكل IP على الدخول والبحث/التصدير                                    rate_login_per_min / rate_heavy_per_min  (0 = بلا حد)
  • انتهاء الجلسة عند الخمول                                                       idle_minutes (0 = معطّل)
  • ترويسات: Permissions-Policy وCOOP وCORP دائمًا؛ HSTS عند HTTPS مباشر أو ACP_HSTS=1 (خلف وكيل TLS)
  • فحص دوري لسلسلة تجزئة سجل الإجراءات مع تنبيه المديرين                          chain_check_hours (0 = معطّل)
  • تصدير سجل الإجراءات إلى Syslog (UDP/RFC5424) عبر ACP_SYSLOG_HOST وACP_SYSLOG_PORT
"""
import os
import re
import socket
import threading
import time
from collections import deque
from datetime import datetime

COMMON = {"password", "password1", "password123", "passw0rd", "passw0rd1", "passw0rd123", "admin123", "admin12345", "qwerty123",
          "qwertyuiop1", "letmein123", "welcome123", "welcome1", "iloveyou1", "abc12345", "abcd1234", "12345678", "123456789",
          "1234567890", "11111111", "00000000", "p@ssw0rd", "p@ssword1", "changeme123", "saudi123", "riyadh123", "ksa12345"}
DEFAULTS = {"pw_min_length": "8", "pw_block_common": "0", "pw_require_case": "0", "rate_login_per_min": "60",
            "rate_heavy_per_min": "240", "idle_minutes": "0", "chain_check_hours": "24"}
BOUNDS = {"pw_min_length": (8, 64)}
PASSIVE = ("/api/notifications", "/api/events", "/api/health")  # نشاط آلي لا يُعدّ استخدامًا من المستخدم


def install(S):
    route, ApiError, now = S.route, S.ApiError, S.now
    S.SETTING_DEFAULTS.update(DEFAULTS)
    S.EXT_SCHEMAS.append("CREATE TABLE IF NOT EXISTS session_activity(token_hash TEXT PRIMARY KEY, last_seen TEXT NOT NULL);")

    # ───── ذاكرة مؤقتة قصيرة للإعدادات (تُقرأ في كل طلب) ─────
    cache = {"t": 0.0, "v": dict(DEFAULTS)}
    cache_lock = threading.Lock()

    def cfg(conn=None):
        if time.time() - cache["t"] < 5:
            return cache["v"]
        c = conn or S.connect()
        try:
            st = S.get_settings(c)
            cache["v"] = {k: st.get(k, DEFAULTS[k]) for k in DEFAULTS}
            cache["t"] = time.time()
        finally:
            if conn is None:
                c.close()
        return cache["v"]

    def num(v, d=0):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return d

    S.EXT_INIT.append(lambda conn: cache.update(t=0.0))

    # إبطال الذاكرة المؤقتة فور تعديل الإعدادات
    orig_settings_put = None
    for i, (m, rx, fn, a, r) in enumerate(S.ROUTES):
        if m == "PUT" and rx.pattern == r"/api/admin/settings":
            orig_settings_put = (i, fn)

    if orig_settings_put:
        idx, fn = orig_settings_put

        def settings_put(ctx, _fn=fn):
            b = ctx.body
            for k, (lo, hi) in BOUNDS.items():
                if k in b and not (lo <= num(b[k], -1) <= hi):
                    raise ApiError(400, f"قيمة {k} يجب أن تكون بين {lo} و{hi}")
            res = _fn(ctx)
            cache["t"] = 0.0
            return res
        m, rx, _, a, r = S.ROUTES[idx]
        S.ROUTES[idx] = (m, rx, settings_put, a, r)

    # ───── سياسة كلمات المرور ─────
    base_policy = S.check_password_policy

    def policy(pw):
        base_policy(pw)
        c = cfg()
        if len(pw) < num(c["pw_min_length"], 8):
            raise ApiError(400, f"كلمة المرور يجب ألا تقل عن {num(c['pw_min_length'], 8)} حرفًا")
        if num(c["pw_require_case"]) and not (re.search(r"[a-z]", pw) and re.search(r"[A-Z]", pw)):
            raise ApiError(400, "كلمة المرور يجب أن تحتوي حروفًا كبيرة وصغيرة")
        if num(c["pw_block_common"]) and (pw.lower() in COMMON or re.fullmatch(r"(.)\1+", pw)):
            raise ApiError(400, "كلمة المرور شائعة ويسهل تخمينها؛ اختر غيرها")
    S.check_password_policy = policy

    # ───── البوابة: حدّ المعدّل + خمول الجلسة ─────
    buckets, blk = {}, threading.Lock()

    def over_limit(key, limit):
        t = time.time()
        with blk:
            q = buckets.setdefault(key, deque())
            while q and q[0] < t - 60:
                q.popleft()
            if len(q) >= limit:
                return True
            q.append(t)
            if len(buckets) > 5000:  # تنظيف المفاتيح الخاملة
                for k in [k for k, v in buckets.items() if not v or v[-1] < t - 60]:
                    buckets.pop(k, None)
        return False

    def gate(handler, conn, path):
        c = cfg(conn)
        ip = handler.client_address[0]
        m = handler.command
        if m == "POST" and path in ("/api/login", "/api/login/mfa"):
            lim = num(c["rate_login_per_min"])
            if lim and over_limit(("login", ip), lim):
                raise ApiError(429, "محاولات كثيرة، أعد المحاولة بعد دقيقة")
        elif path in ("/api/search", "/api/export", "/api/export/xlsx"):
            lim = num(c["rate_heavy_per_min"])
            if lim and over_limit(("heavy", ip), lim):
                raise ApiError(429, "طلبات كثيرة، أعد المحاولة بعد دقيقة")
        idle = num(c["idle_minutes"])
        a = handler.headers.get("Authorization") or ""
        if idle and a.startswith("Bearer "):
            th, t = S.sha(a[7:]), now()
            row = conn.execute("SELECT last_seen FROM session_activity WHERE token_hash=?", (th,)).fetchone()
            if row:
                age = (datetime.strptime(t, "%Y-%m-%d %H:%M:%S") - datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")).total_seconds()
                if age > idle * 60:
                    conn.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
                    conn.execute("DELETE FROM session_activity WHERE token_hash=?", (th,))
                    return
                if age < 30 or path.startswith(PASSIVE):
                    return
            elif path.startswith(PASSIVE):
                return
            conn.execute("INSERT INTO session_activity(token_hash,last_seen) SELECT ?,? WHERE EXISTS(SELECT 1 FROM sessions WHERE token_hash=?) "
                         "ON CONFLICT(token_hash) DO UPDATE SET last_seen=excluded.last_seen", (th, t, th))
    S.EXT_GATE.append(gate)

    # ───── ترويسات الأمان ─────
    S.SEC_HEADERS.update({
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
        "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Resource-Policy": "same-origin"})
    if os.environ.get("ACP_TLS_CERT") or os.environ.get("ACP_HSTS") == "1":
        S.SEC_HEADERS["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # ───── فحص دوري لسلسلة السجل مع تنبيه ─────
    def chain_check(conn):
        hrs = num(cfg(conn)["chain_check_hours"])
        if not hrs or not hasattr(S, "verify_chain"):
            return
        row = conn.execute("SELECT v FROM settings WHERE k='chain_last_check'").fetchone()
        if row and time.time() - float(row[0]) < hrs * 3600:
            return
        conn.execute("INSERT INTO settings(k,v) VALUES('chain_last_check',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(time.time()),))
        conn.execute("DELETE FROM session_activity WHERE token_hash NOT IN (SELECT token_hash FROM sessions)")
        res = S.verify_chain(conn)
        if not res["ok"]:
            for (uid,) in conn.execute("SELECT id FROM users WHERE role='admin' AND active=1").fetchall():
                S.notify(conn, uid, "security", "⚠ تنبيه أمني: سلسلة سجل الإجراءات غير سليمة",
                         f"اكتُشف تلاعب محتمل عند السجل #{res['first_bad_id']}. افتح «سجل الإجراءات» وراجع النسخ الاحتياطية.", None,
                         dedupe=f"chain-bad-{res['first_bad_id']}")
            print(f"[SECURITY] سلسلة سجل الإجراءات غير سليمة عند #{res['first_bad_id']}", flush=True)
    S.EXT_TICK.append(chain_check)

    # ───── تصدير Syslog ─────
    host = os.environ.get("ACP_SYSLOG_HOST")
    if host:
        port = int(os.environ.get("ACP_SYSLOG_PORT") or 514)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        hostname = socket.gethostname()
        clean = lambda v: re.sub(r"[\r\n\x00-\x1f]", " ", "-" if v is None else str(v))

        def to_syslog(rec):
            try:
                msg = (f"<110>1 {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} {hostname} acp - audit - "
                       f"action={clean(rec['action'])} user={clean(rec['username'])} ip={clean(rec['ip'])} entity={clean(rec['entity'])}:{clean(rec['entity_id'])} "
                       f"hash={clean(rec['hash'])[:16]} detail={clean(rec['detail'])[:300]}")
                sock.sendto(msg.encode("utf-8"), (host, port))
            except Exception:
                pass  # التصدير لا يوقف العمل أبدًا
        S.EXT_AUDIT.append(to_syslog)
