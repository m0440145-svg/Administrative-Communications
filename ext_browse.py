"""وحدة ملحقة: استعراض الفهارس (مستلهم من Paperless-ngx) + تقويم المواعيد (مستلهم من AdminLTE) + التحرير الجماعي.

كلها قراءة مجمَّعة فوق جداول قائمة (لا جداول جديدة) وتحترم نطاق ما يحق للمستخدم رؤيته (visible_clause):
  • GET  /api/facets?kind=party|tag|type|folder   — الجهات/الوسوم/الأنواع/المجلدات مع عدد المعاملات وما هو مفتوح وآخر تاريخ
  • GET  /api/calendar?month=YYYY-MM              — مواعيد استحقاق المعاملات ومهامي في الشهر
  • POST /api/correspondences/bulk                — تحرير جماعي (مجلد/نوع/إضافة وسوم/تعليم كمقروء) لما يحق له تعديله فقط
"""
import re
from datetime import date

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
OPEN = "c.status NOT IN('closed','archived')"


def install(S):
    route, ApiError, now, audit = S.route, S.ApiError, S.now, S.audit

    @route("GET", r"/api/facets")
    def facets(ctx):
        kind = ctx.query.get("kind", "party")
        cols = {"party": "c.party", "tag": "t.name", "type": "COALESCE(ty.name,'')", "folder": "COALESCE(fo.name,'')"}
        if kind not in cols:
            raise ApiError(400, "نوع فهرس غير صالح")
        vs, vp = S.visible_clause(ctx.user)
        q = (ctx.query.get("q") or "").strip()[:60]
        tail = "COUNT(*) AS n, SUM(" + OPEN + ") AS open_n, MAX(c.created_at) AS last"
        like, lp = "", []
        if q:
            like = f" AND {cols[kind]} LIKE ? ESCAPE '\\'"
            lp = ["%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"]
        if kind == "party":
            sql = f"SELECT c.party AS name, c.party AS key, {tail} FROM correspondences c WHERE {vs}{like} GROUP BY c.party ORDER BY n DESC, last DESC LIMIT 300"
        elif kind == "tag":
            sql = (f"SELECT t.name AS name, t.name AS key, {tail} FROM correspondences c JOIN corr_tags ct ON ct.corr_id=c.id JOIN tags t ON t.id=ct.tag_id "
                   f"WHERE {vs}{like} GROUP BY t.id ORDER BY n DESC LIMIT 300")
        elif kind == "type":
            sql = (f"SELECT COALESCE(ty.name,'بدون نوع') AS name, COALESCE(c.type_id,'') AS key, {tail} FROM correspondences c LEFT JOIN ctypes ty ON ty.id=c.type_id "
                   f"WHERE {vs}{like} GROUP BY c.type_id ORDER BY n DESC")
        else:
            sql = (f"SELECT COALESCE(fo.name,'بدون مجلد') AS name, COALESCE(c.folder_id,'') AS key, {tail} FROM correspondences c LEFT JOIN folders fo ON fo.id=c.folder_id "
                   f"WHERE {vs}{like} GROUP BY c.folder_id ORDER BY n DESC")
        rows = ctx.conn.execute(sql, vp + lp).fetchall()
        return 200, [dict(r, open_n=r["open_n"] or 0) for r in rows]

    @route("GET", r"/api/calendar")
    def calendar(ctx):
        month = ctx.query.get("month") or date.today().strftime("%Y-%m")
        if not MONTH_RE.match(month):
            raise ApiError(400, "الشهر بصيغة YYYY-MM")
        vs, vp = S.visible_clause(ctx.user)
        urgent = "c.priority IN " + S.URGENT
        corr = ctx.conn.execute(
            f"SELECT c.id,c.ref_no,c.subject,c.due_date AS day,c.status,c.direction,({urgent}) AS urgent FROM correspondences c "
            f"WHERE {vs} AND c.due_date LIKE ? ORDER BY c.due_date, c.id LIMIT 500", vp + [month + "-%"]).fetchall()
        tasks = ctx.conn.execute(
            "SELECT t.id,t.title,t.due_date AS day,t.priority,t.status,t.corr_id FROM work_tasks t WHERE (t.assignee_id=? OR t.created_by=?) AND t.due_date LIKE ? "
            "ORDER BY t.due_date, t.id LIMIT 500", (ctx.user["id"], ctx.user["id"], month + "-%")).fetchall() if _has_table(ctx.conn, "work_tasks") else []
        days = {}
        for r in corr:
            days.setdefault(r["day"], []).append({"kind": "corr", "id": r["id"], "title": r["subject"], "ref_no": r["ref_no"], "urgent": bool(r["urgent"]),
                                                  "done": r["status"] in ("closed", "archived"), "direction": r["direction"]})
        for r in tasks:
            days.setdefault(r["day"], []).append({"kind": "task", "id": r["id"], "title": r["title"], "urgent": r["priority"] == "urgent", "done": r["status"] == "done", "corr_id": r["corr_id"]})
        return 200, {"month": month, "today": date.today().isoformat(), "days": days}

    def _has_table(conn, name):
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

    @route("POST", r"/api/correspondences/bulk")
    def bulk(ctx):
        b = ctx.body
        ids, action = b.get("ids"), b.get("action")
        if not isinstance(ids, list) or not ids or len(ids) > 200 or not all(isinstance(i, int) for i in ids):
            raise ApiError(400, "حدّد من 1 إلى 200 معاملة")
        if action not in ("set_folder", "set_type", "add_tags", "mark_read"):
            raise ApiError(400, "إجراء غير معروف")
        value = b.get("value")
        if action in ("set_folder", "set_type"):
            table = "folders" if action == "set_folder" else "ctypes"
            if value not in (None, "") and (not isinstance(value, int) or not ctx.conn.execute(f"SELECT 1 FROM {table} WHERE id=? AND active=1", (value,)).fetchone()):
                raise ApiError(400, "القيمة غير موجودة")
            value = value or None
        tags = []
        if action == "add_tags":
            if not isinstance(value, list) or not value or len(value) > 10:
                raise ApiError(400, "أدخل وسمًا واحدًا على الأقل (حتى 10)")
            for t in value:
                t = str(t).strip().lstrip("#")[:30]
                if not t or re.search(r"[\x00-\x1f<>]", t):
                    raise ApiError(400, "وسم غير صالح")
                tags.append(t)
        done = denied = 0
        with S.tx(ctx.conn):
            for cid in ids:
                vs, vp = S.visible_clause(ctx.user)
                c = ctx.conn.execute(f"SELECT c.* FROM correspondences c WHERE c.id=? AND {vs}", [cid] + vp).fetchone()
                if not c:
                    denied += 1
                    continue
                if action == "mark_read":
                    ctx.conn.execute("INSERT OR IGNORE INTO reads VALUES(?,?,?)", (cid, ctx.user["id"], now()))
                    done += 1
                    continue
                if not S.can_manage(ctx, c) or c["status"] == "archived":
                    denied += 1
                    continue
                if action == "set_folder":
                    ctx.conn.execute("UPDATE correspondences SET folder_id=?, updated_at=? WHERE id=?", (value, now(), cid))
                elif action == "set_type":
                    ctx.conn.execute("UPDATE correspondences SET type_id=?, updated_at=? WHERE id=?", (value, now(), cid))
                else:
                    for t in tags:
                        ctx.conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (t,))
                        tid = ctx.conn.execute("SELECT id FROM tags WHERE name=?", (t,)).fetchone()[0]
                        ctx.conn.execute("INSERT OR IGNORE INTO corr_tags VALUES(?,?)", (cid, tid))
                    ctx.conn.execute("UPDATE correspondences SET updated_at=? WHERE id=?", (now(), cid))
                if hasattr(S, "index_corr"):
                    S.index_corr(ctx.conn, cid)
                done += 1
            if action != "mark_read":
                audit(ctx, "bulk_edit", "correspondence", None, f"{action}: {done} معاملة" + (f" (تعذّر {denied})" if denied else ""))
        return 200, {"done": done, "denied": denied}
