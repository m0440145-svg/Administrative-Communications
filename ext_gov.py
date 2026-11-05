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

    @route("POST", r"/api/admin/retention/apply", roles=("admin",))
    def ret_apply(ctx):
        b = ctx.body
        action = b.get("action")
        ids = b.get("ids")
        if action not in ("archive", "dispose") or not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(i, int) for i in ids):
            raise ApiError(400, "طلب غير صالح")
        if not S.verify_password(str(b.get("password", "")), ctx.user["password_hash"]):
            raise ApiError(400, "كلمة المرور غير صحيحة — يلزم تأكيد هذا الإجراء بها")
        reason = f_text(b, "reason", 300, action == "dispose", "سبب الإتلاف")
        allowed = {c["id"] for c in candidates(ctx.conn) if c["action"] == action}
        bad = [i for i in ids if i not in allowed]
        if bad:
            raise ApiError(400, f"مراسلات غير مؤهلة حسب السياسات أو عليها تجميد قانوني: {bad[:5]}")
        done = 0
        with S.tx(ctx.conn):
            for cid in ids:
                c = ctx.conn.execute("SELECT * FROM correspondences WHERE id=?", (cid,)).fetchone()
                if action == "archive":
                    ctx.conn.execute("UPDATE correspondences SET status='archived', updated_at=? WHERE id=?", (now(), cid))
                    audit(ctx, "retention_archive", "correspondence", cid, reason or "سياسة احتجاز")
                else:
                    atts = ctx.conn.execute("SELECT id, filename, stored_name, sha256 FROM attachments WHERE corr_id=? AND removed_at IS NULL", (cid,)).fetchall()
                    for a in atts:
                        try:
                            os.remove(os.path.join(S.UPLOADS, a["stored_name"]))
                        except FileNotFoundError:
                            pass
                        ctx.conn.execute("UPDATE attachments SET removed_at=? WHERE id=?", (now(), a["id"]))
                    ctx.conn.execute("UPDATE correspondences SET disposed_at=?, updated_at=? WHERE id=?", (now(), now(), cid))
                    ctx.conn.execute("INSERT INTO disposal_log(corr_id,ref_no,subject,disposed_by,disposed_at,reason,attachments) VALUES(?,?,?,?,?,?,?)",
                                     (cid, c["ref_no"], c["subject"], ctx.user["id"], now(), reason,
                                      json.dumps([{"filename": a["filename"], "sha256": a["sha256"]} for a in atts], ensure_ascii=False)))
                    audit(ctx, "retention_dispose", "correspondence", cid, reason)
                done += 1
        return 200, {"done": done}

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
                key = os.path.join(data_dir, "sign.key")
                if os.path.isfile(key):  # لازم للتحقق من التوقيعات بعد الاسترجاع — احفظ النسخة في مكان آمن
                    z.write(key, "sign.key")
                    manifest["files"]["sign.key"] = sha_file(key)
                z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            os.replace(path + ".part", path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
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
    @route("GET", r"/api/kpi")
    def kpi(ctx):
        u = ctx.user
        if not (u["role"] in ("admin", "manager", "clerk") or S.can(u, "export")):
            raise ApiError(403, "المؤشرات متاحة للمدراء ومكتب الصادر والوارد")
        vs, vp = S.visible_clause(u)
        conn, q = ctx.conn, ctx.query
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
        return 200, {
            "total": total, "open": n_open, "overdue": overdue, "overdue_pct": pct(overdue, n_open),
            "sla_compliance_pct": pct(on_time, with_due), "avg_close_days": round(avg_close, 1) if avg_close is not None else None,
            "avg_first_response_hours": round(avg_first, 1) if avg_first is not None else None,
            "reply_rate_pct": pct(replied, needs), "avg_reply_days": round(avg_reply, 1) if avg_reply is not None else None,
            "backlog_age": buckets, "monthly": monthly, "units": units, "workload": load}

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
            "users": ctx.conn.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0],
            "correspondences": ctx.conn.execute("SELECT COUNT(*) FROM correspondences").fetchone()[0]}
