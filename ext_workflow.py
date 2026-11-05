"""وحدة ملحقة: مسارات العمل القابلة للتخصيص، الاعتماد الرسمي بتوقيع إلكتروني مبسّط (HMAC)، وتفويض الصلاحيات.

التوقيع هنا «توقيع اعتماد داخلي»: بصمة الوثيقة (ومرفقاتها) + هوية المعتمِد + الوقت، موقّعة بمفتاح الخادم السري ويمكن
التحقق منها لاحقًا. التوقيع الرقمي المعتمد قانونيًا (شهادات PKI / الحكومي) ضمن خارطة الطريق.
"""
import hashlib
import hmac
import json
import os
import secrets
from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT, steps TEXT NOT NULL,
  close_on_finish INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wf_instances(
  corr_id INTEGER PRIMARY KEY REFERENCES correspondences(id), workflow_id INTEGER NOT NULL, name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', close_on_finish INTEGER NOT NULL DEFAULT 0, started_by INTEGER NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT);
CREATE TABLE IF NOT EXISTS wf_steps(
  id INTEGER PRIMARY KEY, corr_id INTEGER NOT NULL REFERENCES correspondences(id), idx INTEGER NOT NULL, name TEXT NOT NULL,
  kind TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id), days INTEGER NOT NULL DEFAULT 3, due_date TEXT,
  status TEXT NOT NULL DEFAULT 'waiting', comment TEXT, decided_at TEXT, decided_by INTEGER);
CREATE INDEX IF NOT EXISTS ix_wfs_user ON wf_steps(user_id, status);
CREATE INDEX IF NOT EXISTS ix_wfs_corr ON wf_steps(corr_id);
CREATE TABLE IF NOT EXISTS signatures(
  id INTEGER PRIMARY KEY, corr_id INTEGER NOT NULL REFERENCES correspondences(id), step_id INTEGER, user_id INTEGER NOT NULL,
  decision TEXT NOT NULL, comment TEXT, doc_hash TEXT NOT NULL, signature TEXT NOT NULL, snapshot TEXT NOT NULL, signed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS delegations(
  id INTEGER PRIMARY KEY, from_user INTEGER NOT NULL REFERENCES users(id), to_user INTEGER NOT NULL REFERENCES users(id),
  start_date TEXT NOT NULL, end_date TEXT NOT NULL, note TEXT, active INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER NOT NULL, created_at TEXT NOT NULL);
"""

DEFAULT_WORKFLOWS = [
    ("اعتماد خطاب صادر", "مراجعة المدير المباشر ثم اعتماد رسمي، ويُغلق الخطاب بعد الاعتماد.",
     [{"name": "مراجعة المدير المباشر", "kind": "review", "role": "manager", "days": 2},
      {"name": "الاعتماد الرسمي", "kind": "approve", "role": "admin", "days": 3}], 1),
    ("دراسة وارد ثم اعتماد الرد", "دراسة المراسلة من المدير ثم اعتماد الإجراء.",
     [{"name": "دراسة المدير", "kind": "review", "role": "manager", "days": 3},
      {"name": "اعتماد الإجراء", "kind": "approve", "role": "admin", "days": 2}], 0),
]


def install(S):
    route, ApiError = S.route, S.ApiError
    f_text, f_int, f_date, now, audit = S.f_text, S.f_int, S.f_date, S.now, S.audit
    S.EXT_SCHEMAS.append(SCHEMA)

    def init(conn):
        if conn.execute("SELECT COUNT(*) FROM workflows").fetchone()[0] == 0:
            for n, d, steps, close in DEFAULT_WORKFLOWS:
                conn.execute("INSERT INTO workflows(name,description,steps,close_on_finish,created_at) VALUES(?,?,?,?,?)",
                             (n, d, json.dumps(steps, ensure_ascii=False), close, now()))
    S.EXT_INIT.append(init)

    # ───────────── مفتاح التوقيع (إدارة المفاتيح) ─────────────
    def signing_key():
        env = os.environ.get("ACP_SIGN_KEY")
        if env:
            return env.encode()
        path = os.path.join(S.DATA, "sign.key")
        if not os.path.isfile(path):
            with open(path, "w") as f:
                f.write(secrets.token_hex(32))
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        with open(path) as f:
            return f.read().strip().encode()

    def doc_state(conn, cid):
        c = conn.execute("SELECT ref_no, subject, direction, scope, priority, party, doc_date FROM correspondences WHERE id=?", (cid,)).fetchone()
        atts = sorted(r[0] for r in conn.execute("SELECT sha256 FROM attachments WHERE corr_id=? AND removed_at IS NULL", (cid,)))
        return {**dict(c), "attachments": atts}

    def canonical(obj):
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def sign(conn, cid, step_id, user, decision, comment, on_behalf=None, step_name=None):
        doc = doc_state(conn, cid)
        snap = {"doc": doc, "signer_id": user["id"], "signer": user["full_name"], "decision": decision,
                "step": step_name, "on_behalf_of": on_behalf, "at": now()}
        body = canonical(snap)
        doc_hash = hashlib.sha256(canonical(doc).encode()).hexdigest()
        sig = hmac.new(signing_key(), body.encode(), hashlib.sha256).hexdigest()
        cur = conn.execute("INSERT INTO signatures(corr_id,step_id,user_id,decision,comment,doc_hash,signature,snapshot,signed_at) "
                           "VALUES(?,?,?,?,?,?,?,?,?)", (cid, step_id, user["id"], decision, comment, doc_hash, sig, body, snap["at"]))
        return cur.lastrowid

    def verify(conn, row):
        snap_ok = hmac.compare_digest(hmac.new(signing_key(), row["snapshot"].encode(), hashlib.sha256).hexdigest(), row["signature"])
        snap = json.loads(row["snapshot"])
        unchanged = canonical(doc_state(conn, row["corr_id"])) == canonical(snap["doc"])
        return {"signature_valid": snap_ok, "document_unchanged": unchanged}

    def reauth(ctx):
        if not S.verify_password(str(ctx.body.get("password", "")), ctx.user["password_hash"]):
            raise ApiError(400, "كلمة المرور غير صحيحة — يلزم إدخالها لتأكيد التوقيع")

    # ───────────── التفويض ─────────────
    def enrich(conn, u):
        today = date.today().isoformat()
        for (frm,) in conn.execute(
                "SELECT d.from_user FROM delegations d JOIN users f ON f.id=d.from_user WHERE d.to_user=? AND d.active=1 "
                "AND d.start_date<=? AND d.end_date>=? AND f.active=1", (u["id"], today, today)):
            if frm not in u["eff"]:
                u["eff"].append(frm)
    S.EXT_ENRICH.append(enrich)

    @route("GET", r"/api/delegations")
    def deleg_list(ctx):
        q = ("SELECT d.*, f.full_name AS from_name, t.full_name AS to_name FROM delegations d JOIN users f ON f.id=d.from_user "
             "JOIN users t ON t.id=d.to_user WHERE {} ORDER BY d.id DESC")
        mine = ctx.conn.execute(q.format("d.from_user=?"), (ctx.user["id"],)).fetchall()
        got = ctx.conn.execute(q.format("d.to_user=?"), (ctx.user["id"],)).fetchall()
        return 200, {"given": [dict(r) for r in mine], "received": [dict(r) for r in got]}

    @route("GET", r"/api/admin/delegations", roles=("admin",))
    def deleg_all(ctx):
        rows = ctx.conn.execute("SELECT d.*, f.full_name AS from_name, t.full_name AS to_name FROM delegations d "
                                "JOIN users f ON f.id=d.from_user JOIN users t ON t.id=d.to_user ORDER BY d.id DESC LIMIT 200")
        return 200, [dict(r) for r in rows]

    @route("POST", r"/api/delegations")
    def deleg_add(ctx):
        b = ctx.body
        frm = ctx.user["id"]
        if b.get("from_user") and ctx.user["role"] == "admin":
            frm = f_int(b, "from_user", required=True)
        to = f_int(b, "to_user", required=True)
        S.user_row(ctx.conn, frm)
        S.user_row(ctx.conn, to)
        if to == frm:
            raise ApiError(400, "لا يمكن التفويض لنفسك")
        start, end = f_date(b, "start_date", True, "البداية"), f_date(b, "end_date", True, "النهاية")
        if end < start or (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days > 180:
            raise ApiError(400, "المدة غير صالحة (الحد الأقصى 180 يومًا)")
        cur = ctx.conn.execute("INSERT INTO delegations(from_user,to_user,start_date,end_date,note,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                               (frm, to, start, end, f_text(b, "note", 200), ctx.user["id"], now()))
        audit(ctx, "delegation_create", "user", frm, f"تفويض إلى {to} من {start} إلى {end}")
        S.notify(ctx.conn, to, "delegation", "فُوّضت إليك صلاحيات", f"من {start} إلى {end}")
        return 201, {"id": cur.lastrowid}

    @route("DELETE", r"/api/delegations/(?P<id>\d+)")
    def deleg_del(ctx):
        d = ctx.conn.execute("SELECT * FROM delegations WHERE id=?", (int(ctx.params["id"]),)).fetchone()
        if not d or (d["from_user"] != ctx.user["id"] and ctx.user["role"] != "admin"):
            raise ApiError(404, "التفويض غير موجود")
        ctx.conn.execute("UPDATE delegations SET active=0 WHERE id=?", (d["id"],))
        audit(ctx, "delegation_revoke", "user", d["from_user"], f"إلغاء تفويض #{d['id']}")
        return 200, {"ok": True}

    # ───────────── مسارات العمل ─────────────
    def clean_steps(raw):
        if not isinstance(raw, list) or not 1 <= len(raw) <= 10:
            raise ApiError(400, "المسار يحتاج من 1 إلى 10 خطوات")
        out = []
        for s in raw:
            if not isinstance(s, dict):
                raise ApiError(400, "خطوة غير صالحة")
            name = f_text(s, "name", 60, True, "اسم الخطوة")
            kind = s.get("kind")
            if kind not in ("review", "approve"):
                raise ApiError(400, "نوع الخطوة: مراجعة أو اعتماد")
            step = {"name": name, "kind": kind, "days": f_int(s, "days", 1, 60) or 3}
            if s.get("user_id"):
                step["user_id"] = f_int(s, "user_id", required=True)
            elif s.get("role") in ("manager", "clerk", "admin"):
                step["role"] = s["role"]
            else:
                raise ApiError(400, "حدّد موظفًا أو دورًا لكل خطوة")
            out.append(step)
        return out

    @route("GET", r"/api/workflows")
    def wf_list(ctx):
        allr = ctx.user["role"] == "admin" and ctx.query.get("all") == "1"
        rows = ctx.conn.execute("SELECT * FROM workflows" + ("" if allr else " WHERE active=1") + " ORDER BY id")
        return 200, [dict(r, steps=json.loads(r["steps"])) for r in rows]

    @route("POST", r"/api/workflows", roles=("admin",))
    def wf_add(ctx):
        b = ctx.body
        name = f_text(b, "name", 80, True, "الاسم")
        steps = clean_steps(b.get("steps"))
        try:
            cur = ctx.conn.execute("INSERT INTO workflows(name,description,steps,close_on_finish,created_at) VALUES(?,?,?,?,?)",
                                   (name, f_text(b, "description", 300), json.dumps(steps, ensure_ascii=False), 1 if b.get("close_on_finish") else 0, now()))
        except Exception:
            raise ApiError(409, "يوجد مسار بالاسم نفسه")
        audit(ctx, "workflow_create", "workflow", cur.lastrowid, name)
        return 201, {"id": cur.lastrowid}

    @route("PUT", r"/api/workflows/(?P<id>\d+)", roles=("admin",))
    def wf_edit(ctx):
        b, sets, p = ctx.body, [], []
        if "name" in b:
            sets.append("name=?"); p.append(f_text(b, "name", 80, True, "الاسم"))
        if "description" in b:
            sets.append("description=?"); p.append(f_text(b, "description", 300))
        if "steps" in b:
            sets.append("steps=?"); p.append(json.dumps(clean_steps(b["steps"]), ensure_ascii=False))
        for k in ("close_on_finish", "active"):
            if k in b:
                sets.append(f"{k}=?"); p.append(1 if b[k] else 0)
        if not sets:
            raise ApiError(400, "لا يوجد ما يُحدَّث")
        cur = ctx.conn.execute(f"UPDATE workflows SET {','.join(sets)} WHERE id=?", p + [int(ctx.params["id"])])
        if not cur.rowcount:
            raise ApiError(404, "المسار غير موجود")
        audit(ctx, "workflow_update", "workflow", int(ctx.params["id"]))
        return 200, {"ok": True}

    def resolve_user(conn, step, corr):
        if step.get("user_id"):
            return S.user_row(conn, step["user_id"])
        role = step["role"]
        r = None
        if role == "manager" and corr["department_id"]:
            r = conn.execute("SELECT * FROM users WHERE role='manager' AND active=1 AND department_id=? ORDER BY id LIMIT 1", (corr["department_id"],)).fetchone()
        if not r:
            r = conn.execute("SELECT * FROM users WHERE role=? AND active=1 ORDER BY id LIMIT 1", (role,)).fetchone()
        if not r:
            r = conn.execute("SELECT * FROM users WHERE role='admin' AND active=1 ORDER BY id LIMIT 1").fetchone()
        return r

    def activate(conn, step, corr_ref):
        due = (date.today() + timedelta(days=step["days"])).isoformat()
        conn.execute("UPDATE wf_steps SET status='pending', due_date=? WHERE id=?", (due, step["id"]))
        S.notify(conn, step["user_id"], "wf_step", f"مطلوب إجراؤك: {step['name']}", corr_ref, step["corr_id"])

    @route("POST", r"/api/correspondences/(?P<id>\d+)/workflow")
    def wf_start(ctx):
        cid = int(ctx.params["id"])
        c = S.get_visible(ctx, cid)
        if not S.can_manage(ctx, c):
            raise ApiError(403, "لا تملك صلاحية بدء مسار عمل لهذه المراسلة")
        wf = ctx.conn.execute("SELECT * FROM workflows WHERE id=? AND active=1", (f_int(ctx.body, "workflow_id", required=True),)).fetchone()
        if not wf:
            raise ApiError(404, "مسار العمل غير موجود")
        if ctx.conn.execute("SELECT 1 FROM wf_instances WHERE corr_id=? AND status='active'", (cid,)).fetchone():
            raise ApiError(400, "يوجد مسار عمل جارٍ لهذه المراسلة")
        if c["status"] in ("closed", "archived"):
            raise ApiError(400, "المراسلة مغلقة أو مؤرشفة")
        steps = json.loads(wf["steps"])
        users = []
        for s in steps:
            u = resolve_user(ctx.conn, s, c)
            if not u or u["clearance"] < c["conf_level"]:
                raise ApiError(400, f"لا يوجد موظف مناسب (بصلاحية سرية كافية) لخطوة «{s['name']}»")
            users.append(u)
        with S.tx(ctx.conn):
            ctx.conn.execute("DELETE FROM wf_steps WHERE corr_id=?", (cid,))
            ctx.conn.execute("DELETE FROM wf_instances WHERE corr_id=?", (cid,))
            ctx.conn.execute("INSERT INTO wf_instances(corr_id,workflow_id,name,close_on_finish,started_by,started_at) VALUES(?,?,?,?,?,?)",
                             (cid, wf["id"], wf["name"], wf["close_on_finish"], ctx.user["id"], now()))
            ids = []
            for i, (s, u) in enumerate(zip(steps, users)):
                cur = ctx.conn.execute("INSERT INTO wf_steps(corr_id,idx,name,kind,user_id,days) VALUES(?,?,?,?,?,?)",
                                       (cid, i, s["name"], s["kind"], u["id"], s["days"]))
                ids.append(cur.lastrowid)
            first = ctx.conn.execute("SELECT * FROM wf_steps WHERE id=?", (ids[0],)).fetchone()
            activate(ctx.conn, dict(first), c["ref_no"])
            ctx.conn.execute("UPDATE correspondences SET status=CASE WHEN status='new' THEN 'in_progress' ELSE status END, updated_at=? WHERE id=?", (now(), cid))
            audit(ctx, "workflow_start", "correspondence", cid, wf["name"])
        return 201, {"ok": True}

    @route("POST", r"/api/wf/steps/(?P<id>\d+)/decide")
    def wf_decide(ctx):
        st = ctx.conn.execute("SELECT * FROM wf_steps WHERE id=?", (int(ctx.params["id"]),)).fetchone()
        if not st or st["status"] != "pending" or st["user_id"] not in S.eff_ids(ctx.user):
            raise ApiError(404, "الخطوة غير موجودة أو ليست بانتظارك")
        c = S.get_visible(ctx, st["corr_id"])
        decision = ctx.body.get("decision")
        comment = f_text(ctx.body, "comment", 500)
        ok_decisions = ("approve", "reject") if st["kind"] == "approve" else ("done", "reject")
        if decision not in ok_decisions:
            raise ApiError(400, "قرار غير صالح لهذه الخطوة")
        if st["kind"] == "approve":
            reauth(ctx)  # إعادة إدخال كلمة المرور قبل أي توقيع
        if decision == "reject" and not comment:
            raise ApiError(400, "اكتب سبب الرفض")
        on_behalf = st["user_id"] if st["user_id"] != ctx.user["id"] else None
        with S.tx(ctx.conn):
            ctx.conn.execute("UPDATE wf_steps SET status=?, comment=?, decided_at=?, decided_by=? WHERE id=?",
                             ("rejected" if decision == "reject" else "done", comment, now(), ctx.user["id"], st["id"]))
            if st["kind"] == "approve":
                sign(ctx.conn, st["corr_id"], st["id"], ctx.user, decision, comment, on_behalf, st["name"])
            audit(ctx, "workflow_step", "correspondence", st["corr_id"], f"{st['name']}: {decision}" + (f" — {comment}" if comment else ""))
            inst = ctx.conn.execute("SELECT * FROM wf_instances WHERE corr_id=?", (st["corr_id"],)).fetchone()
            if decision == "reject":
                ctx.conn.execute("UPDATE wf_steps SET status='skipped' WHERE corr_id=? AND status='waiting'", (st["corr_id"],))
                ctx.conn.execute("UPDATE wf_instances SET status='rejected', finished_at=? WHERE corr_id=?", (now(), st["corr_id"]))
                for uid in {inst["started_by"], c["created_by"]} - {ctx.user["id"]}:
                    S.notify(ctx.conn, uid, "wf_rejected", f"رُفضت المراسلة {c['ref_no']} في «{st['name']}»", comment, st["corr_id"])
            else:
                nxt = ctx.conn.execute("SELECT * FROM wf_steps WHERE corr_id=? AND status='waiting' ORDER BY idx LIMIT 1", (st["corr_id"],)).fetchone()
                if nxt:
                    activate(ctx.conn, dict(nxt), c["ref_no"])
                else:
                    ctx.conn.execute("UPDATE wf_instances SET status='done', finished_at=? WHERE corr_id=?", (now(), st["corr_id"]))
                    if inst["close_on_finish"]:
                        ctx.conn.execute("UPDATE correspondences SET status='closed', updated_at=? WHERE id=?", (now(), st["corr_id"]))
                    audit(ctx, "workflow_done", "correspondence", st["corr_id"], inst["name"])
                    for uid in {inst["started_by"], c["created_by"]} - {ctx.user["id"]}:
                        S.notify(ctx.conn, uid, "wf_done", f"اكتمل مسار العمل للمراسلة {c['ref_no']}", inst["name"], st["corr_id"])
        return 200, {"ok": True}

    @route("DELETE", r"/api/correspondences/(?P<id>\d+)/workflow")
    def wf_cancel(ctx):
        cid = int(ctx.params["id"])
        c = S.get_visible(ctx, cid)
        if not S.can_manage(ctx, c):
            raise ApiError(403, "لا تملك الصلاحية")
        with S.tx(ctx.conn):
            ctx.conn.execute("UPDATE wf_steps SET status='skipped' WHERE corr_id=? AND status IN('waiting','pending')", (cid,))
            ctx.conn.execute("UPDATE wf_instances SET status='cancelled', finished_at=? WHERE corr_id=? AND status='active'", (now(), cid))
            audit(ctx, "workflow_cancel", "correspondence", cid)
        return 200, {"ok": True}

    def step_tasks(ctx):
        eff = S.eff_ids(ctx.user)
        rows = ctx.conn.execute(
            "SELECT ws.id AS step_id, ws.name AS step_name, ws.kind AS step_kind, ws.due_date AS transfer_due, c.id, c.ref_no, c.subject, "
            "c.priority, c.conf_level, c.status, u.full_name AS from_name FROM wf_steps ws JOIN correspondences c ON c.id=ws.corr_id "
            f"JOIN users u ON u.id=c.created_by WHERE ws.status='pending' AND ws.user_id IN ({','.join('?' * len(eff))}) ORDER BY ws.id DESC", eff)
        return [dict(r, kind="step", transfer_id=None, transferred_at=None,
                     instruction=("اعتماد: " if r["step_kind"] == "approve" else "مراجعة: ") + r["step_name"]) for r in rows]
    S.EXT_TASKS.append(step_tasks)

    def visible_extra(u, a):
        eff = S.eff_ids(u)
        return (f"{a}.id IN (SELECT corr_id FROM wf_steps WHERE user_id IN ({','.join('?' * len(eff))}))", eff)
    S.EXT_VISIBLE.append(visible_extra)

    def detail(ctx, c):
        conn = ctx.conn
        inst = conn.execute("SELECT * FROM wf_instances WHERE corr_id=?", (c["id"],)).fetchone()
        eff = S.eff_ids(ctx.user)
        if inst:
            steps = conn.execute("SELECT ws.*, u.full_name AS user_name FROM wf_steps ws JOIN users u ON u.id=ws.user_id "
                                 "WHERE ws.corr_id=? ORDER BY ws.idx", (c["id"],)).fetchall()
            c["workflow"] = {**dict(inst), "steps": [dict(s, can_decide=(s["status"] == "pending" and s["user_id"] in eff)) for s in steps]}
        else:
            c["workflow"] = None
        sigs = conn.execute("SELECT s.*, u.full_name AS signer FROM signatures s JOIN users u ON u.id=s.user_id WHERE s.corr_id=? ORDER BY s.id", (c["id"],)).fetchall()
        c["signatures"] = [{"id": s["id"], "signer": s["signer"], "decision": s["decision"], "comment": s["comment"], "signed_at": s["signed_at"],
                            "step": json.loads(s["snapshot"]).get("step"), "doc_hash": s["doc_hash"], **verify(conn, s)} for s in sigs]
        c["can_approve"] = S.can(ctx.user, "manage_all") and c["status"] not in ("archived",)
    S.EXT_DETAIL.append(detail)

    @route("POST", r"/api/correspondences/(?P<id>\d+)/approve")
    def approve(ctx):
        """اعتماد رسمي مباشر (خارج مسار العمل) لمن يملك صلاحية الإدارة الشاملة."""
        cid = int(ctx.params["id"])
        c = S.get_visible(ctx, cid)
        if not S.can(ctx.user, "manage_all"):
            raise ApiError(403, "الاعتماد الرسمي لمن يملك صلاحية الإدارة الشاملة")
        decision = ctx.body.get("decision", "approve")
        if decision not in ("approve", "reject"):
            raise ApiError(400, "قرار غير صالح")
        comment = f_text(ctx.body, "comment", 500)
        if decision == "reject" and not comment:
            raise ApiError(400, "اكتب سبب الرفض")
        reauth(ctx)
        with S.tx(ctx.conn):
            sid = sign(ctx.conn, cid, None, ctx.user, decision, comment, None, "اعتماد مباشر")
            audit(ctx, "approve" if decision == "approve" else "reject", "correspondence", cid, comment or "اعتماد رسمي")
        return 201, {"signature_id": sid}

    @route("GET", r"/api/signatures/(?P<id>\d+)/verify")
    def sig_verify(ctx):
        s = ctx.conn.execute("SELECT * FROM signatures WHERE id=?", (int(ctx.params["id"]),)).fetchone()
        if not s:
            raise ApiError(404, "التوقيع غير موجود")
        S.get_visible(ctx, s["corr_id"])
        return 200, {"id": s["id"], "signed_at": s["signed_at"], "doc_hash": s["doc_hash"], **verify(ctx.conn, s)}
