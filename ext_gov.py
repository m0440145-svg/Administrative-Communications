"""وحدة ملحقة: الحوكمة والأمان والتشغيل — سياسات الاحتجاز والتجميد القانوني والإتلاف الموثّق، التحقق من سلسلة سجل الإجراءات،
النسخ الاحتياطي والاسترجاع، الاستيراد (مزامنة الموارد البشرية + ترحيل مراسلات الأنظمة القديمة)، الدخول الموحّد عبر وسيط موثوق،
تشفير المرفقات عند الراحة (اختياري)، ومؤشرات الأداء (KPI).
"""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from datetime import date, datetime, timedelta

VERSION = "2.0"

SCHEMA = """
CREATE TABLE IF NOT EXISTS retention_policies(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, class_key TEXT, type_id INTEGER, after_days INTEGER NOT NULL,
  action TEXT NOT NULL CHECK(action IN('archive','dispose')), active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS disposal_batches(
  id INTEGER PRIMARY KEY, created_by INTEGER NOT NULL, created_at TEXT NOT NULL, reason TEXT NOT NULL, ids TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', executed_at TEXT);
CREATE TABLE IF NOT EXISTS disposal_votes(
  batch_id INTEGER NOT NULL, user_id INTEGER NOT NULL, decision TEXT NOT NULL, comment TEXT, at TEXT NOT NULL, PRIMARY KEY(batch_id, user_id));
CREATE TABLE IF NOT EXISTS disposal_log(
  id INTEGER PRIMARY KEY, corr_id INTEGER NOT NULL, ref_no TEXT NOT NULL, subject TEXT, disposed_by INTEGER NOT NULL,
  disposed_at TEXT NOT NULL, reason TEXT NOT NULL, attachments TEXT NOT NULL);
"""

STATUS_AR = {"جديد": "new", "جارٍ": "in_progress", "جاري": "in_progress", "محال": "referred", "منجز": "closed", "مؤرشف": "archived"}
DIR_AR = {"وارد": "incoming", "صادر": "outgoing"}
SCOPE_AR = {"خارجي": "external", "داخلي": "internal"}
ROLE_AR = {"مدير النظام": "admin", "مدير إدارة": "manager", "مكتب الصادر والوارد": "clerk", "موظف": "employee"}


def install(S):
    route, ApiError = S.route, S.ApiError
    f_text, f_int, now, audit = S.f_text, S.f_int, S.now, S.audit
    S.EXT_SCHEMAS.append(SCHEMA)
    BACKUPS = os.path.join(S.DATA, "backups")

    # ───────────── تشفير المرفقات عند الراحة (Fernet) — اختياري ─────────────
    enc_keys = os.environ.get("ACP_ENC_KEY")
    if enc_keys:
        try:
            from cryptography.fernet import Fernet, MultiFernet
        except ImportError:
            raise SystemExit("ACP_ENC_KEY مضبوط لكن مكتبة cryptography غير مثبّتة: pip install cryptography (لن يبدأ الخادم دون تشفير)")
        fernets = [Fernet(k.strip().encode()) for k in enc_keys.split(",") if k.strip()]
        multi = MultiFernet(fernets)  # الأول يُشفّر، والجميع يفكّ (تدوير المفاتيح)
        S.FILE_CODEC["enc"] = lambda b: multi.encrypt(b)
        S.FILE_CODEC["dec"] = lambda b: multi.decrypt(b)

    def cli_genkey(args):
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode())
    S.EXT_CLI["genkey"] = cli_genkey

    # ───────────── الدخول الموحّد عبر وسيط موثوق (IIS/oauth2-proxy/nginx auth) ─────────────
    sso_header, sso_secret = os.environ.get("ACP_SSO_HEADER"), os.environ.get("ACP_SSO_SECRET")
    if sso_header and sso_secret:
        def sso_auth(handler, conn):
            username = handler.headers.get(sso_header)
            given = handler.headers.get("X-SSO-Secret") or ""
            if not username or not hmac.compare_digest(given, sso_secret):
                return None  # لا يُقبل الترويسة إلا من الوسيط الذي يعرف السر المشترك
            r = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username.strip()[:64],)).fetchone()
            if not r:
                return None
            u = dict(r)
            u["via"] = "SSO"
            return u
        S.EXT_AUTH.append(sso_auth)

    # ───────────── سلسلة سجل الإجراءات ─────────────
    def verify_chain(conn):
        prev, checked, legacy, bad = None, 0, 0, None
        for r in conn.execute("SELECT * FROM audit_log ORDER BY id"):
            if r["hash"] is None:
                legacy += 1
                continue
            expect_prev = prev if prev is not None else ""
            calc = S.audit_hash(r["prev_hash"], r["at"], r["user_id"], r["username"], r["action"], r["entity"], r["entity_id"], r["detail"], r["ip"])
            if (prev is not None and r["prev_hash"] != expect_prev) or calc != r["hash"]:
                bad = bad or r["id"]
            prev = r["hash"]
            checked += 1
        return {"checked": checked, "legacy_unverified": legacy, "ok": bad is None, "first_bad_id": bad}

    @route("GET", r"/api/admin/audit/verify", roles=("admin",))
    def audit_verify(ctx):
        res = verify_chain(ctx.conn)
        audit(ctx, "audit_verify", detail=("سليم" if res["ok"] else f"تلاعب عند #{res['first_bad_id']}"))
        return 200, res

    def cli_verify(args):
        c = S.connect()
        res = verify_chain(c)
        print(json.dumps(res, ensure_ascii=False))
        sys.exit(0 if res["ok"] else 2)
    S.EXT_CLI["verify-audit"] = cli_verify

    # ───────────── سياسات الاحتجاز والتجميد القانوني والإتلاف ─────────────
    def pol_values(b, creating):
        out = {}
        if creating or "name" in b:
            out["name"] = f_text(b, "name", 80, True, "الاسم")
        if creating or "after_days" in b:
            out["after_days"] = f_int(b, "after_days", 1, 36500, True)
        if creating or "action" in b:
            if b.get("action") not in ("archive", "dispose"):
                raise ApiError(400, "الإجراء: أرشفة أو إتلاف")
            out["action"] = b["action"]
        if "class_key" in b:
            out["class_key"] = f_text(b, "class_key", 40)
        if "type_id" in b:
            out["type_id"] = f_int(b, "type_id")
        if "active" in b:
            out["active"] = 1 if b["active"] else 0
        return out

    @route("GET", r"/api/admin/retention", roles=("admin",))
    def ret_list(ctx):
        return 200, [dict(r) for r in ctx.conn.execute("SELECT * FROM retention_policies ORDER BY id")]

    @route("POST", r"/api/admin/retention", roles=("admin",))
    def ret_add(ctx):
        v = pol_values(ctx.body, True)
        cur = ctx.conn.execute(f"INSERT INTO retention_policies({','.join(v)}) VALUES({','.join('?' * len(v))})", list(v.values()))
        audit(ctx, "retention_create", "retention", cur.lastrowid, v["name"])
        return 201, {"id": cur.lastrowid}

    @route("PUT", r"/api/admin/retention/(?P<id>\d+)", roles=("admin",))
    def ret_edit(ctx):
        v = pol_values(ctx.body, False)
        if not v:
            raise ApiError(400, "لا يوجد ما يُحدَّث")
        cur = ctx.conn.execute(f"UPDATE retention_policies SET {','.join(k + '=?' for k in v)} WHERE id=?", list(v.values()) + [int(ctx.params["id"])])
        if not cur.rowcount:
            raise ApiError(404, "السياسة غير موجودة")
        audit(ctx, "retention_update", "retention", int(ctx.params["id"]))
        return 200, {"ok": True}

    @route("DELETE", r"/api/admin/retention/(?P<id>\d+)", roles=("admin",))
    def ret_del(ctx):
        ctx.conn.execute("DELETE FROM retention_policies WHERE id=?", (int(ctx.params["id"]),))
        audit(ctx, "retention_delete", "retention", int(ctx.params["id"]))
        return 200, {"ok": True}

    def candidates(conn):
        out = {}
        for p in conn.execute("SELECT * FROM retention_policies WHERE active=1"):
            cutoff = (datetime.now() - timedelta(days=p["after_days"])).strftime("%Y-%m-%d %H:%M:%S")
            status = "closed" if p["action"] == "archive" else "archived"
            where, params = ["c.status=?", "c.legal_hold=0", "c.disposed_at IS NULL", "c.updated_at<=?"], [status, cutoff]
            if p["class_key"]:
                where.append("c.priority=?"); params.append(p["class_key"])
            if p["type_id"]:
                where.append("c.type_id=?"); params.append(p["type_id"])
            for r in conn.execute(f"SELECT c.id, c.ref_no, c.subject, c.updated_at FROM correspondences c WHERE {' AND '.join(where)} LIMIT 1000", params):
                out.setdefault((p["action"], r["id"]), {**dict(r), "action": p["action"], "policy": p["name"]})
        return list(out.values())

    @route("GET", r"/api/admin/retention/candidates", roles=("admin",))
    def ret_candidates(ctx):
        return 200, candidates(ctx.conn)

    def do_dispose(ctx, ids, reason):
        """يحذف ملفات المرفقات (العادي والبارد) ويُبقي بيانات المراسلة وسجل إتلاف ببصمات الملفات."""
        done = 0
        for cid in ids:
            c = ctx.conn.execute("SELECT * FROM correspondences WHERE id=?", (cid,)).fetchone()
            atts = ctx.conn.execute("SELECT id, filename, stored_name, sha256, cold FROM attachments WHERE corr_id=? AND removed_at IS NULL", (cid,)).fetchall()
            for a in atts:
                try:
                    os.remove(os.path.join(S.COLD if a["cold"] else S.UPLOADS, a["stored_name"]))
                except FileNotFoundError:
                    pass
                ctx.conn.execute("UPDATE attachments SET removed_at=? WHERE id=?", (now(), a["id"]))
            ctx.conn.execute("UPDATE correspondences SET disposed_at=?, updated_at=? WHERE id=?", (now(), now(), cid))
            ctx.conn.execute("INSERT INTO disposal_log(corr_id,ref_no,subject,disposed_by,disposed_at,reason,attachments) VALUES(?,?,?,?,?,?,?)",
                             (cid, c["ref_no"], c["subject"], ctx.user["id"], now(), reason,
                              json.dumps([{"filename": a["filename"], "sha256": a["sha256"]} for a in atts], ensure_ascii=False)))
            audit(ctx, "retention_dispose", "correspondence", cid, reason)
            done += 1
        return done

    def approvals_required(conn):
        return int(S.get_settings(conn)["disposal_approvals"])

    @route("POST", r"/api/admin/retention/apply", roles=("admin",))
    def ret_apply(ctx):
        b = ctx.body
        action = b.get("action")
        ids = b.get("ids")
        if action not in ("archive", "dispose") or not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(i, int) for i in ids):
            raise ApiError(400, "طلب غير صالح")
        if not S.verify_password(str(b.get("password", "")), ctx.user["password_hash"]):
            raise ApiError(400, "كلمة المرور غير صحيحة — يلزم تأكيد هذا الإجراء بها")
        if hasattr(S, "require_second_factor"):
            S.require_second_factor(ctx)
        if action == "dispose" and approvals_required(ctx.conn) > 0:
            raise ApiError(400, "الإتلاف يتطلب اعتماد لجنة الوثائق: أنشئ دفعة إتلاف من «الاحتجاز والإتلاف» لتُعرض على اللجنة")
        reason = f_text(b, "reason", 300, action == "dispose", "سبب الإتلاف")
        allowed = {c["id"] for c in candidates(ctx.conn) if c["action"] == action}
        bad = [i for i in ids if i not in allowed]
        if bad:
            raise ApiError(400, f"مراسلات غير مؤهلة حسب السياسات أو عليها تجميد قانوني: {bad[:5]}")
        done = 0
        with S.tx(ctx.conn):
            if action == "archive":
                for cid in ids:
                    ctx.conn.execute("UPDATE correspondences SET status='archived', updated_at=? WHERE id=?", (now(), cid))
                    audit(ctx, "retention_archive", "correspondence", cid, reason or "سياسة احتجاز")
                    done += 1
            else:
                done = do_dispose(ctx, ids, reason)
        return 200, {"done": done}

    # ───────────── لجنة الوثائق: اعتماد الإتلاف بفصل المهام ─────────────
    S.PERMS["disposal_committee"] = "عضوية لجنة الوثائق (التصويت على الإتلاف)"
    S.SETTING_DEFAULTS.update({"disposal_approvals": "0", "cold_after_days": "0"})

    def is_committee(conn, u):
        if u["role"] == "admin":
            return True
        return "disposal_committee" in u.get("perms", ())

    @route("POST", r"/api/admin/disposal-batches", roles=("admin",))
    def batch_create(ctx):
        ids, reason = ctx.body.get("ids"), f_text(ctx.body, "reason", 300, True, "سبب الإتلاف")
        if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(i, int) for i in ids):
            raise ApiError(400, "قائمة غير صالحة")
        allowed = {c["id"] for c in candidates(ctx.conn) if c["action"] == "dispose"}
        bad = [i for i in ids if i not in allowed]
        if bad:
            raise ApiError(400, f"مراسلات غير مؤهلة للإتلاف: {bad[:5]}")
        cur = ctx.conn.execute("INSERT INTO disposal_batches(created_by,created_at,reason,ids) VALUES(?,?,?,?)", (ctx.user["id"], now(), reason, json.dumps(ids)))
        audit(ctx, "disposal_batch_create", "batch", cur.lastrowid, f"{len(ids)} مراسلة — {reason}")
        roles = {r[0] for r in ctx.conn.execute("SELECT role FROM role_perms WHERE perm='disposal_committee' AND allowed=1")} | {"admin"}
        for (uid,) in ctx.conn.execute(f"SELECT id FROM users WHERE active=1 AND role IN ({','.join('?' * len(roles))}) AND id<>?", list(roles) + [ctx.user["id"]]).fetchall():
            S.notify(ctx.conn, uid, "disposal_vote", "دفعة إتلاف بانتظار تصويتك", f"{len(ids)} مراسلة — {reason}", None, dedupe=f"batch:{cur.lastrowid}:{uid}")
        return 201, {"id": cur.lastrowid}

    @route("GET", r"/api/admin/disposal-batches")
    def batch_list(ctx):
        if not is_committee(ctx.conn, ctx.user):
            raise ApiError(403, "لجنة الوثائق فقط")
        out = []
        for b in ctx.conn.execute("SELECT b.*, u.full_name AS creator FROM disposal_batches b JOIN users u ON u.id=b.created_by ORDER BY b.id DESC LIMIT 50"):
            votes = [dict(v) for v in ctx.conn.execute("SELECT v.decision, v.comment, v.at, u.full_name FROM disposal_votes v JOIN users u ON u.id=v.user_id WHERE v.batch_id=?", (b["id"],))]
            ids = json.loads(b["ids"])
            sample = [dict(r) for r in ctx.conn.execute(f"SELECT id, ref_no, subject FROM correspondences WHERE id IN ({','.join('?' * len(ids))}) LIMIT 8", ids)]
            out.append({**{k: b[k] for k in ("id", "creator", "created_at", "reason", "status", "executed_at")}, "count": len(ids), "sample": sample, "votes": votes,
                        "required": approvals_required(ctx.conn), "i_voted": any(v["full_name"] == ctx.user["full_name"] for v in votes)})
        return 200, out

    @route("POST", r"/api/admin/disposal-batches/(?P<id>\d+)/vote")
    def batch_vote(ctx):
        if not is_committee(ctx.conn, ctx.user):
            raise ApiError(403, "لجنة الوثائق فقط")
        b = ctx.conn.execute("SELECT * FROM disposal_batches WHERE id=?", (int(ctx.params["id"]),)).fetchone()
        if not b or b["status"] != "pending":
            raise ApiError(404, "الدفعة غير موجودة أو أُغلق التصويت عليها")
        if b["created_by"] == ctx.user["id"]:
            raise ApiError(403, "لا يصوّت من أنشأ الدفعة (فصل المهام)")
        decision = ctx.body.get("decision")
        if decision not in ("approve", "reject"):
            raise ApiError(400, "قرار غير صالح")
        comment = f_text(ctx.body, "comment", 300, decision == "reject", "سبب الرفض")
        if not S.verify_password(str(ctx.body.get("password", "")), ctx.user["password_hash"]):
            raise ApiError(400, "كلمة المرور غير صحيحة")
        if hasattr(S, "require_second_factor"):
            S.require_second_factor(ctx)
        try:
            with S.tx(ctx.conn):
                ctx.conn.execute("INSERT INTO disposal_votes VALUES(?,?,?,?,?)", (b["id"], ctx.user["id"], decision, comment, now()))
                approves = ctx.conn.execute("SELECT COUNT(*) FROM disposal_votes WHERE batch_id=? AND decision='approve'", (b["id"],)).fetchone()[0]
                status = "rejected" if decision == "reject" else ("approved" if approves >= max(1, approvals_required(ctx.conn)) else "pending")
                if status != "pending":
                    ctx.conn.execute("UPDATE disposal_batches SET status=? WHERE id=?", (status, b["id"]))
                audit(ctx, "disposal_vote", "batch", b["id"], f"{decision} — {comment or ''}")
        except sqlite3.IntegrityError:
            raise ApiError(400, "سبق أن صوّتّ على هذه الدفعة")
        return 200, {"status": status}

    @route("POST", r"/api/admin/disposal-batches/(?P<id>\d+)/execute", roles=("admin",))
    def batch_execute(ctx):
        b = ctx.conn.execute("SELECT * FROM disposal_batches WHERE id=?", (int(ctx.params["id"]),)).fetchone()
        if not b or b["status"] != "approved":
            raise ApiError(400, "الدفعة غير معتمدة من اللجنة")
        if not S.verify_password(str(ctx.body.get("password", "")), ctx.user["password_hash"]):
            raise ApiError(400, "كلمة المرور غير صحيحة")
        if hasattr(S, "require_second_factor"):
            S.require_second_factor(ctx)
        ids = json.loads(b["ids"])
        allowed = {c["id"] for c in candidates(ctx.conn) if c["action"] == "dispose"}
        still = [i for i in ids if i in allowed]  # قد يُجمَّد بعضها قانونيًا بعد الاعتماد فيُستثنى
        with S.tx(ctx.conn):
            done = do_dispose(ctx, still, b["reason"] + f" (دفعة #{b['id']} باعتماد اللجنة)")
            ctx.conn.execute("UPDATE disposal_batches SET status='executed', executed_at=? WHERE id=?", (now(), b["id"]))
        return 200, {"done": done, "skipped": len(ids) - len(still)}

    # ───────────── الأرشيف الوسيط (البارد) ─────────────
    def move_cold(conn):
        days = int(S.get_settings(conn)["cold_after_days"])
        if not days:
            return {"moved": 0}
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        moved = 0
        for a in conn.execute("SELECT a.id, a.stored_name FROM attachments a JOIN correspondences c ON c.id=a.corr_id WHERE c.status='archived' AND c.updated_at<=? "
                              "AND a.cold=0 AND a.removed_at IS NULL LIMIT 500", (cutoff,)).fetchall():
            src = os.path.join(S.UPLOADS, a["stored_name"])
            if os.path.isfile(src):
                shutil.move(src, os.path.join(S.COLD, a["stored_name"]))
                conn.execute("UPDATE attachments SET cold=1 WHERE id=?", (a["id"],))
                moved += 1
        return {"moved": moved}
    S.EXT_TICK.append(move_cold)

    @route("POST", r"/api/admin/cold/run", roles=("admin",))
    def cold_run(ctx):
        res = move_cold(ctx.conn)
        audit(ctx, "cold_tier", detail=f"نُقل {res['moved']} ملفًا للأرشيف الوسيط")
        return 200, res
    @route("GET", r"/api/admin/disposal-log", roles=("admin",))
    def disposal_log(ctx):
        return 200, [dict(r) for r in ctx.conn.execute(
            "SELECT d.*, u.full_name AS by_name FROM disposal_log d JOIN users u ON u.id=d.disposed_by ORDER BY d.id DESC LIMIT 200")]

    @route("POST", r"/api/correspondences/(?P<id>\d+)/legal-hold", roles=("admin",))
    def legal_hold(ctx):
        cid = int(ctx.params["id"])
        S.get_visible(ctx, cid)
        hold = 1 if ctx.body.get("hold") else 0
        reason = f_text(ctx.body, "reason", 300, bool(hold), "سبب التجميد")
        ctx.conn.execute("UPDATE correspondences SET legal_hold=? WHERE id=?", (hold, cid))
        audit(ctx, "legal_hold" if hold else "legal_release", "correspondence", cid, reason or "رفع التجميد")
        return 200, {"legal_hold": hold}

    # ───────────── النسخ الاحتياطي والاسترجاع ─────────────
    def sha_file(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    OFFSITE = {"ok": None, "at": None, "error": None, "file": None}

    def make_backup(data_dir=None):
        data_dir = data_dir or S.DATA
        out_dir = os.path.join(data_dir, "backups")
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(out_dir, f"acp-{stamp}.zip")
        tmp = tempfile.mkdtemp(prefix="acpbk-")
        try:
            snap = os.path.join(tmp, "acp.db")
            src = sqlite3.connect(os.path.join(data_dir, "acp.db"))
            dst = sqlite3.connect(snap)
            src.backup(dst)  # نسخة متسقة حتى والخادم يعمل
            dst.close(); src.close()
            manifest = {"version": VERSION, "created_at": now(), "files": {}}
            with zipfile.ZipFile(path + ".part", "w", zipfile.ZIP_DEFLATED) as z:
                z.write(snap, "acp.db")
                manifest["files"]["acp.db"] = sha_file(snap)
                up = os.path.join(data_dir, "uploads")
                if os.path.isdir(up):
                    for n in sorted(os.listdir(up)):
                        fp = os.path.join(up, n)
                        if os.path.isfile(fp):
                            z.write(fp, f"uploads/{n}")
                            manifest["files"][f"uploads/{n}"] = sha_file(fp)
                bd = os.path.join(data_dir, "branding")  # الشعار ضمن النسخة الاحتياطية
                if os.path.isdir(bd):
                    for n in sorted(os.listdir(bd)):
                        fp = os.path.join(bd, n)
                        if os.path.isfile(fp):
                            z.write(fp, f"branding/{n}")
                            manifest["files"][f"branding/{n}"] = sha_file(fp)
                cd = os.path.join(data_dir, "cold")
                if os.path.isdir(cd):
                    for n in sorted(os.listdir(cd)):
                        fp = os.path.join(cd, n)
                        if os.path.isfile(fp):
                            z.write(fp, f"cold/{n}")
                            manifest["files"][f"cold/{n}"] = sha_file(fp)
                key = os.path.join(data_dir, "sign.key")
                if os.path.isfile(key):  # لازم للتحقق من التوقيعات بعد الاسترجاع — احفظ النسخة في مكان آمن
                    z.write(key, "sign.key")
                    manifest["files"]["sign.key"] = sha_file(key)
                z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            os.replace(path + ".part", path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        copy_dir = os.environ.get("ACP_BACKUP_COPY_DIR")  # نسخة في موقع بديل (مشاركة شبكية/قرص خارجي) مع التحقق من البصمة
        if copy_dir:
            try:
                os.makedirs(copy_dir, exist_ok=True)
                dst_p = os.path.join(copy_dir, os.path.basename(path))
                shutil.copy2(path, dst_p)
                if sha_file(dst_p) != sha_file(path):
                    raise IOError("بصمة النسخة البديلة لا تطابق الأصل")
                OFFSITE.update(ok=True, at=now(), error=None, file=os.path.basename(path))
            except Exception as e:
                OFFSITE.update(ok=False, at=now(), error=str(e)[:200])
        return path
    S.make_backup = make_backup

    def prune_backups(keep):
        files = sorted(f for f in os.listdir(BACKUPS) if f.startswith("acp-") and f.endswith(".zip")) if os.path.isdir(BACKUPS) else []
        for f in files[:-keep] if keep > 0 else []:
            os.remove(os.path.join(BACKUPS, f))

    def restore_backup(zip_path, data_dir, force=False):
        with zipfile.ZipFile(zip_path) as z:
            manifest = json.loads(z.read("manifest.json"))
            tmp = tempfile.mkdtemp(prefix="acprs-")
            try:
                for name, digest in manifest["files"].items():
                    if name.startswith("/") or ".." in name:
                        raise ValueError("اسم ملف غير آمن داخل النسخة")
                    z.extract(name, tmp)
                    if sha_file(os.path.join(tmp, name)) != digest:
                        raise ValueError(f"فشل التحقق من سلامة الملف: {name}")
                db = os.path.join(data_dir, "acp.db")
                os.makedirs(data_dir, exist_ok=True)
                if os.path.exists(db):
                    if not force:
                        raise FileExistsError("توجد قاعدة بيانات في الوجهة؛ استخدم --force (تُحفظ نسخة قديمة بجانبها)")
                    shutil.move(db, db + f".bak-{datetime.now():%Y%m%d%H%M%S}")
                    for ext in ("-wal", "-shm"):
                        if os.path.exists(db + ext):
                            os.remove(db + ext)
                shutil.move(os.path.join(tmp, "acp.db"), db)
                if os.path.isdir(os.path.join(tmp, "branding")):
                    os.makedirs(os.path.join(data_dir, "branding"), exist_ok=True)
                    for n in os.listdir(os.path.join(tmp, "branding")):
                        shutil.move(os.path.join(tmp, "branding", n), os.path.join(data_dir, "branding", n))
                cdir = os.path.join(data_dir, "cold")
                os.makedirs(cdir, exist_ok=True)
                if os.path.isdir(os.path.join(tmp, "cold")):
                    for n in os.listdir(os.path.join(tmp, "cold")):
                        shutil.move(os.path.join(tmp, "cold", n), os.path.join(cdir, n))
                up = os.path.join(data_dir, "uploads")
                os.makedirs(up, exist_ok=True)
                if os.path.isdir(os.path.join(tmp, "uploads")):
                    for n in os.listdir(os.path.join(tmp, "uploads")):
                        shutil.move(os.path.join(tmp, "uploads", n), os.path.join(up, n))
                if os.path.exists(os.path.join(tmp, "sign.key")):
                    shutil.move(os.path.join(tmp, "sign.key"), os.path.join(data_dir, "sign.key"))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        return manifest
    S.restore_backup = restore_backup

    def cli_backup(args):
        print(make_backup())

    def cli_restore(args):
        if not args:
            print("الاستخدام: python server.py restore <ملف.zip> [--force]  (أوقف الخادم أولًا)")
            sys.exit(1)
        try:
            m = restore_backup(args[0], S.DATA, "--force" in args)
        except Exception as e:
            print("فشل الاسترجاع:", e)
            sys.exit(1)
        print(f"تم الاسترجاع من نسخة بتاريخ {m['created_at']} ({len(m['files'])} ملفًا)")
    S.EXT_CLI["backup"] = cli_backup
    S.EXT_CLI["restore"] = cli_restore

    def backup_worker():
        hours = float(os.environ.get("ACP_BACKUP_HOURS", "24"))
        keep = int(os.environ.get("ACP_BACKUP_KEEP", "14"))
        if hours <= 0:
            return
        while True:
            try:
                files = sorted(os.listdir(BACKUPS)) if os.path.isdir(BACKUPS) else []
                last = os.path.getmtime(os.path.join(BACKUPS, files[-1])) if files else 0
                if time.time() - last >= hours * 3600:
                    print("نسخة احتياطية تلقائية:", make_backup(), flush=True)
                    prune_backups(keep)
            except Exception:
                traceback.print_exc()
            time.sleep(1800)
    S.EXT_START.append(lambda: threading.Thread(target=backup_worker, daemon=True).start())

    @route("POST", r"/api/admin/backup", roles=("admin",))
    def backup_now(ctx):
        path = make_backup()
        audit(ctx, "backup", detail=os.path.basename(path))
        return 201, {"name": os.path.basename(path), "size": os.path.getsize(path)}

    @route("GET", r"/api/admin/backups", roles=("admin",))
    def backups_list(ctx):
        files = sorted((f for f in os.listdir(BACKUPS) if f.endswith(".zip")), reverse=True) if os.path.isdir(BACKUPS) else []
        return 200, [{"name": f, "size": os.path.getsize(os.path.join(BACKUPS, f)),
                      "at": datetime.fromtimestamp(os.path.getmtime(os.path.join(BACKUPS, f))).strftime("%Y-%m-%d %H:%M")} for f in files]

    @route("GET", r"/api/admin/backups/download", roles=("admin",))
    def backup_download(ctx):
        name = os.path.basename(ctx.query.get("name", ""))
        path = os.path.join(BACKUPS, name)
        if not re.fullmatch(r"acp-\d{8}-\d{6}\.zip", name) or not os.path.isfile(path):
            raise ApiError(404, "النسخة غير موجودة")
        audit(ctx, "backup_download", detail=name)
        with open(path, "rb") as f:
            return S.Raw(200, f.read(), {"Content-Type": "application/zip", "Content-Disposition": f"attachment; filename={name}"})

    # ───────────── الاستيراد ─────────────
    def read_csv(ctx, limit=5_000_000):
        n = int(ctx.h.headers.get("Content-Length") or 0)
        if n <= 0 or n > limit:
            raise ApiError(413 if n else 400, "ملف CSV فارغ أو كبير جدًا (الحد 5MB)")
        raw = ctx.h.rfile.read(n)
        for enc in ("utf-8-sig", "cp1256"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                text = None
        if text is None:
            raise ApiError(400, "ترميز الملف غير مدعوم (استخدم UTF-8)")
        rows = list(csv.DictReader(io.StringIO(text)))
        if len(rows) > 5000:
            raise ApiError(400, "الحد الأقصى 5000 صف في كل استيراد")
        return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rows]

    def by_name(conn, table, name, create):
        if not name:
            return None
        r = conn.execute(f"SELECT id FROM {table} WHERE name=?", (name,)).fetchone()
        if r:
            return r[0]
        if create:
            return conn.execute(f"INSERT INTO {table}(name) VALUES(?)", (name,)).lastrowid
        raise ValueError(f"غير موجود: {name}")

    @route("POST", r"/api/admin/import/users", roles=("admin",))
    def import_users(ctx):
        """مزامنة الموظفين من ملف CSV (تصدير الموارد البشرية): username, full_name, job_title, phone, email, unit, role, clearance, active."""
        rows = read_csv(ctx)
        dry = ctx.query.get("dry_run") == "1"
        report, created, updated, errors = [], 0, 0, 0
        with S.tx(ctx.conn):
            for i, r in enumerate(rows, 2):
                try:
                    un = r.get("username", "")
                    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", un):
                        raise ValueError("اسم مستخدم غير صالح")
                    role = ROLE_AR.get(r.get("role", ""), r.get("role", "")) or None
                    if role and role not in S.ROLES:
                        raise ValueError("دور غير صالح")
                    clr = int(r["clearance"]) if r.get("clearance", "").isdigit() else None
                    if clr is not None and clr > 2:
                        raise ValueError("درجة الخصوصية 0–2")
                    dept = by_name(ctx.conn, "departments", r.get("unit", ""), True)
                    active = None if r.get("active", "") == "" else (0 if r["active"].lower() in ("0", "false", "لا", "no", "معطل") else 1)
                    email = r.get("email") or None
                    if email and not S.EMAIL_RE.match(email):
                        raise ValueError("بريد غير صالح")
                    ex = ctx.conn.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()
                    if ex:
                        sets = {"full_name": r.get("full_name") or None, "job_title": r.get("job_title") or None, "phone": r.get("phone") or None,
                                "email": email, "department_id": dept, "role": role, "clearance": clr, "active": active}
                        sets = {k: v for k, v in sets.items() if v is not None}
                        if sets:
                            ctx.conn.execute(f"UPDATE users SET {','.join(k + '=?' for k in sets)} WHERE id=?", list(sets.values()) + [ex[0]])
                        updated += 1
                        report.append({"row": i, "username": un, "result": "updated"})
                    else:
                        if not r.get("full_name"):
                            raise ValueError("الاسم الكامل مطلوب للموظف الجديد")
                        pw = secrets.token_urlsafe(9) + "7a"
                        ctx.conn.execute(
                            "INSERT INTO users(username,full_name,password_hash,role,department_id,clearance,must_change,created_at,job_title,phone,email,active) "
                            "VALUES(?,?,?,?,?,?,1,?,?,?,?,?)", (un, r["full_name"], S.hash_password(pw), role or "employee", dept, clr or 0, now(),
                                                               r.get("job_title") or None, r.get("phone") or None, email, 1 if active is None else active))
                        created += 1
                        report.append({"row": i, "username": un, "result": "created", "temp_password": pw})
                except Exception as e:
                    errors += 1
                    report.append({"row": i, "username": r.get("username"), "result": "error", "error": str(e)})
            if dry:  # فحص فقط: نتراجع عن كل التغييرات ثم نفتح معاملة فارغة ليُنهيها tx() بـ COMMIT
                ctx.conn.execute("ROLLBACK")
                ctx.conn.execute("BEGIN IMMEDIATE")
            audit(ctx, "import_users", detail=f"{'تجريبي ' if dry else ''}منشأ {created} · محدَّث {updated} · أخطاء {errors}")
        for x in report:
            if dry:
                x.pop("temp_password", None)
        return 200, {"dry_run": dry, "created": created, "updated": updated, "errors": errors, "rows": report}

    def next_ref(conn, scope, direction):
        prefix = ("EXT" if scope == "external" else "INT") + "-" + ("IN" if direction == "incoming" else "OUT")
        key = f"{prefix}-{date.today().year}"
        r = conn.execute("SELECT n FROM counters WHERE k=?", (key,)).fetchone()
        n = (r[0] if r else 0) + 1
        conn.execute("INSERT INTO counters(k,n) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET n=excluded.n", (key, n))
        return f"{key}-{n:05d}"

    @route("POST", r"/api/admin/import/correspondences", roles=("admin",))
    def import_corr(ctx):
        """ترحيل مراسلات من نظام قديم (CSV). الأعمدة: legacy_ref, direction, scope, subject, party, party_ref, doc_date,
        classification, type, folder, unit, status, summary. يدعم dry_run=1 للفحص قبل التنفيذ."""
        rows = read_csv(ctx)
        dry = ctx.query.get("dry_run") == "1"
        conn, u = ctx.conn, ctx.user
        cls = {k: v for k, v in S.classes(conn, False).items()}
        by_label = {v["label"]: k for k, v in cls.items()}
        report, created, skipped, errors, new_ids = [], 0, 0, 0, []
        with S.tx(conn):
            for i, r in enumerate(rows, 2):
                try:
                    direction = DIR_AR.get(r.get("direction", ""), r.get("direction", ""))
                    scope = SCOPE_AR.get(r.get("scope", ""), r.get("scope", "")) or "external"
                    if direction not in ("incoming", "outgoing") or scope not in ("external", "internal"):
                        raise ValueError("الاتجاه/النطاق غير صالح")
                    subject, party = r.get("subject", ""), r.get("party", "")
                    if not subject or not party or len(subject) > 300:
                        raise ValueError("الموضوع والجهة مطلوبان")
                    dd = r.get("doc_date", "") or date.today().isoformat()
                    datetime.strptime(dd, "%Y-%m-%d")
                    pr = r.get("classification", "") or "normal"
                    pr = pr if pr in cls else by_label.get(pr)
                    if not pr:
                        raise ValueError("تصنيف غير معروف")
                    status = STATUS_AR.get(r.get("status", ""), r.get("status", "")) or "archived"
                    if status not in S.STATUSES:
                        raise ValueError("حالة غير صالحة")
                    legacy = r.get("legacy_ref") or None
                    if legacy and conn.execute("SELECT 1 FROM correspondences WHERE legacy_ref=?", (legacy,)).fetchone():
                        skipped += 1
                        report.append({"row": i, "result": "skipped", "reason": "مستورد سابقًا"})
                        continue
                    type_id = by_name(conn, "ctypes", r.get("type", ""), True)
                    folder_id = by_name(conn, "folders", r.get("folder", ""), True)
                    dept = by_name(conn, "departments", r.get("unit", ""), True) or u["department_id"]
                    ref = next_ref(conn, scope, direction)
                    cur = conn.execute(
                        "INSERT INTO correspondences(ref_no,scope,direction,subject,summary,party,party_ref,doc_date,priority,conf_level,status,"
                        "department_id,created_by,created_at,updated_at,type_id,folder_id,legacy_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (ref, scope, direction, subject, r.get("summary") or None, party, r.get("party_ref") or None, dd, pr,
                         cls[pr]["conf_level"], status, dept, u["id"], now(), now(), type_id, folder_id, legacy))
                    if dept:
                        conn.execute("INSERT OR IGNORE INTO corr_units VALUES(?,?)", (cur.lastrowid, dept))
                    conn.execute("INSERT OR IGNORE INTO reads VALUES(?,?,?)", (cur.lastrowid, u["id"], now()))
                    new_ids.append(cur.lastrowid)
                    created += 1
                    report.append({"row": i, "result": "created", "ref_no": ref})
                except Exception as e:
                    errors += 1
                    report.append({"row": i, "result": "error", "error": str(e)})
            if dry:
                conn.execute("ROLLBACK")
                conn.execute("BEGIN IMMEDIATE")
            audit(ctx, "import_correspondences", detail=f"{'تجريبي ' if dry else ''}منشأ {created} · متخطى {skipped} · أخطاء {errors}")
        if not dry and hasattr(S, "index_corr"):
            for cid in new_ids:
                S.index_corr(conn, cid)
        return 200, {"dry_run": dry, "created": created, "skipped": skipped, "errors": errors, "rows": report[:500]}

    # ───────────── مؤشرات الأداء (KPI) ─────────────
    def compute_kpi(conn, u, q):
        vs, vp = S.visible_clause(u)
        where, p = [vs], list(vp)
        if S.DATE_RE.match(q.get("from", "")):
            where.append("c.doc_date>=?"); p.append(q["from"])
        if S.DATE_RE.match(q.get("to", "")):
            where.append("c.doc_date<=?"); p.append(q["to"])
        w = " AND ".join(where)
        open_ = "c.status NOT IN('closed','archived')"

        def one(sql, extra=()):
            return conn.execute(sql, p + list(extra)).fetchone()[0]
        total = one(f"SELECT COUNT(*) FROM correspondences c WHERE {w}")
        n_open = one(f"SELECT COUNT(*) FROM correspondences c WHERE {w} AND {open_}")
        overdue = one(f"SELECT COUNT(*) FROM correspondences c WHERE {w} AND {open_} AND c.due_date IS NOT NULL AND c.due_date<date('now','localtime')")
        with_due = one(f"SELECT COUNT(*) FROM correspondences c WHERE {w} AND c.status IN('closed','archived') AND c.due_date IS NOT NULL")
        on_time = one(f"SELECT COUNT(*) FROM correspondences c WHERE {w} AND c.status IN('closed','archived') AND c.due_date IS NOT NULL AND date(c.updated_at)<=c.due_date")
        avg_close = one(f"SELECT AVG(julianday(c.updated_at)-julianday(c.created_at)) FROM correspondences c WHERE {w} AND c.status IN('closed','archived')")
        avg_first = conn.execute(
            f"SELECT AVG((julianday(t.done_at)-julianday(t.created_at))*24) FROM transfers t JOIN correspondences c ON c.id=t.corr_id WHERE t.status='done' AND {w}", p).fetchone()[0]
        needs = one(f"SELECT COUNT(*) FROM correspondences c WHERE {w} AND c.direction='incoming' AND c.needs_reply=1")
        replied = one(f"SELECT COUNT(*) FROM correspondences c WHERE {w} AND c.direction='incoming' AND c.needs_reply=1 AND EXISTS(SELECT 1 FROM correspondences r WHERE r.parent_id=c.id)")
        avg_reply = conn.execute(
            f"SELECT AVG(julianday(r.created_at)-julianday(c.created_at)) FROM correspondences c JOIN correspondences r ON r.parent_id=c.id WHERE c.needs_reply=1 AND {w}", p).fetchone()[0]
        buckets = {}
        for label, lo, hi in (("0-7", 0, 7), ("8-30", 8, 30), ("31-90", 31, 90), ("90+", 91, 100000)):
            buckets[label] = one(f"SELECT COUNT(*) FROM correspondences c WHERE {w} AND {open_} AND julianday('now','localtime')-julianday(c.created_at)>={lo} AND julianday('now','localtime')-julianday(c.created_at)<{hi + 1}")
        monthly = [dict(r) for r in conn.execute(
            f"SELECT substr(c.created_at,1,7) AS month, COUNT(*) AS created, SUM(c.status IN('closed','archived')) AS closed FROM correspondences c "
            f"WHERE {w} GROUP BY 1 ORDER BY 1 DESC LIMIT 12", p)][::-1]
        units = [dict(r) for r in conn.execute(
            f"SELECT COALESCE(d.name,'بدون وحدة') AS unit, COUNT(*) AS total, SUM({open_}) AS open_n, "
            f"SUM({open_} AND c.due_date IS NOT NULL AND c.due_date<date('now','localtime')) AS overdue FROM correspondences c "
            f"LEFT JOIN corr_units cu ON cu.corr_id=c.id LEFT JOIN departments d ON d.id=cu.dept_id WHERE {w} GROUP BY cu.dept_id ORDER BY open_n DESC", p)]
        load = [dict(r) for r in conn.execute(
            f"SELECT us.full_name AS employee, COUNT(*) AS pending FROM transfers t JOIN users us ON us.id=t.to_user JOIN correspondences c ON c.id=t.corr_id "
            f"WHERE t.status='pending' AND {w} GROUP BY t.to_user ORDER BY pending DESC LIMIT 10", p)]
        pct = lambda a, b: round(100 * a / b, 1) if b else None
        # SLA بحسب سياسة التصنيف (ساعات العمل): ما أُنجز في موعده من الإجمالي المقيَّد بمهلة
        sla_total = one(f"SELECT COUNT(*) FROM correspondences c WHERE {w} AND c.sla_due_at IS NOT NULL AND (c.sla_done_at IS NOT NULL OR c.sla_due_at<datetime('now','localtime'))")
        sla_met = one(f"SELECT COUNT(*) FROM correspondences c WHERE {w} AND c.sla_due_at IS NOT NULL AND c.sla_done_at IS NOT NULL AND c.sla_done_at<=c.sla_due_at")
        sla_breached_open = one(f"SELECT COUNT(*) FROM correspondences c WHERE {w} AND {open_} AND c.sla_due_at IS NOT NULL AND c.sla_done_at IS NULL AND c.sla_due_at<datetime('now','localtime')")
        src = {r[0]: r[1] for r in conn.execute(f"SELECT c.source, COUNT(*) FROM correspondences c WHERE {w} GROUP BY c.source", p)}
        emp_turn = [dict(r) for r in conn.execute(
            f"SELECT us.full_name AS employee, COUNT(*) AS done, ROUND(AVG((julianday(t.done_at)-julianday(t.created_at))*24),1) AS avg_hours FROM transfers t "
            f"JOIN users us ON us.id=t.to_user JOIN correspondences c ON c.id=t.corr_id WHERE t.status='done' AND {w} GROUP BY t.to_user ORDER BY avg_hours DESC LIMIT 10", p)]
        unit_turn = [dict(r) for r in conn.execute(
            f"SELECT COALESCE(d.name,'بدون وحدة') AS unit, ROUND(AVG(julianday(c.updated_at)-julianday(c.created_at)),1) AS avg_days, COUNT(*) AS closed FROM correspondences c "
            f"LEFT JOIN corr_units cu ON cu.corr_id=c.id LEFT JOIN departments d ON d.id=cu.dept_id WHERE {w} AND c.status IN('closed','archived') GROUP BY cu.dept_id ORDER BY avg_days DESC", p)]
        return {
            "total": total, "open": n_open, "overdue": overdue, "overdue_pct": pct(overdue, n_open),
            "sla_compliance_pct": pct(on_time, with_due), "avg_close_days": round(avg_close, 1) if avg_close is not None else None,
            "avg_first_response_hours": round(avg_first, 1) if avg_first is not None else None,
            "reply_rate_pct": pct(replied, needs), "avg_reply_days": round(avg_reply, 1) if avg_reply is not None else None,
            "backlog_age": buckets, "monthly": monthly, "units": units, "workload": load,
            "sla_policy_compliance_pct": pct(sla_met, sla_total), "sla_breached_open": sla_breached_open,
            "by_source": src, "electronic_share_pct": pct(sum(v for k, v in src.items() if k != "manual"), total),
            "employee_turnaround": emp_turn, "unit_turnaround": unit_turn}
    S.compute_kpi = compute_kpi

    @route("GET", r"/api/kpi")
    def kpi(ctx):
        u = ctx.user
        if not (u["role"] in ("admin", "manager", "clerk") or S.can(u, "export")):
            raise ApiError(403, "المؤشرات متاحة للمدراء ومكتب الصادر والوارد")
        return 200, compute_kpi(ctx.conn, u, ctx.query)

    # ───────────── مزامنة الإجازات من الموارد البشرية (تفويض تلقائي) ─────────────
    @route("POST", r"/api/admin/import/leaves", roles=("admin",))
    def import_leaves(ctx):
        """CSV: username, delegate, from, to, note — لكل إجازة يُنشأ تفويض إلى البديل خلال المدة (تفعيل تلقائي بالتكامل مع الموارد البشرية)."""
        rows = read_csv(ctx)
        dry = ctx.query.get("dry_run") == "1"
        report, created, skipped, errors = [], 0, 0, 0
        with S.tx(ctx.conn):
            for i, r in enumerate(rows, 2):
                try:
                    frm = ctx.conn.execute("SELECT id FROM users WHERE username=? AND active=1", (r.get("username", ""),)).fetchone()
                    to = ctx.conn.execute("SELECT id FROM users WHERE username=? AND active=1", (r.get("delegate", ""),)).fetchone()
                    if not frm or not to or frm[0] == to[0]:
                        raise ValueError("اسم الموظف أو البديل غير صالح")
                    a, b = r.get("from", ""), r.get("to", "")
                    da, db = datetime.strptime(a, "%Y-%m-%d"), datetime.strptime(b, "%Y-%m-%d")
                    if db < da or (db - da).days > 180:
                        raise ValueError("مدة غير صالحة (الحد 180 يومًا)")
                    if ctx.conn.execute("SELECT 1 FROM delegations WHERE from_user=? AND to_user=? AND start_date=? AND end_date=? AND active=1", (frm[0], to[0], a, b)).fetchone():
                        skipped += 1
                        report.append({"row": i, "result": "skipped", "reason": "موجود مسبقًا"})
                        continue
                    ctx.conn.execute("INSERT INTO delegations(from_user,to_user,start_date,end_date,note,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                                     (frm[0], to[0], a, b, r.get("note") or "إجازة (مزامنة الموارد البشرية)", ctx.user["id"], now()))
                    created += 1
                    report.append({"row": i, "result": "created"})
                except Exception as e:
                    errors += 1
                    report.append({"row": i, "result": "error", "error": str(e)})
            if dry:
                ctx.conn.execute("ROLLBACK")
                ctx.conn.execute("BEGIN IMMEDIATE")
            audit(ctx, "import_leaves", detail=f"{'تجريبي ' if dry else ''}منشأ {created} · متخطى {skipped} · أخطاء {errors}")
        return 200, {"dry_run": dry, "created": created, "skipped": skipped, "errors": errors, "rows": report[:300]}

    # ───────────── موصّل ذكاء الأعمال (Power BI / Tableau): تغذية بيانات مسطّحة آمنة ─────────────
    @route("GET", r"/api/bi/facts")
    def bi_facts(ctx):
        """حقائق المراسلات بصيغة مسطّحة للقراءة فقط (JSON أو CSV). بلا موضوع/ملخص افتراضيًا؛ include_text=1 يضيف موضوع غير السري."""
        if not S.can(ctx.user, "export"):
            raise ApiError(403, "تتطلب صلاحية التصدير")
        vs, vp = S.visible_clause(ctx.user)
        q = ctx.query
        where, p = [vs], list(vp)
        if S.DATE_RE.match(q.get("since", "")):
            where.append("c.updated_at>=?"); p.append(q["since"])
        size = min(5000, int(q["size"])) if q.get("size", "").isdigit() and int(q["size"]) > 0 else 1000
        page = max(1, int(q["page"])) if q.get("page", "").isdigit() else 1
        w = " AND ".join(where)
        total = ctx.conn.execute(f"SELECT COUNT(*) FROM correspondences c WHERE {w}", p).fetchone()[0]
        names = {k: v["label"] for k, v in S.classes(ctx.conn, False).items()}
        rows = ctx.conn.execute(
            "SELECT c.id, c.ref_no, c.scope, c.direction, c.status, c.priority, c.conf_level, c.doc_date, c.created_at, c.updated_at, c.due_date, c.sla_hours, "
            "c.sla_due_at, c.sla_done_at, c.source, c.needs_reply, d.name AS unit, ct.name AS type, fo.name AS folder, c.subject, "
            "(SELECT COUNT(*) FROM attachments a WHERE a.corr_id=c.id AND a.removed_at IS NULL) AS attachments, "
            "(SELECT COUNT(*) FROM correspondences r WHERE r.parent_id=c.id) AS replies "
            "FROM correspondences c LEFT JOIN departments d ON d.id=c.department_id LEFT JOIN ctypes ct ON ct.id=c.type_id LEFT JOIN folders fo ON fo.id=c.folder_id "
            f"WHERE {w} ORDER BY c.id LIMIT ? OFFSET ?", p + [size, (page - 1) * size]).fetchall()
        inc = q.get("include_text") == "1"
        items = []
        for r in rows:
            d = dict(r)
            d["priority"] = names.get(d["priority"], d["priority"])
            if not (inc and d["conf_level"] == 0):
                d.pop("subject")
            items.append(d)
        if q.get("format") == "csv":
            out = io.StringIO()
            cols = list(items[0].keys()) if items else ["id"]
            wr = csv.DictWriter(out, fieldnames=cols)
            wr.writeheader()
            for d in items:
                wr.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v) for k, v in d.items()})
            return S.Raw(200, ("﻿" + out.getvalue()).encode("utf-8"), {"Content-Type": "text/csv; charset=utf-8"})
        return 200, {"total": total, "page": page, "size": size, "next_page": page + 1 if page * size < total else None, "items": items}

    # ───────────── حالة النظام ─────────────
    @route("GET", r"/api/admin/system", roles=("admin",))
    def system_status(ctx):
        up_n = up_sz = 0
        for n in os.listdir(S.UPLOADS):
            fp = os.path.join(S.UPLOADS, n)
            if os.path.isfile(fp):
                up_n += 1
                up_sz += os.path.getsize(fp)
        files = sorted(os.listdir(BACKUPS)) if os.path.isdir(BACKUPS) else []
        last = datetime.fromtimestamp(os.path.getmtime(os.path.join(BACKUPS, files[-1]))).strftime("%Y-%m-%d %H:%M") if files else None
        chain = verify_chain(ctx.conn)
        return 200, {
            "version": VERSION, "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
            "db_size": os.path.getsize(S.DB_PATH), "attachments": up_n, "attachments_size": up_sz,
            "backups": len(files), "last_backup": last, "audit_chain": chain,
            "encryption_at_rest": bool(S.FILE_CODEC["enc"]), "sso": bool(sso_header and sso_secret),
            "tls": bool(os.environ.get("ACP_TLS_CERT")), "backup_hours": os.environ.get("ACP_BACKUP_HOURS", "24"),
            "offsite_backup": {**OFFSITE, "configured": bool(os.environ.get("ACP_BACKUP_COPY_DIR"))},
            "cold_files": ctx.conn.execute("SELECT COUNT(*) FROM attachments WHERE cold=1 AND removed_at IS NULL").fetchone()[0],
            "mfa_users": ctx.conn.execute("SELECT COUNT(*) FROM users WHERE mfa_enabled=1 AND active=1").fetchone()[0],
            "strict_secret": ctx.conn.execute("SELECT v FROM settings WHERE k='abac_strict_secret'").fetchone() == ("1",),
            "users": ctx.conn.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0],
            "correspondences": ctx.conn.execute("SELECT COUNT(*) FROM correspondences").fetchone()[0]}
