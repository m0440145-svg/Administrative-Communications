"""وحدة ملحقة: الإشعارات، الوسوم والبحث النصي، الاقتراحات الذكية وقواعد التوجيه، قوالب الخطابات،
تفضيلات المستخدم والفلاتر المحفوظة، التذكيرات الدورية وتنبيهات انحراف الأداء.

لا تعدّل أي جدول أو مسار قائم؛ تضيف جداول ومسارات جديدة وتشترك في أحداث النواة عبر emit/on.
"""
import json
import math
import re
import threading
import time
import traceback
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), kind TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT, corr_id INTEGER REFERENCES correspondences(id), dedupe_key TEXT, created_at TEXT NOT NULL, read_at TEXT);
CREATE INDEX IF NOT EXISTS ix_notif_user ON notifications(user_id, read_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_notif_dedupe ON notifications(user_id, dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS tags(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS corr_tags(
  corr_id INTEGER NOT NULL REFERENCES correspondences(id), tag_id INTEGER NOT NULL REFERENCES tags(id), PRIMARY KEY(corr_id, tag_id));
CREATE TABLE IF NOT EXISTS att_text(att_id INTEGER PRIMARY KEY, corr_id INTEGER NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS routing_rules(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, keywords TEXT, party_contains TEXT, direction TEXT,
  set_type_id INTEGER, set_folder_id INTEGER, set_priority TEXT, set_dept_id INTEGER, assign_user_id INTEGER,
  auto_apply INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 100);
CREATE TABLE IF NOT EXISTS templates(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, direction TEXT NOT NULL DEFAULT 'any', subject_tpl TEXT, body_tpl TEXT,
  type_id INTEGER, active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 100);
CREATE TABLE IF NOT EXISTS user_prefs(user_id INTEGER PRIMARY KEY REFERENCES users(id), theme TEXT NOT NULL DEFAULT 'auto',
  page_size INTEGER NOT NULL DEFAULT 20);
CREATE TABLE IF NOT EXISTS saved_filters(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL, kind TEXT NOT NULL,
  query TEXT NOT NULL, created_at TEXT NOT NULL);
"""

FTS_SCHEMA = ("CREATE VIRTUAL TABLE IF NOT EXISTS corr_fts USING fts5("
              "subject, summary, party, ref, tags, att, tokenize='unicode61')")

DEFAULT_SETTINGS = {"reminder_days_before": "1", "alert_overdue_pct": "20", "alert_avg_close_days": "30", "alert_backlog_max": "0"}
DEFAULT_TEMPLATES = [
    ("رد بالموافقة", "outgoing", "رد: {subject_orig}",
     "السيد/ {party} المحترم\nالسلام عليكم ورحمة الله وبركاته،\nإشارةً إلى خطابكم رقم ({ref_orig}) بشأن «{subject_orig}»، "
     "نفيدكم بالموافقة على ما ورد فيه، ونأمل اتخاذ ما يلزم.\nوتقبلوا خالص التحية والتقدير."),
    ("رد بالاعتذار", "outgoing", "اعتذار: {subject_orig}",
     "السيد/ {party} المحترم\nالسلام عليكم ورحمة الله وبركاته،\nإشارةً إلى خطابكم رقم ({ref_orig}) بشأن «{subject_orig}»، "
     "نأسف لعدم تمكّننا من تلبية الطلب في الوقت الحالي.\nوتقبلوا خالص التحية والتقدير."),
    ("طلب استيضاح", "outgoing", "طلب استيضاح بشأن: {subject_orig}",
     "السيد/ {party} المحترم\nالسلام عليكم ورحمة الله وبركاته،\nإشارةً إلى خطابكم رقم ({ref_orig})، نأمل التكرم بتزويدنا "
     "بمزيد من التفاصيل حتى نتمكن من اتخاذ الإجراء المناسب.\nوتقبلوا خالص التحية والتقدير."),
    ("إفادة باستلام", "outgoing", "إفادة باستلام: {subject_orig}",
     "السيد/ {party} المحترم\nنفيدكم باستلام خطابكم رقم ({ref_orig}) بتاريخ {date}، وجارٍ دراسته واتخاذ ما يلزم."),
]


def install(S):
    route, ApiError = S.route, S.ApiError
    f_text, f_int, now, audit = S.f_text, S.f_int, S.now, S.audit
    S.EXT_SCHEMAS.append(SCHEMA)
    S.MIGRATIONS["user_prefs"] = {"columns": "TEXT", "email_on": "INTEGER NOT NULL DEFAULT 1", "sms_on": "INTEGER NOT NULL DEFAULT 0"}
    LIST_COLUMNS = {"read", "party", "subject", "ref", "date", "priority", "status", "reply", "att", "sla", "actions"}
    state = {"fts": False, "model": None}

    # ───────────── تطبيع النص العربي (للبحث والتصنيف) ─────────────
    _marks = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭـ]")

    def norm_tokens(text):
        t = _marks.sub("", (text or "").lower())
        t = (t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
              .replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي"))
        out = []
        for w in re.findall(r"\w+", t):
            for p in ("وال", "بال", "كال", "فال", "لل", "ال"):  # تجذير خفيف: حذف «ال» التعريف وما يسبقها من حروف الجر/العطف
                if len(w) > len(p) + 2 and w.startswith(p):
                    w = w[len(p):]
                    break
            out.append(w)
        return out

    def norm(text):
        return " ".join(norm_tokens(text))

    S.norm_text = norm

    # ───────────── الإعدادات ─────────────
    def get_settings(conn):
        d = dict(DEFAULT_SETTINGS)
        d.update({r[0]: r[1] for r in conn.execute("SELECT k,v FROM settings")})
        return d

    # ───────────── الإشعارات ─────────────
    S.get_settings = get_settings
    S.SETTING_DEFAULTS = DEFAULT_SETTINGS  # تُوسِّعها الوحدات الأخرى بمفاتيحها
    S.SETTING_TEXT_KEYS = set()            # مفاتيح نصية حرة (الباقي أرقام)

    def notify(conn, user_id, kind, title, body=None, corr_id=None, dedupe=None):
        cur = conn.execute("INSERT OR IGNORE INTO notifications(user_id,kind,title,body,corr_id,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?)",
                           (user_id, kind, title, body, corr_id, dedupe, now()))
        if cur.rowcount:  # إشعار جديد فعلًا (لا تكرار) ← القنوات الإضافية: لحظي/بريد/SMS
            for fn in S.EXT_NOTIFY:
                try:
                    fn(conn, user_id, kind, title, body, corr_id)
                except Exception:
                    traceback.print_exc()
        return bool(cur.rowcount)
    S.notify = notify

    def ref_of(conn, cid):
        r = conn.execute("SELECT ref_no, subject FROM correspondences WHERE id=?", (cid,)).fetchone()
        return (r["ref_no"], r["subject"]) if r else ("", "")

    @S.on("corr_created")
    def _n_created(ctx, cid, ref, assignees, reply_users, parent):
        me = ctx.user["id"]
        subj = ref_of(ctx.conn, cid)[1]
        for uid in assignees:
            if uid != me:
                notify(ctx.conn, uid, "assigned", f"أُسندت إليك المراسلة {ref}", subj, cid)
        for uid in reply_users:
            if uid != me:
                notify(ctx.conn, uid, "reply_needed", f"مطلوب منك الرد على {ref}", subj, cid)

    @S.on("transferred")
    def _n_transfer(ctx, cid, to, instruction):
        if to != ctx.user["id"]:
            ref, subj = ref_of(ctx.conn, cid)
            notify(ctx.conn, to, "transfer", f"أُحيلت إليك المراسلة {ref}", instruction or subj, cid)

    @S.on("status_changed")
    def _n_status(ctx, cid, old, new):
        c = ctx.conn.execute("SELECT ref_no, created_by, assigned_to FROM correspondences WHERE id=?", (cid,)).fetchone()
        for uid in {c["created_by"], c["assigned_to"]} - {None, ctx.user["id"]}:
            notify(ctx.conn, uid, "status", f"تغيّرت حالة المراسلة {c['ref_no']}", f"{old} ← {new}", cid)

    @S.on("transfer_done")
    def _n_done(ctx, cid, by, from_user):
        if from_user != ctx.user["id"]:
            notify(ctx.conn, from_user, "done", f"نُفّذت إحالتك على {ref_of(ctx.conn, cid)[0]}", ctx.user.get("username"), cid)

    @S.on("replied")
    def _n_replied(ctx, cid, ref):
        c = ctx.conn.execute("SELECT ref_no, created_by FROM correspondences WHERE id=?", (cid,)).fetchone()
        if c and c["created_by"] != ctx.user["id"]:
            notify(ctx.conn, c["created_by"], "replied", f"تم الرد على {c['ref_no']}", f"برقم {ref}", cid)

    @route("GET", r"/api/notifications")
    def notif_list(ctx):
        limit = min(100, int(ctx.query["limit"])) if ctx.query.get("limit", "").isdigit() else 30
        where = "user_id=?" + (" AND read_at IS NULL" if ctx.query.get("unread") == "1" else "")
        rows = ctx.conn.execute(f"SELECT * FROM notifications WHERE {where} ORDER BY id DESC LIMIT ?", (ctx.user["id"], limit))
        unread = ctx.conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (ctx.user["id"],)).fetchone()[0]
        return 200, {"unread": unread, "items": [dict(r) for r in rows]}

    @route("GET", r"/api/notifications/count")
    def notif_count(ctx):
        n = ctx.conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (ctx.user["id"],)).fetchone()[0]
        return 200, {"unread": n}

    @route("POST", r"/api/notifications/read")
    def notif_read(ctx):
        ids = ctx.body.get("ids")
        if ctx.body.get("all"):
            ctx.conn.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", (now(), ctx.user["id"]))
        elif isinstance(ids, list) and all(isinstance(i, int) for i in ids) and len(ids) <= 200:
            for i in ids:
                ctx.conn.execute("UPDATE notifications SET read_at=? WHERE id=? AND user_id=? AND read_at IS NULL", (now(), i, ctx.user["id"]))
        else:
            raise ApiError(400, "طلب غير صالح")
        return 200, {"ok": True}

    # ───────────── التذكيرات الدورية وتنبيهات انحراف الأداء ─────────────
    def kpi_snapshot(conn):
        open_ = "status NOT IN('closed','archived')"
        total_open = conn.execute(f"SELECT COUNT(*) FROM correspondences WHERE {open_}").fetchone()[0]
        overdue = conn.execute(f"SELECT COUNT(*) FROM correspondences WHERE {open_} AND due_date IS NOT NULL "
                               "AND due_date<date('now','localtime')").fetchone()[0]
        avg = conn.execute("SELECT AVG(julianday(updated_at)-julianday(created_at)) FROM correspondences "
                           "WHERE status IN('closed','archived') AND updated_at>=date('now','localtime','-90 days')").fetchone()[0]
        return {"open": total_open, "overdue": overdue, "overdue_pct": round(100 * overdue / total_open, 1) if total_open else 0.0,
                "avg_close_days": round(avg, 1) if avg is not None else None}

    def tick(conn):
        """تذكيرات المواعيد + تنبيهات الانحراف؛ آمنة التكرار (لا تكرر إشعارًا في اليوم نفسه)."""
        st = get_settings(conn)
        today = date.today()
        horizon = (today + timedelta(days=int(st["reminder_days_before"]))).isoformat()
        made = 0
        rows = conn.execute(
            "SELECT t.id, t.to_user, t.due_date, c.id AS cid, c.ref_no, c.subject FROM transfers t JOIN correspondences c ON c.id=t.corr_id "
            "WHERE t.status='pending' AND t.due_date IS NOT NULL AND t.due_date<=? AND c.status NOT IN('closed','archived')", (horizon,)).fetchall()
        for r in rows:
            late = r["due_date"] < today.isoformat()
            before = conn.total_changes
            notify(conn, r["to_user"], "overdue" if late else "due_soon",
                   ("تجاوزت موعدها: " if late else "يقترب موعدها: ") + r["ref_no"], f"{r['subject']} — الاستحقاق {r['due_date']}", r["cid"],
                   dedupe=f"due:{r['id']}:{today.isoformat()}")
            made += conn.total_changes - before
        snap = kpi_snapshot(conn)
        alerts = []
        if snap["overdue_pct"] > float(st["alert_overdue_pct"]) and snap["open"] >= 5:
            alerts.append(f"نسبة المتأخر {snap['overdue_pct']}٪ تتجاوز الحد ({st['alert_overdue_pct']}٪)")
        if snap["avg_close_days"] is not None and snap["avg_close_days"] > float(st["alert_avg_close_days"]):
            alerts.append(f"متوسط أيام الإنجاز {snap['avg_close_days']} يتجاوز الحد ({st['alert_avg_close_days']})")
        if int(st["alert_backlog_max"]) and snap["open"] > int(st["alert_backlog_max"]):
            alerts.append(f"المراسلات المفتوحة {snap['open']} تتجاوز الحد ({st['alert_backlog_max']})")
        for a in alerts:
            for (uid,) in conn.execute("SELECT id FROM users WHERE role='admin' AND active=1").fetchall():
                notify(conn, uid, "kpi_alert", "تنبيه انحراف أداء", a, None, dedupe=f"kpi:{a[:30]}:{today.isoformat()}")
        extra = {}
        for fn in S.EXT_TICK:  # تصعيد SLA، كشف الانحراف الإحصائي، التقارير المجدولة…
            try:
                extra[fn.__name__] = fn(conn)
            except Exception:
                traceback.print_exc()
        return {"reminders": made, "alerts": alerts, "snapshot": snap, "extra": extra}

    def worker():
        while True:
            time.sleep(600)
            try:
                c = S.connect()
                try:
                    tick(c)
                finally:
                    c.close()
            except Exception:
                traceback.print_exc()

    S.EXT_START.append(lambda: threading.Thread(target=worker, daemon=True).start())

    @route("POST", r"/api/admin/reminders/run", roles=("admin",))
    def run_reminders(ctx):
        return 200, tick(ctx.conn)

    @route("GET", r"/api/admin/settings", roles=("admin",))
    def settings_get(ctx):
        return 200, get_settings(ctx.conn)

    @route("PUT", r"/api/admin/settings", roles=("admin",))
    def settings_put(ctx):
        for k, v in ctx.body.items():
            if k not in DEFAULT_SETTINGS:
                raise ApiError(400, f"إعداد غير معروف: {k}")
            if k in S.SETTING_TEXT_KEYS:
                if not isinstance(v, str) or len(v) > 300:
                    raise ApiError(400, f"قيمة غير صالحة: {k}")
            elif not re.fullmatch(r"\d{1,5}(\.\d)?", str(v)):
                raise ApiError(400, f"قيمة غير صالحة: {k}")
            ctx.conn.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
        audit(ctx, "settings_update", detail=json.dumps(ctx.body, ensure_ascii=False)[:200])
        return 200, get_settings(ctx.conn)

    # ───────────── الوسوم والبحث النصي الكامل ─────────────
    def clean_tags(raw):
        if raw in (None, ""):
            return []
        if not isinstance(raw, list) or len(raw) > 20:
            raise ApiError(400, "قائمة الوسوم غير صالحة")
        out = []
        for t in raw:
            if not isinstance(t, str):
                raise ApiError(400, "وسم غير صالح")
            t = re.sub(r"\s+", " ", t.strip().lstrip("#"))[:30]
            if t and t not in out:
                out.append(t)
        return out

    def set_tags(conn, cid, names):
        conn.execute("DELETE FROM corr_tags WHERE corr_id=?", (cid,))
        for n in names:
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (n,))
            tid = conn.execute("SELECT id FROM tags WHERE name=?", (n,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO corr_tags VALUES(?,?)", (cid, tid))

    def index_corr(conn, cid):
        if not state["fts"]:
            return
        c = conn.execute("SELECT * FROM correspondences WHERE id=?", (cid,)).fetchone()
        if not c:
            return
        tags = " ".join(r[0] for r in conn.execute("SELECT t.name FROM corr_tags ct JOIN tags t ON t.id=ct.tag_id WHERE ct.corr_id=?", (cid,)))
        att = " ".join([r[0] for r in conn.execute("SELECT text FROM att_text WHERE corr_id=?", (cid,))] +
                       [r[0] for r in conn.execute("SELECT text FROM notes WHERE corr_id=?", (cid,))])  # نص المرفقات النصية + التهميشات
        conn.execute("DELETE FROM corr_fts WHERE rowid=?", (cid,))
        conn.execute("INSERT INTO corr_fts(rowid,subject,summary,party,ref,tags,att) VALUES(?,?,?,?,?,?,?)",
                     (cid, norm(c["subject"]), norm(c["summary"]), norm(c["party"]),
                      norm(f"{c['ref_no']} {c['party_ref'] or ''} {c['legacy_ref'] or ''}"), norm(tags), norm(att)))
    S.index_corr = index_corr

    def init(conn):
        try:
            conn.execute(FTS_SCHEMA)
            state["fts"] = True
        except Exception:
            print("تنبيه: FTS5 غير متاح في هذه النسخة من SQLite؛ سيُستخدم بحث LIKE البديل", flush=True)
        if state["fts"]:
            if conn.execute("SELECT COUNT(*) FROM corr_fts").fetchone()[0] != conn.execute("SELECT COUNT(*) FROM correspondences").fetchone()[0]:
                have = {r[0] for r in conn.execute("SELECT rowid FROM corr_fts")}
                conn.execute("BEGIN IMMEDIATE")  # معاملة واحدة: أسرع بأضعاف من التثبيت لكل صف
                try:
                    for (cid,) in conn.execute("SELECT id FROM correspondences").fetchall():
                        if cid not in have:
                            index_corr(conn, cid)  # بناء الفهرس للمراسلات القائمة مسبقًا
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
        if conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0] == 0:
            for i, (n, d, s, b) in enumerate(DEFAULT_TEMPLATES, 1):
                conn.execute("INSERT INTO templates(name,direction,subject_tpl,body_tpl,sort) VALUES(?,?,?,?,?)", (n, d, s, b, i))
    S.EXT_INIT.append(init)

    TEXT_EXT = {".txt", ".csv", ".eml", ".md"}

    @S.on("corr_created")
    def _idx_created(ctx, cid, ref, assignees, reply_users, parent):
        tags = clean_tags(ctx.body.get("tags"))
        if tags:
            set_tags(ctx.conn, cid, tags)
        index_corr(ctx.conn, cid)

    @S.on("attachment_added")
    def _idx_att(ctx, cid, att_id, filename, data):
        import os
        if os.path.splitext(filename)[1].lower() in TEXT_EXT:  # استخلاص نص الملفات النصية فقط (OCR ضمن خارطة الطريق)
            for enc in ("utf-8-sig", "cp1256"):
                try:
                    txt = data[:400_000].decode(enc)
                    break
                except UnicodeDecodeError:
                    txt = None
            if txt:
                ctx.conn.execute("INSERT OR REPLACE INTO att_text(att_id,corr_id,text) VALUES(?,?,?)", (att_id, cid, txt[:200_000]))
        index_corr(ctx.conn, cid)

    def _detail(ctx, c):
        c["tags"] = [r[0] for r in ctx.conn.execute(
            "SELECT t.name FROM corr_tags ct JOIN tags t ON t.id=ct.tag_id WHERE ct.corr_id=? ORDER BY t.name", (c["id"],))]
    S.EXT_DETAIL.append(_detail)

    @route("GET", r"/api/tags")
    def tags_list(ctx):
        return 200, [r[0] for r in ctx.conn.execute("SELECT name FROM tags ORDER BY name LIMIT 500")]

    @route("PUT", r"/api/correspondences/(?P<id>\d+)/tags")
    def tags_put(ctx):
        cid = int(ctx.params["id"])
        c = S.get_visible(ctx, cid)
        if not S.can_manage(ctx, c):
            raise ApiError(403, "لا تملك صلاحية تعديل الوسوم")
        names = clean_tags(ctx.body.get("tags"))
        with S.tx(ctx.conn):
            set_tags(ctx.conn, cid, names)
            index_corr(ctx.conn, cid)
            audit(ctx, "tags", "correspondence", cid, "، ".join(names) or "(حذف الوسوم)")
        return 200, {"tags": names}

    # ───────────── البحث المركّب (AND / OR / NOT / "عبارة" / -استبعاد / أقواس) ─────────────
    def parse_boolean(q):
        """يحوّل استعلام المستخدم إلى تعبير FTS5 آمن. الأمثلة: عقد AND صيانة · "ورشة تدريب" · ميزانية -مسودة · (عقد OR مناقصة) NOT ملغى"""
        parts = re.findall(r'"[^"]*"|\(|\)|[^\s()"]+', q)
        out, prev = [], None   # prev: 'term' | 'op' | 'open' | 'close' | None
        depth = 0

        def push(tok, kind):
            nonlocal prev
            if kind in ("term", "open") and prev in ("term", "close"):
                out.append("AND")  # بين كلمتين متجاورتين AND ضمنيًا
            out.append(tok)
            prev = kind

        for p in parts:
            up = p.upper()
            if p == "(":
                depth += 1
                push("(", "open")
            elif p == ")":
                if depth == 0:
                    continue
                depth -= 1
                if prev == "op":
                    raise ApiError(400, "عامل بحث ناقص قبل القوس")
                push(")", "close")
            elif up in ("AND", "OR", "NOT"):
                if prev in (None, "op", "open"):
                    if up == "NOT":
                        raise ApiError(400, "NOT يجب أن يسبقه مصطلح مطلوب، مثل: عقد NOT ملغى")
                    continue  # AND/OR في البداية تُتجاهل
                push(up, "op")
            elif p.startswith("-") and len(p) > 1 and norm_tokens(p[1:]):
                if prev in (None, "op", "open"):
                    raise ApiError(400, "الاستبعاد (-كلمة) يلي كلمة مطلوبة، مثل: عقد -ملغى")
                if prev in ("term", "close"):
                    out.append("NOT")
                    prev = "op"
                push(f'"{" ".join(norm_tokens(p[1:]))}"', "term")
            elif p.startswith('"'):
                tk = norm_tokens(p.strip('"'))
                if tk:
                    push('"' + " ".join(tk) + '"', "term")
            else:
                tk = norm_tokens(p)
                if tk:
                    push(f'"{tk[0]}"*' if len(tk) == 1 else "(" + " AND ".join(f'"{t_}"*' for t_ in tk) + ")", "term")
        while out and out[-1] in ("AND", "OR", "NOT"):
            out.pop()
        out += [")"] * depth
        if not any(x not in ("AND", "OR", "NOT", "(", ")") for x in out):
            raise ApiError(400, "اكتب كلمة للبحث")
        return " ".join(out)

    @route("GET", r"/api/search")
    def search(ctx):
        q = (ctx.query.get("q") or "").strip()[:200]
        if not norm_tokens(q):
            raise ApiError(400, "اكتب كلمة للبحث")
        vs, vp = S.visible_clause(ctx.user)
        limit = min(100, int(ctx.query["limit"])) if ctx.query.get("limit", "").isdigit() else 50
        qy = ctx.query
        extra, ep = [], []
        for key, col in (("type", "type_id"), ("folder", "folder_id")):
            if qy.get(key, "").isdigit():
                extra.append(f"c.{col}=?"); ep.append(int(qy[key]))
        for key in ("scope", "direction", "status", "priority"):
            if qy.get(key):
                extra.append(f"c.{key}=?"); ep.append(qy[key][:40])
        if S.DATE_RE.match(qy.get("from", "")):
            extra.append("c.doc_date>=?"); ep.append(qy["from"])
        if S.DATE_RE.match(qy.get("to", "")):
            extra.append("c.doc_date<=?"); ep.append(qy["to"])
        if qy.get("tag"):
            extra.append("EXISTS(SELECT 1 FROM corr_tags ctg JOIN tags tg ON tg.id=ctg.tag_id WHERE ctg.corr_id=c.id AND tg.name=?)"); ep.append(qy["tag"][:30])
        ex = (" AND " + " AND ".join(extra)) if extra else ""
        expr = None
        if state["fts"]:
            expr = parse_boolean(q)
            try:
                rows = ctx.conn.execute(
                    "SELECT c.id, c.ref_no, c.subject, c.party, c.direction, c.scope, c.status, c.priority, c.doc_date, "
                    "snippet(corr_fts, -1, '«', '»', '…', 14) AS snip FROM corr_fts JOIN correspondences c ON c.id=corr_fts.rowid "
                    f"WHERE corr_fts MATCH ? AND {vs}{ex} ORDER BY rank LIMIT ?", [expr] + vp + ep + [limit]).fetchall()
            except Exception:  # تعبير غير صالح لـFTS5: نعود لـAND بسيط بدل الفشل
                expr = " AND ".join(f'"{t_}"*' for t_ in norm_tokens(q)[:8])
                rows = ctx.conn.execute(
                    "SELECT c.id, c.ref_no, c.subject, c.party, c.direction, c.scope, c.status, c.priority, c.doc_date, "
                    "snippet(corr_fts, -1, '«', '»', '…', 14) AS snip FROM corr_fts JOIN correspondences c ON c.id=corr_fts.rowid "
                    f"WHERE corr_fts MATCH ? AND {vs}{ex} ORDER BY rank LIMIT ?", [expr] + vp + ep + [limit]).fetchall()
        else:
            like = "%" + q.replace("%", "").replace("_", "") + "%"
            rows = ctx.conn.execute(
                "SELECT c.id, c.ref_no, c.subject, c.party, c.direction, c.scope, c.status, c.priority, c.doc_date, c.summary AS snip "
                f"FROM correspondences c WHERE (c.subject LIKE ? OR c.summary LIKE ? OR c.party LIKE ?) AND {vs}{ex} ORDER BY c.id DESC LIMIT ?",
                [like] * 3 + vp + ep + [limit]).fetchall()
        return 200, {"engine": "fts5" if state["fts"] else "like", "query": expr, "items": [dict(r) for r in rows]}
    # ───────────── الاقتراحات الذكية (Naive Bayes على الأرشيف + قواعد صريحة) ─────────────
    TARGETS = ("type_id", "folder_id", "priority", "department_id")

    def build_model(conn):
        sig = tuple(conn.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM correspondences").fetchone())
        if state["model"] and state["model"]["sig"] == sig:
            return state["model"]
        rows = conn.execute("SELECT subject, summary, party, type_id, folder_id, priority, department_id FROM correspondences "
                            "ORDER BY id DESC LIMIT 4000").fetchall()
        m = {"sig": sig, "vocab": set(), "t": {k: {"n": Counter(), "tok": defaultdict(Counter), "tot": Counter()} for k in TARGETS}}
        for r in rows:
            toks = [w for w in norm_tokens(f"{r['subject']} {r['summary'] or ''} {r['party']}") if len(w) > 1]
            m["vocab"].update(toks)
            for k in TARGETS:
                lab = r[k]
                if lab is None:
                    continue
                t = m["t"][k]
                t["n"][lab] += 1
                t["tok"][lab].update(toks)
                t["tot"][lab] += len(toks)
        state["model"] = m
        return m

    def predict(model, toks, k):
        t = model["t"][k]
        total = sum(t["n"].values())
        if total < 3:
            return None
        V = max(1, len(model["vocab"]))
        scores = {}
        for lab, n in t["n"].items():
            s = math.log(n / total)
            for w in toks:
                s += math.log((t["tok"][lab][w] + 0.5) / (t["tot"][lab] + 0.5 * V))
            scores[lab] = s
        mx = max(scores.values())
        exps = {lab: math.exp(s - mx) for lab, s in scores.items()}
        z = sum(exps.values())
        best = max(exps, key=exps.get)
        return best, exps[best] / z, t["n"][best]

    def rule_matches(rule, text_norm, party_norm, direction):
        if rule["direction"] and direction and rule["direction"] != direction:
            return False
        kws = [k for k in dict.fromkeys(norm(x) for x in re.split(r"[،,]", rule["keywords"] or "") if x.strip()) if k]
        if kws and not any(k in text_norm for k in kws):
            return False
        if rule["party_contains"] and norm(rule["party_contains"]) not in party_norm:
            return False
        return bool(kws or rule["party_contains"])

    def match_rules(conn, text, party, direction=None):
        tn, pn = norm(text), norm(party)
        return [r for r in conn.execute("SELECT * FROM routing_rules WHERE active=1 ORDER BY sort, id").fetchall()
                if rule_matches(r, tn, pn, direction)]

    @route("GET", r"/api/suggest")
    def suggest(ctx):
        conn = ctx.conn
        subject, summary, party = (ctx.query.get(k, "")[:500] for k in ("subject", "summary", "party"))
        direction = ctx.query.get("direction")
        text = f"{subject} {summary} {party}"
        toks = [w for w in norm_tokens(text) if len(w) > 1][:40]
        out = {"source": None, "type": None, "folder": None, "priority": None, "unit": None, "assignee": None, "rules": []}
        if not toks:
            return 200, out
        names = {"type_id": ("ctypes", "type"), "folder_id": ("folders", "folder"), "department_id": ("departments", "unit")}
        model = build_model(conn)
        for k in TARGETS:
            p = predict(model, toks, k)
            if not p or p[1] < 0.45 or p[2] < 2:
                continue
            lab, conf, support = p
            conf = min(conf, 0.99)  # الاقتراح الآلي لا يدّعي يقينًا مطلقًا
            if k == "priority":
                cl = S.classes(conn).get(lab)
                if cl and cl["conf_level"] <= ctx.user["clearance"] and lab != "normal":
                    out["priority"] = {"key": lab, "label": cl["label"], "confidence": round(conf, 2), "support": support, "source": "ml"}
                continue
            table, key = names[k]
            nm = conn.execute(f"SELECT name FROM {table} WHERE id=?", (lab,)).fetchone()
            if nm:
                out[key] = {"id": lab, "name": nm[0], "confidence": round(conf, 2), "support": support, "source": "ml"}
        for r in match_rules(conn, text, party, direction):  # القواعد الصريحة تتفوق على التعلم الآلي
            out["rules"].append(r["name"])
            for fld, key, table in (("set_type_id", "type", "ctypes"), ("set_folder_id", "folder", "folders"), ("set_dept_id", "unit", "departments")):
                if r[fld]:
                    nm = conn.execute(f"SELECT name FROM {table} WHERE id=?", (r[fld],)).fetchone()
                    if nm:
                        out[key] = {"id": r[fld], "name": nm[0], "confidence": 1.0, "source": "rule"}
            if r["set_priority"] and r["set_priority"] in S.classes(conn):
                cl = S.classes(conn)[r["set_priority"]]
                if cl["conf_level"] <= ctx.user["clearance"]:
                    out["priority"] = {"key": r["set_priority"], "label": cl["label"], "confidence": 1.0, "source": "rule"}
            if r["assign_user_id"]:
                u = conn.execute("SELECT id, full_name FROM users WHERE id=? AND active=1", (r["assign_user_id"],)).fetchone()
                if u:
                    out["assignee"] = {"id": u["id"], "name": u["full_name"], "confidence": 1.0, "source": "rule"}
        out["source"] = "rule" if out["rules"] else ("ml" if any(out[k] for k in ("type", "folder", "priority", "unit")) else None)
        return 200, out

    @S.on("corr_created")
    def _auto_route(ctx, cid, ref, assignees, reply_users, parent):
        conn = ctx.conn
        c = conn.execute("SELECT * FROM correspondences WHERE id=?", (cid,)).fetchone()
        for r in match_rules(conn, f"{c['subject']} {c['summary'] or ''}", c["party"], c["direction"]):
            if not r["auto_apply"]:
                continue
            sets, params = [], []
            if r["set_type_id"] and c["type_id"] is None:
                sets.append("type_id=?"); params.append(r["set_type_id"])
            if r["set_folder_id"] and c["folder_id"] is None:
                sets.append("folder_id=?"); params.append(r["set_folder_id"])
            if sets:
                conn.execute(f"UPDATE correspondences SET {','.join(sets)} WHERE id=?", params + [cid])
            if r["set_dept_id"]:
                conn.execute("INSERT OR IGNORE INTO corr_units(corr_id,dept_id) VALUES(?,?)", (cid, r["set_dept_id"]))
            if r["assign_user_id"] and not assignees:
                u = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (r["assign_user_id"],)).fetchone()
                if u and u["clearance"] >= c["conf_level"]:
                    conn.execute("INSERT INTO transfers(corr_id,from_user,to_user,instruction,created_at) VALUES(?,?,?,?,?)",
                                 (cid, ctx.user["id"], u["id"], f"توجيه تلقائي: {r['name']}", now()))
                    conn.execute("UPDATE correspondences SET assigned_to=?, status='referred' WHERE id=? AND assigned_to IS NULL", (u["id"], cid))
                    notify(conn, u["id"], "assigned", f"أُسندت إليك المراسلة {ref} (توجيه تلقائي)", r["name"], cid)
            audit(ctx, "auto_route", "correspondence", cid, f"قاعدة: {r['name']}")
            break  # أول قاعدة مطابقة فقط
        index_corr(conn, cid)

    # ───────────── إدارة قواعد التوجيه والقوالب (للمدير) ─────────────
    RULE_FIELDS = ("name", "keywords", "party_contains", "direction", "set_type_id", "set_folder_id", "set_priority",
                   "set_dept_id", "assign_user_id", "auto_apply", "active", "sort")

    def rule_values(b, creating):
        out = {}
        if creating or "name" in b:
            out["name"] = f_text(b, "name", 80, True, "اسم القاعدة")
        for k, ln in (("keywords", 300), ("party_contains", 100), ("set_priority", 40)):
            if k in b:
                out[k] = f_text(b, k, ln)
        if "direction" in b:
            if b["direction"] not in (None, "", "incoming", "outgoing"):
                raise ApiError(400, "اتجاه غير صالح")
            out["direction"] = b["direction"] or None
        for k in ("set_type_id", "set_folder_id", "set_dept_id", "assign_user_id"):
            if k in b:
                out[k] = f_int(b, k)
        for k in ("auto_apply", "active"):
            if k in b:
                out[k] = 1 if b[k] else 0
        if "sort" in b:
            out["sort"] = f_int(b, "sort", 0, 1000, True)
        return out

    @route("GET", r"/api/admin/rules", roles=("admin",))
    def rules_list(ctx):
        return 200, [dict(r) for r in ctx.conn.execute("SELECT * FROM routing_rules ORDER BY sort, id")]

    @route("POST", r"/api/admin/rules", roles=("admin",))
    def rules_add(ctx):
        v = rule_values(ctx.body, True)
        if not (v.get("keywords") or v.get("party_contains")):
            raise ApiError(400, "حدّد كلمات مفتاحية أو جزءًا من اسم الجهة")
        cur = ctx.conn.execute(f"INSERT INTO routing_rules({','.join(v)}) VALUES({','.join('?' * len(v))})", list(v.values()))
        audit(ctx, "rule_create", "rule", cur.lastrowid, v["name"])
        return 201, {"id": cur.lastrowid}

    @route("PUT", r"/api/admin/rules/(?P<id>\d+)", roles=("admin",))
    def rules_edit(ctx):
        v = rule_values(ctx.body, False)
        if not v:
            raise ApiError(400, "لا يوجد ما يُحدَّث")
        cur = ctx.conn.execute(f"UPDATE routing_rules SET {','.join(k + '=?' for k in v)} WHERE id=?", list(v.values()) + [int(ctx.params["id"])])
        if not cur.rowcount:
            raise ApiError(404, "القاعدة غير موجودة")
        audit(ctx, "rule_update", "rule", int(ctx.params["id"]), json.dumps(v, ensure_ascii=False)[:200])
        return 200, {"ok": True}

    @route("DELETE", r"/api/admin/rules/(?P<id>\d+)", roles=("admin",))
    def rules_del(ctx):
        ctx.conn.execute("DELETE FROM routing_rules WHERE id=?", (int(ctx.params["id"]),))
        audit(ctx, "rule_delete", "rule", int(ctx.params["id"]))
        return 200, {"ok": True}

    @route("GET", r"/api/templates")
    def tpl_list(ctx):
        allr = ctx.user["role"] == "admin" and ctx.query.get("all") == "1"
        rows = ctx.conn.execute("SELECT * FROM templates" + ("" if allr else " WHERE active=1") + " ORDER BY sort, id")
        return 200, [dict(r) for r in rows]

    def tpl_values(b, creating):
        out = {}
        if creating or "name" in b:
            out["name"] = f_text(b, "name", 80, True, "اسم القالب")
        if "direction" in b:
            if b["direction"] not in ("any", "incoming", "outgoing"):
                raise ApiError(400, "اتجاه غير صالح")
            out["direction"] = b["direction"]
        if "subject_tpl" in b:
            out["subject_tpl"] = f_text(b, "subject_tpl", 300)
        if "body_tpl" in b:
            out["body_tpl"] = f_text(b, "body_tpl", 5000)
        if "type_id" in b:
            out["type_id"] = f_int(b, "type_id")
        for k in ("active",):
            if k in b:
                out[k] = 1 if b[k] else 0
        if "sort" in b:
            out["sort"] = f_int(b, "sort", 0, 1000, True)
        return out

    @route("POST", r"/api/templates", roles=("admin",))
    def tpl_add(ctx):
        v = tpl_values(ctx.body, True)
        cur = ctx.conn.execute(f"INSERT INTO templates({','.join(v)}) VALUES({','.join('?' * len(v))})", list(v.values()))
        audit(ctx, "template_create", "template", cur.lastrowid, v["name"])
        return 201, {"id": cur.lastrowid}

    @route("PUT", r"/api/templates/(?P<id>\d+)", roles=("admin",))
    def tpl_edit(ctx):
        v = tpl_values(ctx.body, False)
        if not v:
            raise ApiError(400, "لا يوجد ما يُحدَّث")
        cur = ctx.conn.execute(f"UPDATE templates SET {','.join(k + '=?' for k in v)} WHERE id=?", list(v.values()) + [int(ctx.params["id"])])
        if not cur.rowcount:
            raise ApiError(404, "القالب غير موجود")
        audit(ctx, "template_update", "template", int(ctx.params["id"]))
        return 200, {"ok": True}

    # ───────────── تفضيلات المستخدم والفلاتر المحفوظة ─────────────
    @route("GET", r"/api/me/prefs")
    def prefs_get(ctx):
        r = ctx.conn.execute("SELECT theme, page_size, columns, email_on, sms_on FROM user_prefs WHERE user_id=?", (ctx.user["id"],)).fetchone()
        d = dict(r) if r else {"theme": "auto", "page_size": 20, "columns": None, "email_on": 1, "sms_on": 0}
        d["columns"] = json.loads(d["columns"]) if d["columns"] else None
        return 200, d

    @route("PUT", r"/api/me/prefs")
    def prefs_put(ctx):
        b = ctx.body
        theme = b.get("theme", "auto")
        size = f_int(b, "page_size", 5, 100) or 20
        if theme not in ("auto", "light", "dark"):
            raise ApiError(400, "سمة غير صالحة")
        cols = b.get("columns")
        if cols is not None and (not isinstance(cols, list) or not all(c in LIST_COLUMNS for c in cols)):
            raise ApiError(400, "أعمدة غير صالحة")
        ctx.conn.execute("INSERT INTO user_prefs(user_id,theme,page_size) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                         "theme=excluded.theme, page_size=excluded.page_size", (ctx.user["id"], theme, size))
        if "columns" in b:
            ctx.conn.execute("UPDATE user_prefs SET columns=? WHERE user_id=?", (json.dumps(cols) if cols else None, ctx.user["id"]))
        for k in ("email_on", "sms_on"):
            if k in b:
                ctx.conn.execute(f"UPDATE user_prefs SET {k}=? WHERE user_id=?", (1 if b[k] else 0, ctx.user["id"]))
        return 200, {"theme": theme, "page_size": size}

    @route("GET", r"/api/me/filters")
    def filters_list(ctx):
        return 200, [dict(r) for r in ctx.conn.execute("SELECT id,name,kind,query FROM saved_filters WHERE user_id=? ORDER BY id", (ctx.user["id"],))]

    @route("POST", r"/api/me/filters")
    def filters_add(ctx):
        name = f_text(ctx.body, "name", 60, True, "الاسم")
        kind = ctx.body.get("kind")
        if kind not in ("list", "report"):
            raise ApiError(400, "نوع غير صالح")
        query = f_text(ctx.body, "query", 500) or ""
        if not re.fullmatch(r"[\w%&=.\-:+،ا-ي ]*", query):
            raise ApiError(400, "استعلام غير صالح")
        if ctx.conn.execute("SELECT COUNT(*) FROM saved_filters WHERE user_id=?", (ctx.user["id"],)).fetchone()[0] >= 30:
            raise ApiError(400, "بلغت الحد الأقصى للفلاتر المحفوظة (30)")
        cur = ctx.conn.execute("INSERT INTO saved_filters(user_id,name,kind,query,created_at) VALUES(?,?,?,?,?)",
                               (ctx.user["id"], name, kind, query, now()))
        return 201, {"id": cur.lastrowid}

    @route("DELETE", r"/api/me/filters/(?P<id>\d+)")
    def filters_del(ctx):
        ctx.conn.execute("DELETE FROM saved_filters WHERE id=? AND user_id=?", (int(ctx.params["id"]), ctx.user["id"]))
        return 200, {"ok": True}
