"""وحدة ملحقة: المهام (To-Do) — إضافة مهام مستقلة، وتحويل أي معاملة (وخصوصًا العاجلة) إلى مهمة متابعة.

جدول مستقل `work_tasks` ومسارات جديدة فقط؛ لا يمسّ الإحالات ولا خطوات المسارات ولا قائمة /api/tasks القائمة.
  • أي مستخدم ينشئ مهمة لنفسه؛ مدير النظام/مدير الإدارة/مكتب الصادر والوارد يُسندونها لغيرهم.
  • مهمة مرتبطة بمعاملة: لا تُنشأ إلا لمن يحق له الاطلاع على المعاملة، ولا يرى غيرُه موضوعَها إن لم يحق له.
  • إشعار عند الإسناد، وتذكير عند الاستحقاق/التأخر (دون تكرار).
"""
import re
from datetime import date, timedelta

PRIORITIES = {"normal": "عادية", "high": "مهمة", "urgent": "عاجلة"}
STAFF = ("admin", "manager", "clerk")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def install(S):
    route, ApiError, now, audit = S.route, S.ApiError, S.now, S.audit
    S.EXT_SCHEMAS.append("""CREATE TABLE IF NOT EXISTS work_tasks(
        id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT, corr_id INTEGER REFERENCES correspondences(id),
        assignee_id INTEGER NOT NULL REFERENCES users(id), created_by INTEGER NOT NULL REFERENCES users(id),
        priority TEXT NOT NULL DEFAULT 'normal', due_date TEXT, status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','done')),
        done_note TEXT, done_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_wt_assignee ON work_tasks(assignee_id, status);
        CREATE INDEX IF NOT EXISTS ix_wt_corr ON work_tasks(corr_id);""")

    def clean_date(v, label="التاريخ"):
        if v in (None, ""):
            return None
        if not isinstance(v, str) or not DATE_RE.match(v):
            raise ApiError(400, f"{label} بصيغة YYYY-MM-DD")
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ApiError(400, f"{label} غير صالح")
        return v

    def clean_prio(v):
        v = v or "normal"
        if v not in PRIORITIES:
            raise ApiError(400, "أولوية غير صالحة")
        return v

    def resolve_assignee(ctx, v):
        """الموظف العادي يُسند لنفسه فقط؛ الأدوار الإدارية لأي مستخدم نشط."""
        uid = int(v) if str(v or "").isdigit() else ctx.user["id"]
        if uid != ctx.user["id"]:
            if ctx.user["role"] not in STAFF:
                raise ApiError(403, "لا تملك صلاحية إسناد المهام لغيرك")
            if not ctx.conn.execute("SELECT 1 FROM users WHERE id=? AND active=1", (uid,)).fetchone():
                raise ApiError(404, "المستخدم المُسنَد إليه غير موجود أو معطّل")
        return uid

    def corr_by_ref(ctx, ref):
        vs, vp = S.visible_clause(ctx.user)
        r = ctx.conn.execute(f"SELECT c.id FROM correspondences c WHERE UPPER(c.ref_no)=UPPER(?) AND {vs}", [ref.strip()] + vp).fetchone()
        if not r:
            raise ApiError(404, "لا توجد معاملة بهذا الرقم ضمن ما يحق لك الاطلاع عليه")
        return r["id"]

    def make(ctx, b, corr_id=None):
        title = S.f_text(b, "title", 200, True, "عنوان المهمة")
        desc = S.f_text(b, "description", 2000)
        due = clean_date(b.get("due_date"), "الاستحقاق")
        prio = clean_prio(b.get("priority"))
        assignee = resolve_assignee(ctx, b.get("assignee_id"))
        if corr_id is None:
            if b.get("corr_id"):
                corr_id = int(b["corr_id"])
            elif b.get("ref_no"):
                corr_id = corr_by_ref(ctx, str(b["ref_no"]))
        ref = None
        if corr_id is not None:
            ref = S.get_visible(ctx, corr_id)["ref_no"]  # يحق له الاطلاع، وإلا 404 مع تسجيل المحاولة
        with S.tx(ctx.conn):
            cur = ctx.conn.execute(
                "INSERT INTO work_tasks(title,description,corr_id,assignee_id,created_by,priority,due_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (title, desc, corr_id, assignee, ctx.user["id"], prio, due, now(), now()))
            tid = cur.lastrowid
            audit(ctx, "task_create", "work_task", tid, title + (f" — {ref}" if ref else ""))
            if assignee != ctx.user["id"]:
                S.notify(ctx.conn, assignee, "task", f"مهمة جديدة: {title}", f"أسندها إليك {ctx.user['full_name']}" + (f" — مرتبطة بالمعاملة {ref}" if ref else ""), corr_id)
        return tid

    SELECT = ("SELECT t.*, a.full_name AS assignee_name, cr.full_name AS creator_name, c.ref_no, c.id AS c_id, c.subject, c.conf_level "
              "FROM work_tasks t JOIN users a ON a.id=t.assignee_id JOIN users cr ON cr.id=t.created_by LEFT JOIN correspondences c ON c.id=t.corr_id")

    def shape(ctx, rows):
        """يُظهر موضوع المعاملة المرتبطة لمن يحق له فقط (وإلا الرقم المرجعي بلا موضوع)."""
        out, today = [], date.today().isoformat()
        vis = {}
        for r in rows:
            d = dict(r)
            cid = d.pop("c_id")
            subj, conf = d.pop("subject"), d.pop("conf_level")
            if cid is not None:
                if cid not in vis:
                    vs, vp = S.visible_clause(ctx.user)
                    vis[cid] = bool(ctx.conn.execute(f"SELECT 1 FROM correspondences c WHERE c.id=? AND {vs}", [cid] + vp).fetchone())
                d["corr_visible"] = vis[cid]
                d["corr_subject"] = subj if vis[cid] else None
                if not vis[cid]:
                    d["corr_id"] = None  # لا رابط لما لا يحق له فتحه
            d["overdue"] = bool(d["status"] == "open" and d["due_date"] and d["due_date"] < today)
            d["can_edit"] = ctx.user["role"] == "admin" or d["created_by"] == ctx.user["id"]
            d["can_complete"] = ctx.user["role"] == "admin" or ctx.user["id"] in (d["assignee_id"], d["created_by"])
            out.append(d)
        return out

    @route("GET", r"/api/work-tasks")
    def list_tasks(ctx):
        q, uid = ctx.query, ctx.user["id"]
        where, p = [], []
        if q.get("corr_id", "").isdigit():  # مهام معاملة بعينها: لكل من يحق له الاطلاع عليها
            cid = int(q["corr_id"])
            S.get_visible(ctx, cid)
            where.append("t.corr_id=?"); p.append(cid)
        else:
            scope = q.get("scope", "mine")
            if scope == "mine":
                where.append("t.assignee_id=?"); p.append(uid)
            elif scope == "created":
                where.append("t.created_by=? AND t.assignee_id<>?"); p += [uid, uid]
            elif scope == "all":
                if ctx.user["role"] != "admin":
                    raise ApiError(403, "العرض الشامل لمدير النظام")
            else:
                raise ApiError(400, "نطاق غير صالح")
        if q.get("status") in ("open", "done"):
            where.append("t.status=?"); p.append(q["status"])
        sql = f"{SELECT} WHERE {' AND '.join(where) or '1=1'} ORDER BY t.status='done', COALESCE(t.due_date,'9999-12-31'), t.id DESC LIMIT 300"
        return 200, shape(ctx, ctx.conn.execute(sql, p).fetchall())

    @route("GET", r"/api/work-tasks/summary")
    def summary(ctx):
        today = date.today().isoformat()
        c = ctx.conn
        o = c.execute("SELECT COUNT(*) FROM work_tasks WHERE assignee_id=? AND status='open'", (ctx.user["id"],)).fetchone()[0]
        ov = c.execute("SELECT COUNT(*) FROM work_tasks WHERE assignee_id=? AND status='open' AND due_date<?", (ctx.user["id"], today)).fetchone()[0]
        td = c.execute("SELECT COUNT(*) FROM work_tasks WHERE assignee_id=? AND status='open' AND due_date=?", (ctx.user["id"], today)).fetchone()[0]
        return 200, {"open": o, "overdue": ov, "due_today": td}

    @route("POST", r"/api/work-tasks")
    def create_task(ctx):
        tid = make(ctx, ctx.body)
        return 201, shape(ctx, ctx.conn.execute(f"{SELECT} WHERE t.id=?", (tid,)).fetchall())[0]

    @route("POST", r"/api/correspondences/(?P<id>\d+)/to-task")
    def to_task(ctx):
        """تحويل معاملة (عاجلة غالبًا) إلى مهمة متابعة: عنوان وأولوية واستحقاق افتراضية من المعاملة."""
        cid = int(ctx.params["id"])
        c = S.get_visible(ctx, cid)
        urgent = bool(ctx.conn.execute("SELECT 1 FROM classifications WHERE key=? AND is_urgent=1", (c["priority"],)).fetchone())
        b = dict(ctx.body or {})
        if not (b.get("title") or "").strip():
            b["title"] = f"متابعة: {c['subject']}"[:200] if c["conf_level"] == 0 else f"متابعة المعاملة {c['ref_no']}"
        b.setdefault("priority", "urgent" if urgent else "normal")
        if not b.get("due_date"):
            b["due_date"] = (date.today() + timedelta(days=1 if urgent else 3)).isoformat()
        if not b.get("assignee_id"):
            b["assignee_id"] = c["assigned_to"] if c["assigned_to"] and ctx.user["role"] in STAFF else ctx.user["id"]
        tid = make(ctx, b, corr_id=cid)
        return 201, shape(ctx, ctx.conn.execute(f"{SELECT} WHERE t.id=?", (tid,)).fetchall())[0]

    def get_task(ctx):
        r = ctx.conn.execute("SELECT * FROM work_tasks WHERE id=?", (int(ctx.params["id"]),)).fetchone()
        if not r:
            raise ApiError(404, "المهمة غير موجودة")
        return r

    def own(ctx, t, edit=False):
        if ctx.user["role"] == "admin":
            return
        if edit and t["created_by"] != ctx.user["id"]:
            raise ApiError(403, "التعديل والحذف لمنشئ المهمة أو مدير النظام")
        if not edit and ctx.user["id"] not in (t["assignee_id"], t["created_by"]):
            raise ApiError(403, "لا تملك صلاحية على هذه المهمة")

    @route("POST", r"/api/work-tasks/(?P<id>\d+)/complete")
    def complete(ctx):
        t = get_task(ctx); own(ctx, t)
        note = S.f_text(ctx.body, "note", 1000)
        with S.tx(ctx.conn):
            ctx.conn.execute("UPDATE work_tasks SET status='done', done_note=?, done_at=?, updated_at=? WHERE id=?", (note, now(), now(), t["id"]))
            audit(ctx, "task_done", "work_task", t["id"], t["title"])
            if t["created_by"] != ctx.user["id"]:
                S.notify(ctx.conn, t["created_by"], "task", f"أُنجزت المهمة: {t['title']}", f"أنجزها {ctx.user['full_name']}" + (f" — {note}" if note else ""), t["corr_id"])
        return 200, {"ok": True}

    @route("POST", r"/api/work-tasks/(?P<id>\d+)/reopen")
    def reopen(ctx):
        t = get_task(ctx); own(ctx, t)
        with S.tx(ctx.conn):
            ctx.conn.execute("UPDATE work_tasks SET status='open', done_note=NULL, done_at=NULL, updated_at=? WHERE id=?", (now(), t["id"]))
            audit(ctx, "task_reopen", "work_task", t["id"], t["title"])
        return 200, {"ok": True}

    @route("PUT", r"/api/work-tasks/(?P<id>\d+)")
    def update(ctx):
        t = get_task(ctx); own(ctx, t, edit=True)
        b = ctx.body
        title = S.f_text(b, "title", 200, True, "عنوان المهمة") if "title" in b else t["title"]
        desc = S.f_text(b, "description", 2000) if "description" in b else t["description"]
        due = clean_date(b.get("due_date"), "الاستحقاق") if "due_date" in b else t["due_date"]
        prio = clean_prio(b.get("priority")) if "priority" in b else t["priority"]
        assignee = resolve_assignee(ctx, b.get("assignee_id")) if "assignee_id" in b else t["assignee_id"]
        with S.tx(ctx.conn):
            ctx.conn.execute("UPDATE work_tasks SET title=?, description=?, due_date=?, priority=?, assignee_id=?, updated_at=? WHERE id=?", (title, desc, due, prio, assignee, now(), t["id"]))
            audit(ctx, "task_update", "work_task", t["id"], title)
            if assignee != t["assignee_id"] and assignee != ctx.user["id"]:
                S.notify(ctx.conn, assignee, "task", f"مهمة جديدة: {title}", f"أُسندت إليك من {ctx.user['full_name']}", t["corr_id"])
        return 200, {"ok": True}

    @route("DELETE", r"/api/work-tasks/(?P<id>\d+)")
    def delete(ctx):
        t = get_task(ctx); own(ctx, t, edit=True)
        with S.tx(ctx.conn):
            ctx.conn.execute("DELETE FROM work_tasks WHERE id=?", (t["id"],))
            audit(ctx, "task_delete", "work_task", t["id"], t["title"])
        return 200, {"ok": True}

    # ───── تذكير بالاستحقاق والتأخر (مرة واحدة لكل حالة) ─────
    def remind(conn):
        today = date.today().isoformat()
        for t in conn.execute("SELECT id,title,assignee_id,corr_id,due_date FROM work_tasks WHERE status='open' AND due_date IS NOT NULL AND due_date<=?", (today,)).fetchall():
            late = t["due_date"] < today
            S.notify(conn, t["assignee_id"], "task", ("⏰ مهمة متأخرة: " if late else "📌 مهمة مستحقة اليوم: ") + t["title"], f"الاستحقاق {t['due_date']}", t["corr_id"],
                     dedupe=f"wtask-{t['id']}-{'late' if late else 'due'}")
    S.EXT_TICK.append(remind)
