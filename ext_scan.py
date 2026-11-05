"""وحدة ملحقة: صندوق المسح والاستلام + مجلد المسح الساخن + أنواع الملفات المدعومة.

• الماسحات الضوئية الشبكية/المكتبية تحفظ الملف في مجلد (ACP_SCAN_DIR) فتلتقطه المنصة كل بضع ثوانٍ:
  - اسم الملف يبدأ برقم قيد موجود (مثال: IN-00012__عقد.pdf) ← يُرفق مباشرةً بتلك المراسلة.
  - غير ذلك ← يدخل «صندوق المسح» لينسبه الموظف لمراسلة جديدة أو قائمة.
• الرفع من المتصفح (سحب وإفلات/كاميرا/دمج صور في PDF) يذهب إلى الصندوق نفسه.
• أنواع الملفات: قائمة موسّعة آمنة + إضافة المدير لأنواع أخرى، مع قائمة حظر ثابتة للتنفيذيات والسكربتات.
"""
import hashlib
import os
import re
import secrets
import shutil
import threading
import time
import traceback
from types import SimpleNamespace

EXTRA_DEFAULT = {".odt", ".ods", ".odp", ".rtf", ".gif", ".bmp", ".webp", ".heic", ".heif", ".xml", ".json", ".md", ".7z", ".rar", ".gz", ".tar",
                 ".mp3", ".wav", ".m4a", ".ogg", ".mov", ".avi", ".mkv", ".webm", ".vsd", ".vsdx", ".dwg", ".dxf", ".xps", ".epub", ".p7s", ".p7m", ".pub", ".one"}
BLOCKED = {".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".msp", ".dll", ".sys", ".js", ".jse", ".vbs", ".vbe", ".wsf", ".wsh", ".ps1", ".psm1", ".sh", ".jar", ".apk",
           ".html", ".htm", ".xhtml", ".svg", ".hta", ".lnk", ".reg", ".php", ".asp", ".aspx", ".jsp", ".py", ".pl", ".cpl", ".inf", ".gadget", ".docm", ".xlsm", ".pptm"}
EXT_RE = re.compile(r"^\.[a-z0-9]{1,8}$")
DEFAULTS = {"ext_extra": "", "max_upload_mb": "20"}


def install(S):
    route, ApiError, now, audit = S.route, S.ApiError, S.now, S.audit
    S.SETTING_DEFAULTS.update(DEFAULTS)
    S.SETTING_TEXT_KEYS.add("ext_extra")
    S.EXT_SCHEMAS.append("""CREATE TABLE IF NOT EXISTS scan_inbox(
        id INTEGER PRIMARY KEY, filename TEXT NOT NULL, stored_name TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
        enc INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL, uploaded_by INTEGER, created_at TEXT NOT NULL);""")
    BASE_EXT = set(S.ALLOWED_EXT)
    SCAN_DIR = os.environ.get("ACP_SCAN_DIR") or ""

    def parse_extra(text):
        out = []
        for t in re.split(r"[,\s،;]+", (text or "").lower()):
            if not t:
                continue
            t = t if t.startswith(".") else "." + t
            if not EXT_RE.match(t):
                raise ApiError(400, f"امتداد غير صالح: {t}")
            if t in BLOCKED:
                raise ApiError(400, f"النوع {t} محظور لأسباب أمنية (ملفات تنفيذية/سكربتات/صفحات ويب)")
            out.append(t)
        return out

    def apply_rules(conn):
        st = S.get_settings(conn)
        try:
            extra = set(parse_extra(st.get("ext_extra")))
        except ApiError:
            extra = set()
        S.ALLOWED_EXT.clear()
        S.ALLOWED_EXT.update((BASE_EXT | EXTRA_DEFAULT | extra) - BLOCKED)
        try:
            mb = max(1, min(200, int(float(st.get("max_upload_mb") or 20))))
        except ValueError:
            mb = 20
        S.MAX_UPLOAD = mb * 1024 * 1024
    S.EXT_INIT.append(apply_rules)

    # تحقّق وتطبيق فوري عند حفظ الإعدادات
    for i, (m, rx, fn, a, r) in enumerate(S.ROUTES):
        if m == "PUT" and rx.pattern == r"/api/admin/settings":
            def settings_put(ctx, _fn=fn):
                b = ctx.body
                if "ext_extra" in b:
                    parse_extra(str(b["ext_extra"]))
                if "max_upload_mb" in b and not (1 <= float(re.sub(r"[^0-9.]", "", str(b["max_upload_mb"])) or 0) <= 200):
                    raise ApiError(400, "الحجم الأقصى بين 1 و200 ميغابايت")
                res = _fn(ctx)
                apply_rules(ctx.conn)
                return res
            S.ROUTES[i] = (m, rx, settings_put, a, r)
            break

    def staff(ctx):
        if ctx.user["role"] not in ("admin", "manager", "clerk"):
            raise ApiError(403, "صندوق المسح لموظفي السجل والإدارة")

    @route("GET", r"/api/scan/rules")
    def rules(ctx):
        return 200, {"exts": sorted(S.ALLOWED_EXT), "blocked": sorted(BLOCKED), "max_mb": S.MAX_UPLOAD // (1024 * 1024), "scan_dir": bool(SCAN_DIR)}

    def store_inbox(conn, name, data, source, uid):
        stored = secrets.token_hex(16)
        enc = 1 if S.FILE_CODEC["enc"] else 0
        with open(os.path.join(S.UPLOADS, stored), "wb") as f:
            f.write(S.FILE_CODEC["enc"](data) if enc else data)
        cur = conn.execute("INSERT INTO scan_inbox(filename,stored_name,size,sha256,enc,source,uploaded_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                           (name, stored, len(data), hashlib.sha256(data).hexdigest(), enc, source, uid, now()))
        return cur.lastrowid

    def read_inbox(row):
        with open(os.path.join(S.UPLOADS, row["stored_name"]), "rb") as f:
            data = f.read()
        if row["enc"]:
            data = S.FILE_CODEC["dec"](data)
        return data

    @route("GET", r"/api/scan/inbox")
    def inbox_list(ctx):
        staff(ctx)
        rows = ctx.conn.execute("SELECT i.id,i.filename,i.size,i.source,i.created_at,u.full_name AS uploader FROM scan_inbox i LEFT JOIN users u ON u.id=i.uploaded_by ORDER BY i.id DESC LIMIT 200").fetchall()
        return 200, [dict(r) for r in rows]

    @route("POST", r"/api/scan/inbox")
    def inbox_upload(ctx):
        staff(ctx)
        name = S.clean_filename(ctx.h.headers.get("X-Filename"))
        n = int(ctx.h.headers.get("Content-Length") or 0)
        if n <= 0:
            raise ApiError(400, "الملف فارغ")
        if n > S.MAX_UPLOAD:
            raise ApiError(413, f"حجم الملف يتجاوز {S.MAX_UPLOAD // (1024 * 1024)} ميغابايت")
        data = ctx.h.rfile.read(n)
        with S.tx(ctx.conn):
            iid = store_inbox(ctx.conn, name, data, "upload", ctx.user["id"])
            audit(ctx, "scan_upload", "scan_inbox", iid, name)
        return 201, {"id": iid}

    def get_item(ctx):
        r = ctx.conn.execute("SELECT * FROM scan_inbox WHERE id=?", (int(ctx.params["id"]),)).fetchone()
        if not r:
            raise ApiError(404, "العنصر غير موجود في صندوق المسح")
        return r

    @route("GET", r"/api/scan/inbox/(?P<id>\d+)/file")
    def inbox_file(ctx):
        staff(ctx)
        r = get_item(ctx)
        try:
            data = read_inbox(r)
        except Exception:
            raise ApiError(500, "تعذّر قراءة الملف")
        return S.Raw(200, data, {"Content-Type": "application/octet-stream", "Content-Disposition": "attachment"})

    def drop_item(conn, r):
        conn.execute("DELETE FROM scan_inbox WHERE id=?", (r["id"],))
        try:
            os.remove(os.path.join(S.UPLOADS, r["stored_name"]))
        except OSError:
            pass

    @route("POST", r"/api/scan/inbox/(?P<id>\d+)/attach")
    def inbox_attach(ctx):
        staff(ctx)
        r = get_item(ctx)
        b = ctx.body
        if b.get("corr_id"):
            cid = int(b["corr_id"])
        else:
            ref = str(b.get("ref_no") or "").strip()
            vs, vp = S.visible_clause(ctx.user)
            row = ctx.conn.execute(f"SELECT c.id FROM correspondences c WHERE UPPER(c.ref_no)=UPPER(?) AND {vs}", [ref] + vp).fetchone()
            if not row:
                raise ApiError(404, "لا توجد مراسلة بهذا الرقم ضمن ما يحق لك الاطلاع عليه")
            cid = row["id"]
        c = S.get_visible(ctx, cid)
        if c["status"] == "archived":
            raise ApiError(400, "لا يمكن إضافة مرفقات لمراسلة مؤرشفة")
        data = read_inbox(r)
        with S.tx(ctx.conn):
            S.store_attachment(ctx.conn, ctx.user["id"], cid, r["filename"], data)
            drop_item(ctx.conn, r)
            audit(ctx, "scan_attach", "correspondence", cid, r["filename"])
        return 200, {"corr_id": cid, "ref_no": c["ref_no"]}

    @route("DELETE", r"/api/scan/inbox/(?P<id>\d+)")
    def inbox_delete(ctx):
        staff(ctx)
        r = get_item(ctx)
        with S.tx(ctx.conn):
            drop_item(ctx.conn, r)
            audit(ctx, "scan_delete", "scan_inbox", r["id"], r["filename"])
        return 200, {"ok": True}

    # ───────────── مجلد المسح الساخن ─────────────
    def process_file(conn, path, name):
        ext = os.path.splitext(name)[1].lower()
        size = os.path.getsize(path)
        if ext not in S.ALLOWED_EXT or not (0 < size <= S.MAX_UPLOAD):
            return "_failed"
        with open(path, "rb") as f:
            data = f.read()
        ctx = SimpleNamespace(user={"id": None, "username": "scanner", "via": "folder"}, conn=conn, ip="scan-folder")
        target = None
        for tok in re.split(r"[\s_]+", os.path.splitext(name)[0])[:3]:
            if len(tok) >= 5:
                target = conn.execute("SELECT id,ref_no FROM correspondences WHERE UPPER(ref_no)=UPPER(?) AND status<>'archived'", (tok,)).fetchone()
                if target:
                    break
        actor = conn.execute("SELECT id FROM users WHERE role='admin' AND active=1 ORDER BY id LIMIT 1").fetchone()
        with S.tx(conn):
            if target and actor:
                S.store_attachment(conn, actor["id"], target["id"], name, data)
                audit(ctx, "scan_attach", "correspondence", target["id"], f"{name} (مجلد المسح)")
            else:
                iid = store_inbox(conn, name, data, "folder", None)
                audit(ctx, "scan_folder_in", "scan_inbox", iid, name)
        return "_processed"

    def scan_worker():
        done_dir, fail_dir = (os.path.join(SCAN_DIR, d) for d in ("_processed", "_failed"))
        os.makedirs(done_dir, exist_ok=True)
        os.makedirs(fail_dir, exist_ok=True)
        while True:
            try:
                conn = S.connect()
                try:
                    for name in sorted(os.listdir(SCAN_DIR)):
                        path = os.path.join(SCAN_DIR, name)
                        if name.startswith((".", "~", "_")) or not os.path.isfile(path) or time.time() - os.path.getmtime(path) < 3:
                            continue  # ملف قيد الكتابة من الماسح
                        try:
                            dest = process_file(conn, path, name)
                        except Exception:
                            traceback.print_exc()
                            dest = "_failed"
                        shutil.move(path, os.path.join(SCAN_DIR, dest, time.strftime("%Y%m%d-%H%M%S-") + name))
                    for d in (done_dir, fail_dir):  # حذف النسخ المعالجة بعد 14 يومًا
                        for n in os.listdir(d):
                            p = os.path.join(d, n)
                            if time.time() - os.path.getmtime(p) > 14 * 86400:
                                os.remove(p)
                finally:
                    conn.close()
            except Exception:
                traceback.print_exc()
            time.sleep(max(1, int(os.environ.get("ACP_SCAN_SECONDS") or 10)))

    if SCAN_DIR:
        os.makedirs(SCAN_DIR, exist_ok=True)
        S.EXT_START.append(lambda: threading.Thread(target=scan_worker, daemon=True).start())
