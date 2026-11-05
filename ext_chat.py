"""وحدة ملحقة: الدردشة الفورية بين الموظفين داخل كل معاملة (وارد/صادر).

• كل من يحق له الاطلاع على المعاملة (visible_clause) يقرأ ويكتب في دردشتها؛ ومن لا يحق له لا يرى حتى وجودها (404).
• فورية عبر قناة الإشعارات اللحظية القائمة (SSE) بحدث مستقل `chat`، ويُدفَع فقط للمتصلين ممن يحق لهم رؤية المعاملة.
• الإشارة بـ @اسم_المستخدم تُنشئ إشعارًا دائمًا للمُشار إليه (إن كان يحق له الاطلاع على المعاملة).
• غير المقروء: يُحسب لمنشئ المعاملة والمُسنَد إليه والمُحال إليه ومن شارك بالدردشة.
جداول جديدة فقط (corr_chat وcorr_chat_reads)؛ لا تأثير على الإحالات أو الإشعارات القائمة.
"""
import re
import time

MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9_.\-]{2,40})")
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def install(S):
    route, ApiError, now, audit = S.route, S.ApiError, S.now, S.audit
    S.EXT_SCHEMAS.append("""
        CREATE TABLE IF NOT EXISTS corr_chat(
          id INTEGER PRIMARY KEY, corr_id INTEGER NOT NULL REFERENCES correspondences(id), user_id INTEGER NOT NULL REFERENCES users(id),
          body TEXT NOT NULL, created_at TEXT NOT NULL, deleted_at TEXT);
        CREATE INDEX IF NOT EXISTS ix_chat_corr ON corr_chat(corr_id, id);
        CREATE TABLE IF NOT EXISTS corr_chat_reads(
          corr_id INTEGER NOT NULL, user_id INTEGER NOT NULL, last_id INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(corr_id, user_id));""")

    def shape(rows, me):
        return [{"id": r["id"], "user_id": r["user_id"], "name": r["full_name"], "username": r["username"], "at": r["created_at"],
                 "body": None if r["deleted_at"] else r["body"], "deleted": bool(r["deleted_at"]), "mine": r["user_id"] == me} for r in rows]

    SEL = ("SELECT m.id, m.user_id, m.body, m.created_at, m.deleted_at, u.full_name, u.username FROM corr_chat m JOIN users u ON u.id=m.user_id ")

    def mark_read(conn, cid, uid, last_id):
        conn.execute("INSERT INTO corr_chat_reads(corr_id,user_id,last_id) VALUES(?,?,?) ON CONFLICT(corr_id,user_id) DO UPDATE SET last_id=MAX(last_id, excluded.last_id)", (cid, uid, last_id))

    @route("GET", r"/api/correspondences/(?P<id>\d+)/chat")
    def get_chat(ctx):
        cid = int(ctx.params["id"])
        c = S.get_visible(ctx, cid)
        after = int(ctx.query["after"]) if ctx.query.get("after", "").isdigit() else 0
        if after:
            rows = ctx.conn.execute(SEL + "WHERE m.corr_id=? AND m.id>? ORDER BY m.id LIMIT 200", (cid, after)).fetchall()
        else:
            rows = ctx.conn.execute(SEL + "WHERE m.corr_id=? ORDER BY m.id DESC LIMIT 100", (cid,)).fetchall()[::-1]
        if rows and ctx.query.get("mark") != "0":
            with S.tx(ctx.conn):
                mark_read(ctx.conn, cid, ctx.user["id"], rows[-1]["id"])
        return 200, {"messages": shape(rows, ctx.user["id"]), "can_post": c["status"] != "archived"}

    def user_view(conn, uid):
        r = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
        if not r:
            return None
        u = dict(r)
        u["perms"] = S.load_perms(conn, u["role"])
        u["eff"] = [u["id"]]
        for fn in S.EXT_ENRICH:
            fn(conn, u)
        return u

    def sees(conn, u, cid):
        sql, p = S.visible_clause(u)
        return bool(conn.execute(f"SELECT 1 FROM correspondences c WHERE c.id=? AND {sql}", [cid] + p).fetchone())

    @route("POST", r"/api/correspondences/(?P<id>\d+)/chat")
    def post_chat(ctx):
        cid = int(ctx.params["id"])
        c = S.get_visible(ctx, cid)
        if c["status"] == "archived":
            raise ApiError(400, "المعاملة مؤرشفة: الدردشة للقراءة فقط")
        body = CTRL.sub("", str(ctx.body.get("body") or "")).strip()
        if not body:
            raise ApiError(400, "اكتب رسالة")
        if len(body) > 2000:
            raise ApiError(400, "الرسالة أطول من 2000 حرف")
        recent = ctx.conn.execute("SELECT COUNT(*) FROM corr_chat WHERE user_id=? AND created_at>=?", (ctx.user["id"], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 60)))).fetchone()[0]
        if recent >= 30:
            raise ApiError(429, "رسائل كثيرة، انتظر قليلًا")
        with S.tx(ctx.conn):
            cur = ctx.conn.execute("INSERT INTO corr_chat(corr_id,user_id,body,created_at) VALUES(?,?,?,?)", (cid, ctx.user["id"], body, now()))
            mid = cur.lastrowid
            mark_read(ctx.conn, cid, ctx.user["id"], mid)
        # إشارات @ → إشعار دائم لمن يحق له الاطلاع
        for uname in dict.fromkeys(MENTION.findall(body)):
            tu = ctx.conn.execute("SELECT id FROM users WHERE username=? AND active=1", (uname,)).fetchone()
            if tu and tu["id"] != ctx.user["id"]:
                u = user_view(ctx.conn, tu["id"])
                if u and sees(ctx.conn, u, cid):
                    with S.tx(ctx.conn):
                        S.notify(ctx.conn, tu["id"], "chat", f"💬 {ctx.user['full_name']} ذكرك في دردشة {c['ref_no']}", body[:160], cid)
        # دفع لحظي للمتصلين الذين يحق لهم رؤية المعاملة
        if hasattr(S, "publish") and hasattr(S, "connected_users"):
            ev = {"_event": "chat", "corr_id": cid, "id": mid, "from": ctx.user["id"], "name": ctx.user["full_name"], "ref_no": c["ref_no"]}
            for uid in S.connected_users():
                if uid == ctx.user["id"]:
                    continue
                u = user_view(ctx.conn, uid)
                if u and sees(ctx.conn, u, cid):
                    S.publish(uid, ev)
        row = ctx.conn.execute(SEL + "WHERE m.id=?", (mid,)).fetchone()
        return 201, shape([row], ctx.user["id"])[0]

    @route("DELETE", r"/api/chat/(?P<id>\d+)")
    def delete_msg(ctx):
        m = ctx.conn.execute("SELECT * FROM corr_chat WHERE id=?", (int(ctx.params["id"]),)).fetchone()
        if not m:
            raise ApiError(404, "الرسالة غير موجودة")
        S.get_visible(ctx, m["corr_id"])
        if m["user_id"] != ctx.user["id"] and ctx.user["role"] != "admin":
            raise ApiError(403, "تحذف رسائلك فقط (أو مدير النظام)")
        with S.tx(ctx.conn):
            ctx.conn.execute("UPDATE corr_chat SET deleted_at=? WHERE id=?", (now(), m["id"]))
            audit(ctx, "chat_delete", "correspondence", m["corr_id"], f"رسالة #{m['id']}")
        return 200, {"ok": True}

    @route("GET", r"/api/chat/unread")
    def unread(ctx):
        vs, vp = S.visible_clause(ctx.user)
        uid = ctx.user["id"]
        rows = ctx.conn.execute(
            f"SELECT c.id, c.ref_no, c.subject, c.conf_level, COUNT(m.id) AS n FROM correspondences c JOIN corr_chat m ON m.corr_id=c.id AND m.deleted_at IS NULL AND m.user_id<>? "
            f"LEFT JOIN corr_chat_reads r ON r.corr_id=c.id AND r.user_id=? "
            f"WHERE {vs} AND m.id>COALESCE(r.last_id,0) AND (c.created_by=? OR c.assigned_to=? OR r.user_id IS NOT NULL "
            f"OR EXISTS(SELECT 1 FROM transfers t WHERE t.corr_id=c.id AND t.to_user=?)) GROUP BY c.id ORDER BY MAX(m.id) DESC LIMIT 20",
            [uid, uid] + vp + [uid, uid, uid]).fetchall()
        return 200, {"total": sum(r["n"] for r in rows), "items": [{"id": r["id"], "ref_no": r["ref_no"], "subject": r["subject"] if r["conf_level"] == 0 else None, "n": r["n"]} for r in rows]}
