"""وحدة ملحقة (مستلهمة من Mayan EDMS): إصدارات المرفقات + حقول البيانات المخصصة لكل نوع معاملة.

• إصدارات المرفق: رفع نسخة جديدة من مرفق قائم تحفظ السابقة (بملفها وبصمتها) وتبقى قابلة للتنزيل والمقارنة بالبصمة.
  تُمنع على المعاملات الموقَّعة (حتى لا تُبطَل التواقيع بصمتًا) وعلى المؤرشفة، وتُتلَف الإصدارات القديمة مع الإتلاف النظامي.
• الحقول المخصصة: يعرّفها المدير (نص/رقم/تاريخ/اختيار، إلزامي أو لا، لكل الأنواع أو لنوع معيّن)، ويملؤها من يملك تعديل المعاملة.
جداول جديدة ومسارات جديدة فقط؛ لا تغيير في جداول المرفقات أو المعاملات.
"""
import hashlib
import json
import os
import re
import secrets
import urllib.parse
from datetime import date

FTYPES = {"text": "نص", "number": "رقم", "date": "تاريخ", "choice": "اختيار من قائمة"}
NUM_RE = re.compile(r"^-?\d{1,15}(\.\d{1,6})?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def install(S):
    route, ApiError, now, audit = S.route, S.ApiError, S.now, S.audit
    S.EXT_SCHEMAS.append("""
        CREATE TABLE IF NOT EXISTS att_versions(
          id INTEGER PRIMARY KEY, att_id INTEGER NOT NULL REFERENCES attachments(id), ver_no INTEGER NOT NULL, filename TEXT NOT NULL,
          stored_name TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, enc INTEGER NOT NULL DEFAULT 0, cold INTEGER NOT NULL DEFAULT 0,
          uploaded_by INTEGER, created_at TEXT NOT NULL, note TEXT, UNIQUE(att_id, ver_no));
        CREATE TABLE IF NOT EXISTS custom_fields(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, ftype TEXT NOT NULL, choices TEXT, required INTEGER NOT NULL DEFAULT 0,
          type_id INTEGER, sort INTEGER NOT NULL DEFAULT 100, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS corr_field_values(
          corr_id INTEGER NOT NULL REFERENCES correspondences(id), field_id INTEGER NOT NULL REFERENCES custom_fields(id), value TEXT NOT NULL,
          PRIMARY KEY(corr_id, field_id));""")

    # ───────────── إصدارات المرفقات ─────────────
    def get_att(ctx, aid=None):
        a = ctx.conn.execute("SELECT * FROM attachments WHERE id=?", (aid or int(ctx.params["id"]),)).fetchone()
        if not a:
            raise ApiError(404, "المرفق غير موجود")
        c = S.get_visible(ctx, a["corr_id"])
        return a, c

    def can_version(ctx, a, c):
        return ctx.user["role"] == "admin" or S.can_manage(ctx, c) or a["uploaded_by"] == ctx.user["id"]

    def read_file(rec):
        path = os.path.join(S.COLD if rec["cold"] else S.UPLOADS, rec["stored_name"])
        if not os.path.isfile(path):
            raise ApiError(404, "الملف مفقود من التخزين")
        with open(path, "rb") as f:
            data = f.read()
        if rec["enc"]:
            if not S.FILE_CODEC["dec"]:
                raise ApiError(500, "المرفق مشفّر ومفتاح التشفير غير مضبوط")
            data = S.FILE_CODEC["dec"](data)
        if hashlib.sha256(data).hexdigest() != rec["sha256"]:
            raise ApiError(500, "فشل التحقق من سلامة الملف (البصمة لا تطابق)")
        return data

    @route("GET", r"/api/correspondences/(?P<id>\d+)/att-meta")
    def att_meta(ctx):
        """لكل مرفق (بترتيب ظهوره في المعاملة): عدد إصداراته القديمة وهل يستطيع المستخدم رفع إصدار جديد."""
        cid = int(ctx.params["id"])
        c = S.get_visible(ctx, cid)
        signed = bool(ctx.conn.execute("SELECT 1 FROM signatures WHERE corr_id=? LIMIT 1", (cid,)).fetchone())
        rows = ctx.conn.execute("SELECT a.id, a.filename, a.uploaded_by, (SELECT COUNT(*) FROM att_versions v WHERE v.att_id=a.id) AS old_n FROM attachments a "
                                "WHERE a.corr_id=? AND a.removed_at IS NULL ORDER BY a.id", (cid,)).fetchall()
        return 200, {"signed": signed, "archived": c["status"] == "archived",
                     "items": [{"id": r["id"], "filename": r["filename"], "old_versions": r["old_n"], "can_version": can_version(ctx, r, c) and not signed and c["status"] != "archived"} for r in rows]}

    @route("GET", r"/api/attachments/(?P<id>\d+)/versions")
    def versions(ctx):
        a, c = get_att(ctx)
        cur = {"id": None, "ver_no": None, "current": True, "filename": a["filename"], "size": a["size"], "sha256": a["sha256"], "created_at": a["created_at"],
               "uploader": ctx.conn.execute("SELECT full_name FROM users WHERE id=?", (a["uploaded_by"],)).fetchone()[0]}
        old = [dict(r, current=False) for r in ctx.conn.execute(
            "SELECT v.id, v.ver_no, v.filename, v.size, v.sha256, v.created_at, v.note, u.full_name AS uploader FROM att_versions v LEFT JOIN users u ON u.id=v.uploaded_by "
            "WHERE v.att_id=? ORDER BY v.ver_no DESC", (a["id"],))]
        cur["ver_no"] = (old[0]["ver_no"] + 1) if old else 1
        return 200, [cur] + old

    @route("POST", r"/api/attachments/(?P<id>\d+)/versions")
    def new_version(ctx):
        a, c = get_att(ctx)
        if a["removed_at"]:
            raise ApiError(410, "أُتلف هذا المرفق وفق سياسة الاحتجاز")
        if c["status"] == "archived":
            raise ApiError(400, "المعاملة مؤرشفة")
        if not can_version(ctx, a, c):
            raise ApiError(403, "لا تملك صلاحية رفع إصدار جديد لهذا المرفق")
        if ctx.conn.execute("SELECT 1 FROM signatures WHERE corr_id=? LIMIT 1", (c["id"],)).fetchone():
            raise ApiError(400, "المعاملة موقَّعة: استبدال المرفق يُبطل التواقيع. أضف مرفقًا جديدًا بدل ذلك")
        raw_name = ctx.h.headers.get("X-Filename")
        name = S.clean_filename(raw_name) if raw_name else a["filename"]
        note = (ctx.h.headers.get("X-Note") or "").strip()[:300]
        note = urllib.parse.unquote(note) if note else None
        n = int(ctx.h.headers.get("Content-Length") or 0)
        if n <= 0:
            raise ApiError(400, "الملف فارغ")
        if n > S.MAX_UPLOAD:
            raise ApiError(413, f"حجم الملف يتجاوز {S.MAX_UPLOAD // (1024 * 1024)} ميغابايت")
        data = ctx.h.rfile.read(n)
        digest = hashlib.sha256(data).hexdigest()
        if digest == a["sha256"]:
            raise ApiError(400, "الملف مطابق تمامًا للإصدار الحالي (البصمة نفسها)")
        stored, enc = secrets.token_hex(16), (1 if S.FILE_CODEC["enc"] else 0)
        with open(os.path.join(S.UPLOADS, stored), "wb") as f:
            f.write(S.FILE_CODEC["enc"](data) if enc else data)
        try:
            with S.tx(ctx.conn):
                ver = ctx.conn.execute("SELECT COALESCE(MAX(ver_no),0)+1 FROM att_versions WHERE att_id=?", (a["id"],)).fetchone()[0]
                ctx.conn.execute("INSERT INTO att_versions(att_id,ver_no,filename,stored_name,size,sha256,enc,cold,uploaded_by,created_at,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                 (a["id"], ver, a["filename"], a["stored_name"], a["size"], a["sha256"], a["enc"], a["cold"], a["uploaded_by"], a["created_at"], None))
                ctx.conn.execute("UPDATE attachments SET filename=?, stored_name=?, size=?, sha256=?, enc=?, cold=0, uploaded_by=?, created_at=? WHERE id=?",
                                 (name, stored, n, digest, enc, ctx.user["id"], now(), a["id"]))
                if note:
                    ctx.conn.execute("UPDATE att_versions SET note=? WHERE att_id=? AND ver_no=?", (note, a["id"], ver))
                audit(ctx, "attach_version", "correspondence", c["id"], f"{name}: الإصدار {ver + 1} (sha256:{digest[:12]}…)")
        except BaseException:
            os.remove(os.path.join(S.UPLOADS, stored))
            raise
        return 201, {"id": a["id"], "ver_no": ver + 1, "sha256": digest}

    @route("GET", r"/api/attachment-versions/(?P<id>\d+)")
    def download_version(ctx):
        v = ctx.conn.execute("SELECT * FROM att_versions WHERE id=?", (int(ctx.params["id"]),)).fetchone()
        if not v:
            raise ApiError(404, "الإصدار غير موجود")
        a, c = get_att(ctx, v["att_id"])
        if a["removed_at"]:
            raise ApiError(410, "أُتلف هذا المرفق وفق سياسة الاحتجاز")
        data = read_file(v)
        audit(ctx, "download_version", "correspondence", c["id"], f"{v['filename']} (الإصدار {v['ver_no']})")
        return S.Raw(200, data, {"Content-Type": "application/octet-stream", "Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(v['filename'])}"})

    def purge_att_versions(conn, att_id):
        for v in conn.execute("SELECT stored_name, cold FROM att_versions WHERE att_id=?", (att_id,)).fetchall():
            try:
                os.remove(os.path.join(S.COLD if v["cold"] else S.UPLOADS, v["stored_name"]))
            except FileNotFoundError:
                pass
        conn.execute("DELETE FROM att_versions WHERE att_id=?", (att_id,))
    S.purge_att_versions = purge_att_versions

    # ───────────── الحقول المخصصة ─────────────
    def shape_field(r):
        d = dict(r)
        d["choices"] = json.loads(d["choices"]) if d["choices"] else []
        d["ftype_label"] = FTYPES.get(d["ftype"], d["ftype"])
        return d

    def field_input(b, creating, conn):
        out = {}
        if creating or "name" in b:
            out["name"] = S.f_text(b, "name", 80, True, "اسم الحقل")
        if creating or "ftype" in b:
            if b.get("ftype") not in FTYPES:
                raise ApiError(400, "نوع الحقل: " + "، ".join(FTYPES.values()))
            out["ftype"] = b["ftype"]
        if "required" in b:
            out["required"] = 1 if b["required"] else 0
        if "type_id" in b:
            tid = b["type_id"]
            if tid in (None, ""):
                out["type_id"] = None
            elif not isinstance(tid, int) or not conn.execute("SELECT 1 FROM ctypes WHERE id=?", (tid,)).fetchone():
                raise ApiError(400, "نوع المعاملة غير موجود")
            else:
                out["type_id"] = tid
        if "active" in b:
            out["active"] = 1 if b["active"] else 0
        if "sort" in b and isinstance(b["sort"], int):
            out["sort"] = b["sort"]
        if "choices" in b or (creating and b.get("ftype") == "choice"):
            ch = b.get("choices") or []
            if isinstance(ch, str):
                ch = [x.strip() for x in re.split(r"[\n،,]", ch)]
            ch = [x for x in ch if isinstance(x, str) and x.strip()]
            if len(ch) > 50 or any(len(x) > 80 for x in ch):
                raise ApiError(400, "قائمة الاختيارات: حتى 50 عنصرًا، لكل عنصر 80 حرفًا")
            if (out.get("ftype") or b.get("ftype")) == "choice" and not ch:
                raise ApiError(400, "حقل الاختيار يحتاج قائمة اختيارات")
            out["choices"] = json.dumps(list(dict.fromkeys(x.strip() for x in ch)), ensure_ascii=False) if ch else None
        return out

    @route("GET", r"/api/custom-fields")
    def list_fields(ctx):
        allf = ctx.user["role"] == "admin" and ctx.query.get("all") == "1"
        rows = ctx.conn.execute("SELECT * FROM custom_fields" + ("" if allf else " WHERE active=1") + " ORDER BY sort, id").fetchall()
        return 200, [shape_field(r) for r in rows]

    @route("POST", r"/api/custom-fields", roles=("admin",))
    def create_field(ctx):
        f = field_input(ctx.body, True, ctx.conn)
        try:
            with S.tx(ctx.conn):
                cur = ctx.conn.execute("INSERT INTO custom_fields(name,ftype,choices,required,type_id,sort) VALUES(?,?,?,?,?,?)",
                                       (f["name"], f["ftype"], f.get("choices"), f.get("required", 0), f.get("type_id"), f.get("sort", 100)))
                audit(ctx, "field_create", "custom_field", cur.lastrowid, f["name"])
        except Exception as e:
            if "UNIQUE" in str(e):
                raise ApiError(409, "يوجد حقل بهذا الاسم")
            raise
        return 201, {"id": cur.lastrowid}

    @route("PUT", r"/api/custom-fields/(?P<id>\d+)", roles=("admin",))
    def update_field(ctx):
        fid = int(ctx.params["id"])
        if not ctx.conn.execute("SELECT 1 FROM custom_fields WHERE id=?", (fid,)).fetchone():
            raise ApiError(404, "الحقل غير موجود")
        f = field_input(ctx.body, False, ctx.conn)
        if "ftype" in f:
            raise ApiError(400, "لا يُغيَّر نوع الحقل بعد إنشائه (عطّله وأنشئ غيره)")
        if not f:
            raise ApiError(400, "لا شيء للتحديث")
        with S.tx(ctx.conn):
            ctx.conn.execute("UPDATE custom_fields SET " + ",".join(f"{k}=?" for k in f) + " WHERE id=?", list(f.values()) + [fid])
            audit(ctx, "field_update", "custom_field", fid, "، ".join(f))
        return 200, {"ok": True}

    def applicable(conn, c):
        return conn.execute("SELECT * FROM custom_fields WHERE active=1 AND (type_id IS NULL OR type_id=?) ORDER BY sort, id", (c["type_id"],)).fetchall()

    @route("GET", r"/api/correspondences/(?P<id>\d+)/fields")
    def get_values(ctx):
        c = S.get_visible(ctx, int(ctx.params["id"]))
        vals = {r["field_id"]: r["value"] for r in ctx.conn.execute("SELECT field_id, value FROM corr_field_values WHERE corr_id=?", (c["id"],))}
        can_edit = S.can_manage(ctx, c) or c["created_by"] == ctx.user["id"]
        return 200, {"can_edit": bool(can_edit) and c["status"] != "archived", "fields": [dict(shape_field(f), value=vals.get(f["id"], "")) for f in applicable(ctx.conn, c)]}

    @route("PUT", r"/api/correspondences/(?P<id>\d+)/fields")
    def put_values(ctx):
        c = S.get_visible(ctx, int(ctx.params["id"]))
        if not (S.can_manage(ctx, c) or c["created_by"] == ctx.user["id"]) or c["status"] == "archived":
            raise ApiError(403, "لا تملك صلاحية تعديل بيانات هذه المعاملة")
        given = ctx.body.get("values")
        if not isinstance(given, dict):
            raise ApiError(400, "values مطلوبة")
        clean = {}
        for f in applicable(ctx.conn, c):
            if str(f["id"]) not in given:
                continue
            v = given[str(f["id"])]
            v = "" if v is None else str(v).strip()
            if not v:
                if f["required"]:
                    raise ApiError(400, f"الحقل «{f['name']}» إلزامي")
                clean[f["id"]] = None
                continue
            if f["ftype"] == "text" and len(v) > 500:
                raise ApiError(400, f"«{f['name']}»: حتى 500 حرف")
            if f["ftype"] == "number" and not NUM_RE.match(v):
                raise ApiError(400, f"«{f['name']}»: رقم غير صالح")
            if f["ftype"] == "date":
                try:
                    date.fromisoformat(v)
                except ValueError:
                    raise ApiError(400, f"«{f['name']}»: تاريخ غير صالح (YYYY-MM-DD)")
            if f["ftype"] == "choice" and v not in json.loads(f["choices"] or "[]"):
                raise ApiError(400, f"«{f['name']}»: قيمة خارج القائمة")
            clean[f["id"]] = v
        with S.tx(ctx.conn):
            for fid, v in clean.items():
                if v is None:
                    ctx.conn.execute("DELETE FROM corr_field_values WHERE corr_id=? AND field_id=?", (c["id"], fid))
                else:
                    ctx.conn.execute("INSERT INTO corr_field_values(corr_id,field_id,value) VALUES(?,?,?) ON CONFLICT(corr_id,field_id) DO UPDATE SET value=excluded.value", (c["id"], fid, v))
            ctx.conn.execute("UPDATE correspondences SET updated_at=? WHERE id=?", (now(), c["id"]))
            audit(ctx, "fields_save", "correspondence", c["id"], f"{len(clean)} حقل")
        return 200, {"saved": len(clean)}
