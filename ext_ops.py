"""وحدة ملحقة: العمليات — اتفاقيات مستوى الخدمة (SLA) بساعات العمل ومصفوفة التصعيد، قنوات الإشعار (بريد SMTP / بوابة SMS)
مع صندوق صادر وإعادة محاولة، إرسال الصادر بالبريد مع تتبع الحالة، استقبال البريد الوارد (EML / IMAP)، الإشعارات اللحظية (SSE)،
كشف الانحرافات إحصائيًا، والتقارير المجدولة.
"""
import email
import email.policy
import email.utils
import hashlib
import imaplib
import json
import os
import queue
import re
import secrets
import smtplib
import statistics
import threading
import time
import traceback
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from email.message import EmailMessage

SCHEMA = """
CREATE TABLE IF NOT EXISTS sla_policies(class_key TEXT PRIMARY KEY, hours REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, to_addr TEXT NOT NULL, subject TEXT, body TEXT, files TEXT,
  user_id INTEGER, corr_id INTEGER, status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, created_at TEXT NOT NULL, sent_at TEXT);
CREATE INDEX IF NOT EXISTS ix_outbox_status ON outbox(status);
CREATE TABLE IF NOT EXISTS kpi_daily(day TEXT NOT NULL, unit_id INTEGER NOT NULL, open_n INTEGER NOT NULL, overdue_n INTEGER NOT NULL, PRIMARY KEY(day, unit_id));
CREATE TABLE IF NOT EXISTS report_schedules(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'kpi', weekday INTEGER NOT NULL, hour INTEGER NOT NULL DEFAULT 8,
  recipients TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, last_run TEXT, created_by INTEGER);
CREATE TABLE IF NOT EXISTS report_runs(id INTEGER PRIMARY KEY, schedule_id INTEGER, name TEXT NOT NULL, filename TEXT NOT NULL, path TEXT NOT NULL, created_at TEXT NOT NULL);
"""

DEFAULT_SLA = (("very_urgent", 4), ("urgent", 8), ("very_high", 24), ("high", 48))
EMAIL_KINDS = {"assigned", "reply_needed", "transfer", "overdue", "due_soon", "sla_warn", "sla_breach", "wf_step", "wf_rejected", "wf_done", "kpi_alert", "anomaly", "report", "delegation"}
SMS_KINDS = {"sla_breach", "overdue"}
HTML_TAG = re.compile(r"<[^>]+>")


class PseudoCtx:
    """سياق مبسّط للعمليات الخلفية (استقبال البريد…) كي تعمل معها السجلات والأحداث نفسها."""
    def __init__(self, conn, user, ip="system"):
        self.conn, self.user, self.ip, self.body, self.h, self.query, self.params = conn, user, ip, {}, None, {}, {}


def install(S):
    route, ApiError = S.route, S.ApiError
    f_text, f_int, now, audit = S.f_text, S.f_int, S.now, S.audit
    S.EXT_SCHEMAS.append(SCHEMA)
    S.MIGRATIONS["correspondences"]["sla_done_at"] = "TEXT"
    S.SETTING_DEFAULTS.update({"work_days": "6,0,1,2,3", "work_start": "08:00", "work_end": "16:00", "anomaly_sigma": "2"})
    S.SETTING_TEXT_KEYS.update({"work_days", "work_start", "work_end"})
    subs, subs_lock = {}, threading.Lock()

    def init(conn):
        if conn.execute("SELECT COUNT(*) FROM sla_policies").fetchone()[0] == 0:
            for k, h in DEFAULT_SLA:
                conn.execute("INSERT INTO sla_policies(class_key,hours) VALUES(?,?)", (k, h))
    S.EXT_INIT.append(init)

    # ───────────── ساعات العمل والـSLA ─────────────
    def cfg(conn):
        st = S.get_settings(conn)
        hm = lambda s: tuple(int(x) for x in s.split(":"))
        return {"days": {int(x) for x in st["work_days"].split(",") if x.strip() != ""}, "start": hm(st["work_start"]), "end": hm(st["work_end"])}

    def day_window(d, c):
        return d.replace(hour=c["start"][0], minute=c["start"][1], second=0, microsecond=0), d.replace(hour=c["end"][0], minute=c["end"][1], second=0, microsecond=0)

    def add_business_hours(start, hours, c):
        cur, remaining = start, hours * 3600
        for _ in range(800):
            if cur.weekday() in c["days"]:
                ws, we = day_window(cur, c)
                cur = max(cur, ws)
                if cur < we:
                    avail = (we - cur).total_seconds()
                    if remaining <= avail:
                        return cur + timedelta(seconds=remaining)
                    remaining -= avail
            cur = (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return cur

    def business_seconds(a, b, c):
        if b <= a:
            return 0.0
        total, d = 0.0, a.replace(hour=0, minute=0, second=0, microsecond=0)
        while d <= b:
            if d.weekday() in c["days"]:
                ws, we = day_window(d, c)
                lo, hi = max(a, ws), min(b, we)
                if hi > lo:
                    total += (hi - lo).total_seconds()
            d += timedelta(days=1)
        return total
    S.add_business_hours, S.business_seconds = add_business_hours, business_seconds

    def dt(s):
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")

    @S.on("corr_created")
    def _sla_assign(ctx, cid, ref, assignees, reply_users, parent):
        c = ctx.conn.execute("SELECT * FROM correspondences WHERE id=?", (cid,)).fetchone()
        if ctx.user.get("via", "").startswith(("API", "OAuth", "JWT")):
            ctx.conn.execute("UPDATE correspondences SET source='api' WHERE id=?", (cid,))
        if c["status"] == "draft":
            return
        p = ctx.conn.execute("SELECT hours FROM sla_policies WHERE class_key=? AND active=1", (c["priority"],)).fetchone()
        if p:
            due = add_business_hours(dt(c["created_at"]), p[0], cfg(ctx.conn))
            ctx.conn.execute("UPDATE correspondences SET sla_hours=?, sla_due_at=? WHERE id=?", (p[0], due.strftime("%Y-%m-%d %H:%M:%S"), cid))

    @S.on("status_changed")
    def _sla_done(ctx, cid, old, new):
        if new in ("closed", "archived"):
            ctx.conn.execute("UPDATE correspondences SET sla_done_at=COALESCE(sla_done_at,?) WHERE id=?", (now(), cid))
        if old == "draft" and new != "draft":  # المسودة عند تقديمها يبدأ احتساب مهلتها
            c = ctx.conn.execute("SELECT * FROM correspondences WHERE id=?", (cid,)).fetchone()
            p = ctx.conn.execute("SELECT hours FROM sla_policies WHERE class_key=? AND active=1", (c["priority"],)).fetchone()
            if p and not c["sla_due_at"]:
                due = add_business_hours(datetime.now(), p[0], cfg(ctx.conn))
                ctx.conn.execute("UPDATE correspondences SET sla_hours=?, sla_due_at=? WHERE id=?", (p[0], due.strftime("%Y-%m-%d %H:%M:%S"), cid))

    @S.on("replied")
    def _sla_replied(ctx, cid, ref):
        ctx.conn.execute("UPDATE correspondences SET sla_done_at=COALESCE(sla_done_at,?) WHERE id=?", (now(), cid))

    def sla_state(c, conf, now_dt):
        if not c.get("sla_due_at") or c.get("sla_done_at") or c["status"] in ("closed", "archived", "draft"):
            return None
        due = dt(c["sla_due_at"])
        if now_dt > due:
            return "breach"
        frac = business_seconds(dt(c["created_at"]), now_dt, conf) / max(1.0, (c["sla_hours"] or 1) * 3600)
        return "warn" if frac >= 0.75 else "ok"

    def list_hook(ctx, items):
        conf, n = cfg(ctx.conn), datetime.now()
        for it in items:
            it["sla_state"] = sla_state(it, conf, n)
    S.EXT_LIST.append(list_hook)

    def sla_escalation(conn):
        conf, n = cfg(conn), datetime.now()
        out = {"warn": 0, "breach": 0}
        rows = conn.execute("SELECT * FROM correspondences WHERE sla_due_at IS NOT NULL AND sla_done_at IS NULL "
                            "AND status NOT IN('closed','archived','draft')").fetchall()
        for c in rows:
            st = sla_state(dict(c), conf, n)
            if st not in ("warn", "breach"):
                continue
            targets = {r[0] for r in conn.execute("SELECT to_user FROM transfers WHERE corr_id=? AND status='pending'", (c["id"],))}
            if c["assigned_to"]:
                targets.add(c["assigned_to"])
            targets = targets or {c["created_by"]}
            subj = c["subject"] if c["conf_level"] == 0 else None
            for uid in targets:  # تنبيه الموظف عند 75٪
                if S.notify(conn, uid, "sla_warn" if st == "warn" else "sla_breach",
                            ("اقترب موعد SLA: " if st == "warn" else "تجاوزت مهلة SLA: ") + c["ref_no"], subj, c["id"], dedupe=f"sla:{'75' if st == 'warn' else '100u'}:{c['id']}:{uid}"):
                    out[st] += 1
            if st == "breach":  # التصعيد للمدير المباشر عند 100٪
                mgrs = {r[0] for r in conn.execute(
                    "SELECT u.id FROM users u JOIN corr_units cu ON cu.dept_id=u.department_id WHERE cu.corr_id=? AND u.role='manager' AND u.active=1", (c["id"],))}
                mgrs |= {r[0] for r in conn.execute("SELECT id FROM users WHERE role='admin' AND active=1")}
                for uid in mgrs - targets:
                    S.notify(conn, uid, "sla_breach", "تصعيد: تجاوز SLA " + c["ref_no"], subj, c["id"], dedupe=f"sla:100m:{c['id']}:{uid}")
        return out
    S.EXT_TICK.append(sla_escalation)

    @route("GET", r"/api/admin/sla", roles=("admin",))
    def sla_get(ctx):
        st = S.get_settings(ctx.conn)
        return 200, {"policies": {r["class_key"]: r["hours"] for r in ctx.conn.execute("SELECT * FROM sla_policies WHERE active=1")},
                     "work_days": st["work_days"], "work_start": st["work_start"], "work_end": st["work_end"]}

    @route("PUT", r"/api/admin/sla", roles=("admin",))
    def sla_put(ctx):
        b = ctx.body
        with S.tx(ctx.conn):
            for k, h in (b.get("policies") or {}).items():
                if k not in S.classes(ctx.conn, False):
                    raise ApiError(400, f"تصنيف غير معروف: {k}")
                if h in (None, "", 0):
                    ctx.conn.execute("DELETE FROM sla_policies WHERE class_key=?", (k,))
                else:
                    if not isinstance(h, (int, float)) or not 0 < h <= 2000:
                        raise ApiError(400, "المهلة بالساعات بين 0 و2000")
                    ctx.conn.execute("INSERT INTO sla_policies(class_key,hours) VALUES(?,?) ON CONFLICT(class_key) DO UPDATE SET hours=excluded.hours, active=1", (k, h))
            for k in ("work_days", "work_start", "work_end"):
                if k in b:
                    v = str(b[k])
                    ok = re.fullmatch(r"[0-6](,[0-6])*", v) if k == "work_days" else re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v)
                    if not ok:
                        raise ApiError(400, f"قيمة غير صالحة: {k}")
                    ctx.conn.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
            audit(ctx, "sla_update", detail=json.dumps(b, ensure_ascii=False)[:200])
        return sla_get(ctx)

    # ───────────── الإشعارات اللحظية (SSE) ─────────────
    def publish(user_id, event):
        with subs_lock:
            for q in list(subs.get(user_id, ())):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    S.publish = publish
    S.connected_users = lambda: [u for u, qs in list(subs.items()) if qs]

    @route("GET", r"/api/events")
    def events(ctx):
        uid = ctx.user["id"]
        q = queue.Queue(maxsize=100)
        with subs_lock:
            lst = subs.setdefault(uid, [])
            if len(lst) >= 5:
                lst.pop(0)
            lst.append(q)

        def gen():
            try:
                yield b"retry: 5000\n\nevent: ready\ndata: {}\n\n"
                while True:
                    try:
                        ev = q.get(timeout=15)
                        name = ev.get("_event", "notification") if isinstance(ev, dict) else "notification"
                        payload = {k: v for k, v in ev.items() if k != "_event"} if isinstance(ev, dict) else ev
                        yield f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
                    except queue.Empty:
                        yield b": ping\n\n"
            finally:
                with subs_lock:
                    if q in subs.get(uid, []):
                        subs[uid].remove(q)
        return S.Stream(gen())

    # ───────────── قنوات الإشعار: بريد وSMS ─────────────
    def smtp_on():
        return bool(os.environ.get("ACP_SMTP_HOST"))

    def sms_on():
        return bool(os.environ.get("ACP_SMS_URL"))

    def enqueue(conn, kind, to, subject, body, user_id=None, corr_id=None, files=None):
        return conn.execute("INSERT INTO outbox(kind,to_addr,subject,body,files,user_id,corr_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                            (kind, to, subject, body, json.dumps(files) if files else None, user_id, corr_id, now())).lastrowid
    S.enqueue_outbox = enqueue

    def channel_hook(conn, user_id, kind, title, body, corr_id):
        publish(user_id, {"kind": kind, "title": title, "body": body, "corr_id": corr_id})
        u = conn.execute("SELECT email, phone FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
        if not u:
            return
        pr = conn.execute("SELECT email_on, sms_on FROM user_prefs WHERE user_id=?", (user_id,)).fetchone()
        e_on, s_on = (pr["email_on"], pr["sms_on"]) if pr else (1, 0)
        safe_body = body
        if corr_id:  # لا يُرسل موضوع مراسلة سرية خارج المنصة
            c = conn.execute("SELECT conf_level FROM correspondences WHERE id=?", (corr_id,)).fetchone()
            if c and c["conf_level"] > 0:
                safe_body = "مراسلة سرية — افتح المنصة للاطلاع"
        if smtp_on() and u["email"] and e_on and kind in EMAIL_KINDS:
            enqueue(conn, "email", u["email"], title, safe_body or title, user_id, corr_id)
        if sms_on() and u["phone"] and s_on and kind in SMS_KINDS:
            enqueue(conn, "sms", u["phone"], None, title, user_id, corr_id)
    S.EXT_NOTIFY.append(channel_hook)

    def load_files(conn, files):
        out = []
        for f in files or []:
            if f["type"] == "att":
                a = conn.execute("SELECT * FROM attachments WHERE id=? AND removed_at IS NULL", (f["id"],)).fetchone()
                if not a:
                    continue
                with open(os.path.join(S.COLD if a["cold"] else S.UPLOADS, a["stored_name"]), "rb") as fh:
                    data = fh.read()
                if a["enc"]:
                    data = S.FILE_CODEC["dec"](data)
                out.append((a["filename"], data))
            else:
                with open(f["path"], "rb") as fh:
                    out.append((f["name"], fh.read()))
        return out

    def send_email_row(conn, r):
        msg = EmailMessage()
        msg["From"] = os.environ.get("ACP_SMTP_FROM", "noreply@localhost")
        msg["To"], msg["Subject"] = r["to_addr"], r["subject"] or "إشعار من منصة الاتصالات الإدارية"
        msg["Message-ID"] = email.utils.make_msgid(domain="acp.local")
        msg.set_content(r["body"] or "")
        for name, data in load_files(conn, json.loads(r["files"]) if r["files"] else None):
            ext = os.path.splitext(name)[1].lower()
            mt = {".pdf": ("application", "pdf"), ".png": ("image", "png"), ".jpg": ("image", "jpeg")}.get(ext, ("application", "octet-stream"))
            msg.add_attachment(data, maintype=mt[0], subtype=mt[1], filename=name)
        mode = os.environ.get("ACP_SMTP_TLS", "starttls")
        port = int(os.environ.get("ACP_SMTP_PORT", "587" if mode == "starttls" else "465" if mode == "ssl" else "25"))
        cls = smtplib.SMTP_SSL if mode == "ssl" else smtplib.SMTP
        with cls(os.environ["ACP_SMTP_HOST"], port, timeout=15) as s:
            if mode == "starttls":
                s.starttls()
            if os.environ.get("ACP_SMTP_USER"):
                s.login(os.environ["ACP_SMTP_USER"], os.environ.get("ACP_SMTP_PASS", ""))
            s.send_message(msg)

    def send_sms_row(r):
        body = json.dumps({"to": r["to_addr"], "message": r["body"], "sender": os.environ.get("ACP_SMS_SENDER", "ACP")}).encode()
        req = urllib.request.Request(os.environ["ACP_SMS_URL"], data=body, method="POST", headers={
            "Content-Type": "application/json", **({"Authorization": "Bearer " + os.environ["ACP_SMS_TOKEN"]} if os.environ.get("ACP_SMS_TOKEN") else {})})
        with urllib.request.urlopen(req, timeout=15) as resp:
            if not 200 <= resp.status < 300:
                raise RuntimeError(f"SMS gateway status {resp.status}")

    def deliver(conn, r):
        try:
            if r["kind"] == "email":
                send_email_row(conn, r)
            else:
                send_sms_row(r)
            conn.execute("UPDATE outbox SET status='sent', attempts=attempts+1, sent_at=?, last_error=NULL WHERE id=?", (now(), r["id"]))
            return "sent"
        except Exception as e:
            att = r["attempts"] + 1
            conn.execute("UPDATE outbox SET status=?, attempts=?, last_error=? WHERE id=?", ("failed" if att >= 3 else "queued", att, str(e)[:300], r["id"]))
            return "failed" if att >= 3 else "retry"

    def flush(conn, limit=50):
        res = {"sent": 0, "retry": 0, "failed": 0}
        for r in conn.execute("SELECT * FROM outbox WHERE status='queued' ORDER BY id LIMIT ?", (limit,)).fetchall():
            if (r["kind"] == "email" and not smtp_on()) or (r["kind"] == "sms" and not sms_on()):
                continue
            res[deliver(conn, r)] += 1
        return res

    def outbox_worker():
        while True:
            time.sleep(15)
            try:
                c = S.connect()
                try:
                    flush(c)
                finally:
                    c.close()
            except Exception:
                traceback.print_exc()
    S.EXT_START.append(lambda: threading.Thread(target=outbox_worker, daemon=True).start())

    @route("GET", r"/api/admin/channels", roles=("admin",))
    def channels(ctx):
        return 200, {"email": smtp_on(), "sms": sms_on(), "imap": bool(os.environ.get("ACP_IMAP_HOST")),
                     "queued": ctx.conn.execute("SELECT COUNT(*) FROM outbox WHERE status='queued'").fetchone()[0],
                     "failed": ctx.conn.execute("SELECT COUNT(*) FROM outbox WHERE status='failed'").fetchone()[0]}

    @route("GET", r"/api/admin/outbox", roles=("admin",))
    def outbox_list(ctx):
        return 200, [dict(r) for r in ctx.conn.execute("SELECT id,kind,to_addr,subject,status,attempts,last_error,created_at,sent_at,corr_id FROM outbox ORDER BY id DESC LIMIT 100")]

    @route("POST", r"/api/admin/outbox/flush", roles=("admin",))
    def outbox_flush(ctx):
        return 200, flush(ctx.conn)

    EMAIL_RE = S.EMAIL_RE

    @route("POST", r"/api/correspondences/(?P<id>\d+)/send-email")
    def send_email(ctx):
        """إرسال خطاب صادر بالبريد الإلكتروني مع المرفقات وتتبّع حالة التسليم (قبول خادم البريد، لا قراءة المستلم)."""
        cid = int(ctx.params["id"])
        c = S.get_visible(ctx, cid)
        if not S.can_manage(ctx, c):
            raise ApiError(403, "لا تملك صلاحية إرسال هذه المراسلة")
        if not smtp_on():
            raise ApiError(400, "البريد الصادر غير مُهيّأ على الخادم (ACP_SMTP_HOST)")
        if c["direction"] != "outgoing":
            raise ApiError(400, "الإرسال بالبريد للمراسلات الصادرة فقط")
        if c["conf_level"] > 0:
            raise ApiError(400, "لا تُرسل المراسلات السرية بالبريد الإلكتروني خارج المنصة")
        to = f_text(ctx.body, "to", 150, True, "المرسل إليه")
        if not EMAIL_RE.match(to):
            raise ApiError(400, "بريد المرسل إليه غير صالح")
        msg = f_text(ctx.body, "message", 3000) or ""
        ids = ctx.body.get("att_ids") or []
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            raise ApiError(400, "قائمة المرفقات غير صالحة")
        atts = [a for a in ctx.conn.execute("SELECT * FROM attachments WHERE corr_id=? AND removed_at IS NULL", (cid,)) if a["id"] in ids]
        if sum(a["size"] for a in atts) > 10 * 1024 * 1024:
            raise ApiError(400, "مجموع المرفقات يتجاوز 10MB")
        body = f"{msg}\n\n— {c['ref_no']}: {c['subject']}"
        rid = enqueue(ctx.conn, "email", to, f"{c['ref_no']} — {c['subject']}", body, ctx.user["id"], cid, [{"type": "att", "id": a["id"]} for a in atts])
        audit(ctx, "send_email", "correspondence", cid, f"إلى {to} ({len(atts)} مرفق)")
        row = ctx.conn.execute("SELECT * FROM outbox WHERE id=?", (rid,)).fetchone()
        status = deliver(ctx.conn, row)  # محاولة فورية؛ الفشل يُعاد تلقائيًا (حتى 3 مرات)
        return 201, {"id": rid, "status": status}

    @route("GET", r"/api/correspondences/(?P<id>\d+)/deliveries")
    def deliveries(ctx):
        cid = int(ctx.params["id"])
        S.get_visible(ctx, cid)
        return 200, [dict(r) for r in ctx.conn.execute(
            "SELECT id,kind,to_addr,status,attempts,last_error,created_at,sent_at FROM outbox WHERE corr_id=? AND kind='email' AND user_id IS NOT NULL ORDER BY id DESC", (cid,))]

    # ───────────── استقبال البريد الوارد (EML / IMAP) ─────────────
    def next_ref(conn, scope, direction):
        prefix = ("EXT" if scope == "external" else "INT") + "-" + ("IN" if direction == "incoming" else "OUT")
        key = f"{prefix}-{date.today().year}"
        r = conn.execute("SELECT n FROM counters WHERE k=?", (key,)).fetchone()
        n = (r[0] if r else 0) + 1
        conn.execute("INSERT INTO counters(k,n) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET n=excluded.n", (key, n))
        return f"{key}-{n:05d}"
    S.next_ref = next_ref

    def store_attachment(conn, uid, cid, name, data):
        stored = secrets.token_hex(16)
        enc = 1 if S.FILE_CODEC["enc"] else 0
        with open(os.path.join(S.UPLOADS, stored), "wb") as f:
            f.write(S.FILE_CODEC["enc"](data) if enc else data)
        digest = hashlib.sha256(data).hexdigest()
        cur = conn.execute("INSERT INTO attachments(corr_id,filename,stored_name,size,sha256,uploaded_by,created_at,enc) VALUES(?,?,?,?,?,?,?,?)",
                           (cid, name, stored, len(data), digest, uid, now(), enc))
        return cur.lastrowid, digest
    S.store_attachment = store_attachment

    def parse_eml(raw):
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        subject = re.sub(r"\s+", " ", str(msg["subject"] or "(بلا موضوع)")).strip()[:300] or "(بلا موضوع)"
        name, addr = email.utils.parseaddr(str(msg["from"] or ""))
        body = msg.get_body(preferencelist=("plain", "html"))
        text = ""
        if body is not None:
            text = body.get_content()
            if body.get_content_type() == "text/html":
                text = re.sub(r"\s+\n", "\n", HTML_TAG.sub(" ", text))
        try:
            d = email.utils.parsedate_to_datetime(str(msg["date"])).date().isoformat()
        except Exception:
            d = date.today().isoformat()
        atts = []
        for part in msg.iter_attachments():
            fn = part.get_filename()
            if fn:
                atts.append((os.path.basename(fn)[:150], part.get_payload(decode=True) or b""))
        return {"subject": subject, "party": (name or addr or "مرسل غير معروف")[:200], "from_addr": addr, "summary": text.strip()[:4000],
                "doc_date": d, "message_id": (str(msg["message-id"] or "")[:150] or None), "attachments": atts}

    def ingest_eml(conn, actor, raw, ip="email"):
        p = parse_eml(raw)
        legacy = ("mid:" + p["message_id"]) if p["message_id"] else None
        if legacy and conn.execute("SELECT id FROM correspondences WHERE legacy_ref=?", (legacy,)).fetchone():
            return {"duplicate": True}
        ctx = PseudoCtx(conn, actor, ip)
        with S.tx(conn):
            ref = next_ref(conn, "external", "incoming")
            cur = conn.execute(
                "INSERT INTO correspondences(ref_no,scope,direction,subject,summary,party,party_ref,doc_date,priority,conf_level,status,department_id,"
                "created_by,created_at,updated_at,legacy_ref,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ref, "external", "incoming", p["subject"], p["summary"] or None, p["party"], p["from_addr"] or None, p["doc_date"], "normal", 0, "new",
                 actor["department_id"], actor["id"], now(), now(), legacy, "email"))
            cid = cur.lastrowid
            conn.execute("INSERT OR IGNORE INTO corr_units VALUES(?,?)", (cid, actor["department_id"])) if actor["department_id"] else None
            conn.execute("INSERT OR IGNORE INTO reads VALUES(?,?,?)", (cid, actor["id"], now()))
            store_attachment(conn, actor["id"], cid, "message.eml", raw)  # الرسالة الأصلية كدليل
            kept = 0
            for fn, data in p["attachments"]:
                if os.path.splitext(fn)[1].lower() in S.ALLOWED_EXT and 0 < len(data) <= S.MAX_UPLOAD:
                    store_attachment(conn, actor["id"], cid, fn, data)
                    kept += 1
            audit(ctx, "email_ingest", "correspondence", cid, f"{ref} من {p['from_addr']} ({kept} مرفق)")
        S.emit("corr_created", ctx, cid=cid, ref=ref, assignees=[], reply_users=[], parent=None)
        return {"id": cid, "ref_no": ref, "attachments": kept}

    @route("POST", r"/api/admin/import/eml")
    def import_eml(ctx):
        if not S.can(ctx.user, "create_external"):
            raise ApiError(403, "لا تملك صلاحية تسجيل المراسلات الخارجية")
        n = int(ctx.h.headers.get("Content-Length") or 0)
        if n <= 0 or n > 25 * 1024 * 1024:
            raise ApiError(400, "ملف EML فارغ أو أكبر من 25MB")
        return 201, ingest_eml(ctx.conn, ctx.user, ctx.h.rfile.read(n), ctx.ip)

    def imap_poll(conn, client, actor, folder="INBOX"):
        """يقرأ الرسائل غير المقروءة ويحوّلها لمعاملات واردة، ويعلّم المقروء فقط بعد نجاح القيد."""
        client.select(folder)
        typ, data = client.search(None, "UNSEEN")
        done = 0
        for num in (data[0].split() if data and data[0] else []):
            typ, md = client.fetch(num, "(RFC822)")
            if typ != "OK" or not md or not md[0]:
                continue
            try:
                ingest_eml(conn, actor, md[0][1], "imap")
                client.store(num, "+FLAGS", "\\Seen")
                done += 1
            except Exception:
                traceback.print_exc()
        return done
    S.imap_poll = imap_poll

    def imap_worker():
        host = os.environ.get("ACP_IMAP_HOST")
        if not host:
            return
        every = int(os.environ.get("ACP_IMAP_SECONDS", "120"))
        while True:
            try:
                c = S.connect()
                try:
                    actor = c.execute("SELECT * FROM users WHERE username=? AND active=1", (os.environ.get("ACP_IMAP_ACTOR", "admin"),)).fetchone()
                    if actor:
                        cls = imaplib.IMAP4_SSL if os.environ.get("ACP_IMAP_SSL", "1") == "1" else imaplib.IMAP4
                        cl = cls(host, int(os.environ.get("ACP_IMAP_PORT", "993" if cls is imaplib.IMAP4_SSL else "143")))
                        cl.login(os.environ["ACP_IMAP_USER"], os.environ["ACP_IMAP_PASS"])
                        try:
                            imap_poll(c, cl, dict(actor), os.environ.get("ACP_IMAP_FOLDER", "INBOX"))
                        finally:
                            cl.logout()
                finally:
                    c.close()
            except Exception:
                traceback.print_exc()
            time.sleep(every)
    S.EXT_START.append(lambda: threading.Thread(target=imap_worker, daemon=True).start())

    # ───────────── كشف الانحرافات الإحصائي ─────────────
    def anomaly_scan(conn):
        today = date.today().isoformat()
        sigma = float(S.get_settings(conn)["anomaly_sigma"])
        found = []
        for u in conn.execute("SELECT id, name FROM departments WHERE active=1").fetchall():
            row = conn.execute(
                "SELECT SUM(c.status NOT IN('closed','archived','draft')) AS o, SUM(c.status NOT IN('closed','archived','draft') AND c.due_date IS NOT NULL "
                "AND c.due_date<date('now','localtime')) AS od FROM correspondences c JOIN corr_units cu ON cu.corr_id=c.id WHERE cu.dept_id=?", (u["id"],)).fetchone()
            open_n, over = row["o"] or 0, row["od"] or 0
            hist = [r[0] for r in conn.execute("SELECT overdue_n FROM kpi_daily WHERE unit_id=? AND day<? ORDER BY day DESC LIMIT 14", (u["id"], today))]
            conn.execute("INSERT INTO kpi_daily(day,unit_id,open_n,overdue_n) VALUES(?,?,?,?) ON CONFLICT(day,unit_id) DO UPDATE SET open_n=excluded.open_n, overdue_n=excluded.overdue_n",
                         (today, u["id"], open_n, over))
            if len(hist) >= 5:
                mean, sd = statistics.mean(hist), statistics.pstdev(hist)
                limit = mean + max(sigma * sd, 3)
                if over >= 3 and over > limit:
                    msg = f"ارتفاع غير معتاد في المتأخر بوحدة «{u['name']}»: {over} مقابل متوسط {mean:.1f}"
                    found.append(msg)
                    mgrs = {r[0] for r in conn.execute("SELECT id FROM users WHERE role='manager' AND department_id=? AND active=1", (u["id"],))}
                    mgrs |= {r[0] for r in conn.execute("SELECT id FROM users WHERE role='admin' AND active=1")}
                    for uid in mgrs:
                        S.notify(conn, uid, "anomaly", "تنبيه انحراف أداء", msg, None, dedupe=f"anom:u{u['id']}:{today}:{uid}")
        loads = {r[0]: r[1] for r in conn.execute("SELECT to_user, COUNT(*) FROM transfers WHERE status='pending' GROUP BY to_user")}
        if len(loads) >= 3:  # تكدس معاملات عند موظف بعينه مقارنة بزملائه
            med = statistics.median(loads.values())
            for uid, n in loads.items():
                if n >= 8 and n >= 3 * max(med, 1):
                    name = conn.execute("SELECT full_name, department_id FROM users WHERE id=?", (uid,)).fetchone()
                    msg = f"تكدس {n} معاملة لدى {name['full_name']} (الوسيط {med:g}) — يُنصح بإعادة توزيع العبء"
                    found.append(msg)
                    mgrs = {r[0] for r in conn.execute("SELECT id FROM users WHERE role='manager' AND department_id=? AND active=1", (name["department_id"],))}
                    mgrs |= {r[0] for r in conn.execute("SELECT id FROM users WHERE role='admin' AND active=1")}
                    for m in mgrs:
                        S.notify(conn, m, "anomaly", "تنبيه انحراف أداء", msg, None, dedupe=f"anom:e{uid}:{today}:{m}")
        return found
    S.EXT_TICK.append(anomaly_scan)

    # ───────────── التقارير المجدولة ─────────────
    REPORTS_DIR = os.path.join(S.DATA, "reports")

    def kpi_sheets(k):
        return [("ملخص المؤشرات", [["المؤشر", "القيمة"], ["إجمالي المراسلات", k["total"]], ["المفتوح", k["open"]], ["المتأخر", k["overdue"]],
                                   ["نسبة المتأخر ٪", k["overdue_pct"]], ["الالتزام بـ SLA ٪", k.get("sla_compliance_pct")], ["متوسط أيام الإنجاز", k["avg_close_days"]],
                                   ["متوسط زمن أول استجابة (ساعة)", k["avg_first_response_hours"]], ["نسبة الرد ٪", k["reply_rate_pct"]]]),
                ("الوحدات", [["الوحدة", "الإجمالي", "مفتوح", "متأخر"]] + [[u["unit"], u["total"], u["open_n"], u["overdue"]] for u in k["units"]]),
                ("أعباء الموظفين", [["الموظف", "إحالات معلّقة"]] + [[w["employee"], w["pending"]] for w in k["workload"]])]

    def run_report(conn, sch):
        admin = {"id": 0, "role": "admin", "clearance": 2, "perms": set(), "eff": [0], "department_id": None}
        k = S.compute_kpi(conn, admin, {})
        data = S.make_xlsx(kpi_sheets(k))
        os.makedirs(REPORTS_DIR, exist_ok=True)
        fname = f"{re.sub(r'[^\w-]+', '_', sch['name'])}-{date.today().isoformat()}.xlsx"
        path = os.path.join(REPORTS_DIR, f"{secrets.token_hex(4)}-{fname}")
        with open(path, "wb") as f:
            f.write(data)
        rid = conn.execute("INSERT INTO report_runs(schedule_id,name,filename,path,created_at) VALUES(?,?,?,?,?)", (sch["id"], sch["name"], fname, path, now())).lastrowid
        for uid in json.loads(sch["recipients"]):
            S.notify(conn, uid, "report", f"تقرير جاهز: {sch['name']}", fname, None, dedupe=f"rep:{rid}:{uid}")
            u = conn.execute("SELECT email FROM users WHERE id=? AND active=1", (uid,)).fetchone()
            if smtp_on() and u and u["email"]:
                enqueue(conn, "email", u["email"], f"تقرير دوري: {sch['name']}", "مرفق التقرير الدوري من منصة الاتصالات الإدارية.", uid, None, [{"type": "file", "name": fname, "path": path}])
        conn.execute("UPDATE report_schedules SET last_run=? WHERE id=?", (date.today().isoformat(), sch["id"]))
        return rid

    def run_schedules(conn):
        n = datetime.now()
        wd = (n.weekday() + 1) % 7  # الأحد = 0
        ran = []
        for s in conn.execute("SELECT * FROM report_schedules WHERE active=1").fetchall():
            if s["weekday"] == wd and n.hour >= s["hour"] and s["last_run"] != date.today().isoformat():
                ran.append(run_report(conn, s))
        return ran
    S.EXT_TICK.append(run_schedules)

    def sched_values(b, creating, conn):
        out = {}
        if creating or "name" in b:
            out["name"] = f_text(b, "name", 80, True, "الاسم")
        if creating or "weekday" in b:
            out["weekday"] = f_int(b, "weekday", 0, 6, True)
        if "hour" in b:
            out["hour"] = f_int(b, "hour", 0, 23, True)
        if creating or "recipients" in b:
            rec = b.get("recipients")
            if not isinstance(rec, list) or not rec or not all(isinstance(i, int) for i in rec):
                raise ApiError(400, "حدّد مستلمًا واحدًا على الأقل")
            for i in rec:
                S.user_row(conn, i)
            out["recipients"] = json.dumps(rec)
        if "active" in b:
            out["active"] = 1 if b["active"] else 0
        return out

    @route("GET", r"/api/admin/report-schedules", roles=("admin",))
    def sch_list(ctx):
        return 200, [dict(r, recipients=json.loads(r["recipients"])) for r in ctx.conn.execute("SELECT * FROM report_schedules ORDER BY id")]

    @route("POST", r"/api/admin/report-schedules", roles=("admin",))
    def sch_add(ctx):
        v = sched_values(ctx.body, True, ctx.conn)
        v["created_by"] = ctx.user["id"]
        cur = ctx.conn.execute(f"INSERT INTO report_schedules({','.join(v)}) VALUES({','.join('?' * len(v))})", list(v.values()))
        audit(ctx, "report_schedule_create", "report", cur.lastrowid, v["name"])
        return 201, {"id": cur.lastrowid}

    @route("PUT", r"/api/admin/report-schedules/(?P<id>\d+)", roles=("admin",))
    def sch_edit(ctx):
        v = sched_values(ctx.body, False, ctx.conn)
        if not v:
            raise ApiError(400, "لا يوجد ما يُحدَّث")
        ctx.conn.execute(f"UPDATE report_schedules SET {','.join(k + '=?' for k in v)} WHERE id=?", list(v.values()) + [int(ctx.params["id"])])
        return 200, {"ok": True}

    @route("DELETE", r"/api/admin/report-schedules/(?P<id>\d+)", roles=("admin",))
    def sch_del(ctx):
        ctx.conn.execute("DELETE FROM report_schedules WHERE id=?", (int(ctx.params["id"]),))
        return 200, {"ok": True}

    @route("POST", r"/api/admin/report-schedules/(?P<id>\d+)/run", roles=("admin",))
    def sch_run(ctx):
        s = ctx.conn.execute("SELECT * FROM report_schedules WHERE id=?", (int(ctx.params["id"]),)).fetchone()
        if not s:
            raise ApiError(404, "الجدولة غير موجودة")
        rid = run_report(ctx.conn, s)
        audit(ctx, "report_run", "report", rid, s["name"])
        return 201, {"run_id": rid}

    @route("GET", r"/api/admin/report-runs", roles=("admin",))
    def runs_list(ctx):
        return 200, [dict(r) for r in ctx.conn.execute("SELECT id,schedule_id,name,filename,created_at FROM report_runs ORDER BY id DESC LIMIT 100")]

    @route("GET", r"/api/admin/report-runs/(?P<id>\d+)/download", roles=("admin",))
    def run_download(ctx):
        r = ctx.conn.execute("SELECT * FROM report_runs WHERE id=?", (int(ctx.params["id"]),)).fetchone()
        if not r or not os.path.isfile(r["path"]):
            raise ApiError(404, "التقرير غير موجود")
        with open(r["path"], "rb") as f:
            return S.Raw(200, f.read(), {"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                         "Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(r["filename"])})
