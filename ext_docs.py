"""وحدة ملحقة: الوثائق — الملف الموحّد للمعاملة (Dossier) بخط زمني، تهميشات وأختام على طبقة منفصلة، ربط المراسلات وكشف التكرار،
تثبيت المعاملات، ملصقات QR وباركود Code128، الخطاب الرسمي بالترويسة، وتصدير Excel (xlsx) بلا اعتماديات خارجية.
"""
import io
import itertools
import json
import re
import zipfile
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

SCHEMA = """
CREATE TABLE IF NOT EXISTS pins(user_id INTEGER NOT NULL, corr_id INTEGER NOT NULL, at TEXT NOT NULL, PRIMARY KEY(user_id, corr_id));
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY, corr_id INTEGER NOT NULL REFERENCES correspondences(id), att_id INTEGER, user_id INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'note', text TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_notes_corr ON notes(corr_id);
CREATE TABLE IF NOT EXISTS related(a INTEGER NOT NULL, b INTEGER NOT NULL, created_by INTEGER NOT NULL, at TEXT NOT NULL, PRIMARY KEY(a, b));
"""

STAMPS = ("اطّلع", "معتمد", "للمتابعة", "للحفظ", "مؤجَّل", "يُعاد للدراسة")

# ───────────────────────── QR (وضع البايت، تصحيح M، الإصدارات 1–10) ─────────────────────────
EC_M = {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]), 6: (16, [(4, 27)]),
        7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]), 10: (26, [(4, 43), (1, 44)])}
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_EXP, _LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i], _LOG[_x] = _x, _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gmul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs(data, n):
    gen = [1]
    for i in range(n):
        nxt = [0] * (len(gen) + 1)
        for j, g in enumerate(gen):
            nxt[j] ^= g
            nxt[j + 1] ^= _gmul(g, _EXP[i])
        gen = nxt
    rem = [0] * n
    for b in data:
        f = b ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(n):
            rem[i] ^= _gmul(gen[i + 1], f)
    return rem


def qr_matrix(text, mask=None):
    data = text.encode("utf-8")
    for ver in range(1, 11):
        ecn, groups = EC_M[ver]
        cap = sum(c * d for c, d in groups)
        cbits = 8 if ver < 10 else 16
        if 4 + cbits + 8 * len(data) <= cap * 8:
            break
    else:
        raise ValueError("النص أطول من سعة QR المدعومة")
    bits = "0100" + format(len(data), f"0{cbits}b") + "".join(format(b, "08b") for b in data)
    bits += "0" * min(4, cap * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    cw = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    for pad in itertools.cycle((0xEC, 0x11)):
        if len(cw) >= cap:
            break
        cw.append(pad)
    blocks, pos = [], 0
    for cnt, dlen in groups:
        for _ in range(cnt):
            blocks.append(cw[pos:pos + dlen])
            pos += dlen
    ecs = [_rs(b, ecn) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        out += [b[i] for b in blocks if i < len(b)]
    for i in range(ecn):
        out += [e[i] for e in ecs]
    stream = "".join(format(b, "08b") for b in out) + "0" * ({1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7}.get(ver, 0))
    size = 17 + 4 * ver
    M = [[False] * size for _ in range(size)]
    F = [[False] * size for _ in range(size)]

    def setf(x, y, dark):
        M[y][x] = bool(dark)
        F[y][x] = True
    for i in range(size):  # التوقيت
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):  # مكتشفات الموضع
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    pts = ALIGN[ver]
    for i, py in enumerate(pts):
        for j, px in enumerate(pts):
            if (i == 0 and j == 0) or (i == 0 and j == len(pts) - 1) or (i == len(pts) - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(px + dx, py + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(m):
        d = (0 << 3) | m  # مستوى M = 00
        rem = d
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        b = ((d << 10) | rem) ^ 0x5412
        g = lambda i: (b >> i) & 1
        for i in range(0, 6):
            setf(8, i, g(i))
        setf(8, 7, g(6)); setf(8, 8, g(7)); setf(7, 8, g(8))
        for i in range(9, 15):
            setf(14 - i, 8, g(i))
        for i in range(0, 8):
            setf(size - 1 - i, 8, g(i))
        for i in range(8, 15):
            setf(8, size - 15 + i, g(i))
        setf(8, size - 8, True)
    draw_format(0)
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        vb = (ver << 12) | rem
        for i in range(18):
            bit, a, b2 = (vb >> i) & 1, size - 11 + i % 3, i // 3
            setf(a, b2, bit); setf(b2, a, bit)
    k, up, right = 0, True, size - 1
    while right >= 1:  # أزواج الأعمدة من اليمين؛ عمود التوقيت (6) يُتخطّى فيصير الزوج (5,4)
        if right == 6:
            right = 5
        for v in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - v if up else v
                if not F[y][x] and k < len(stream):
                    M[y][x] = stream[k] == "1"
                    k += 1
        up = not up
        right -= 2
    masks = [lambda i, j: (i + j) % 2 == 0, lambda i, j: i % 2 == 0, lambda i, j: j % 3 == 0, lambda i, j: (i + j) % 3 == 0,
             lambda i, j: (i // 2 + j // 3) % 2 == 0, lambda i, j: (i * j) % 2 + (i * j) % 3 == 0,
             lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0, lambda i, j: ((i + j) % 2 + (i * j) % 3) % 2 == 0]

    def with_mask(m):
        grid = [row[:] for row in M]
        for y in range(size):
            for x in range(size):
                if not F[y][x] and masks[m](y, x):
                    grid[y][x] = not grid[y][x]
        # كتابة معلومات الصيغة للقناع المختار
        saved = [row[:] for row in M]
        for yy in range(size):
            M[yy][:] = grid[yy]
        draw_format(m)
        res = [row[:] for row in M]
        for yy in range(size):
            M[yy][:] = saved[yy]
        return res

    def penalty(g):
        s = 0
        for lines in (g, [list(c) for c in zip(*g)]):
            for line in lines:
                run = 1
                for i in range(1, size):
                    if line[i] == line[i - 1]:
                        run += 1
                    else:
                        s += 3 + (run - 5) if run >= 5 else 0
                        run = 1
                s += 3 + (run - 5) if run >= 5 else 0
                bs = "".join("1" if c else "0" for c in line)
                s += 40 * (len(re.findall(r"(?=(?:0000)?10111010000|(?:0000)10111010)", bs)) if False else (bs.count("000010111010") + bs.count("010111010000")))
        for y in range(size - 1):
            for x in range(size - 1):
                if g[y][x] == g[y][x + 1] == g[y + 1][x] == g[y + 1][x + 1]:
                    s += 3
        dark = sum(sum(r) for r in g)
        s += 10 * (abs(dark * 20 - size * size * 10) // (size * size) if False else max(0, -(-abs(dark * 20 - size * size * 10) // (size * size)) - 1))
        return s
    if mask is None:
        mask = min(range(8), key=lambda m: (penalty(with_mask(m)), m))
    return with_mask(mask)


def qr_svg(text, scale=4, mask=None):
    g = qr_matrix(text, mask)
    n = len(g)
    parts = []
    for y, row in enumerate(g):
        x = 0
        while x < n:
            if row[x]:
                w = 1
                while x + w < n and row[x + w]:
                    w += 1
                parts.append(f"M{x + 4} {y + 4}h{w}v1h-{w}z")
                x += w
            else:
                x += 1
    d = "".join(parts)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n + 8} {n + 8}" width="{(n + 8) * scale}" height="{(n + 8) * scale}" '
            f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/><path d="{d}" fill="#000"/></svg>')


# ───────────────────────── Code 128 (المجموعة B) ─────────────────────────
C128 = ("212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
        "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
        "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
        "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
        "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
        "114131 311141 411131 211412 211214 211232 2331112").split()


def code128_modules(text):
    codes = [104] + [ord(c) - 32 for c in text]
    if any(not 0 <= c < 95 for c in codes[1:]):
        raise ValueError("الباركود يدعم الأحرف اللاتينية والأرقام فقط")
    codes.append(sum(c * (i if i else 1) for i, c in enumerate(codes)) % 103)
    codes.append(106)
    return "".join(C128[c] for c in codes)


def code128_svg(text, height=40, unit=2):
    widths, x, rects, dark = code128_modules(text), 10 * unit, [], True
    for w in widths:
        w = int(w) * unit
        if dark:
            rects.append(f'<rect x="{x}" y="0" width="{w}" height="{height}"/>')
        x += w
        dark = not dark
    total = x + 10 * unit
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {height}" width="{total}" height="{height}" shape-rendering="crispEdges">'
            f'<rect width="{total}" height="{height}" fill="#fff"/><g fill="#000">{"".join(rects)}</g></svg>')


# ───────────────────────── XLSX (OOXML مبسّط) ─────────────────────────
def _col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def make_xlsx(sheets):
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    files = {}
    names = []
    for n, (title, rows) in enumerate(sheets, 1):
        t = re.sub(r"[\[\]:*?/\\]", " ", str(title))[:31] or f"Sheet{n}"
        names.append(t)
        out = []
        for r, row in enumerate(rows, 1):
            cells = []
            for c, v in enumerate(row):
                ref = f"{_col(c)}{r}"
                if v is None:
                    continue
                if isinstance(v, bool):
                    v = int(v)
                if isinstance(v, (int, float)):
                    cells.append(f'<c r="{ref}"><v>{v}</v></c>')
                else:
                    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v))
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(s)}</t></is></c>')
            out.append(f'<row r="{r}">{"".join(cells)}</row>')
        files[f"xl/worksheets/sheet{n}.xml"] = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{ns}"><sheetViews>'
                                                f'<sheetView rightToLeft="1" workbookViewId="0"/></sheetViews><sheetData>{"".join(out)}</sheetData></worksheet>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                    for i in range(1, len(names) + 1)) + "</Types>")
    wb = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
          + "".join(f'<sheet name="{escape(n, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1)) + "</sheets></workbook>")
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    wbrels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{rel_ns}">'
              + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                        for i in range(1, len(names) + 1)) + "</Relationships>")
    root = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{rel_ns}"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", root)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wbrels)
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


def install(S):
    route, ApiError = S.route, S.ApiError
    f_text, f_int, now, audit = S.f_text, S.f_int, S.now, S.audit
    S.EXT_SCHEMAS.append(SCHEMA)
    S.qr_svg, S.code128_svg, S.make_xlsx = qr_svg, code128_svg, make_xlsx
    S.SETTING_DEFAULTS.update({"org_name": "", "org_header": "", "org_footer": "", "org_stamp": ""})
    S.SETTING_TEXT_KEYS.update({"org_name", "org_header", "org_footer", "org_stamp"})

    def dt(s):
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")

    def visible_ids(ctx, ids):
        ids = list(dict.fromkeys(ids))
        if not ids:
            return []
        vs, vp = S.visible_clause(ctx.user)
        return [r[0] for r in ctx.conn.execute(f"SELECT c.id FROM correspondences c WHERE c.id IN ({','.join('?' * len(ids))}) AND {vs}", ids + vp)]

    # ───────────── التثبيت (Pinning) ─────────────
    def list_hook(ctx, items):
        if not items:
            return
        ids = [i["id"] for i in items]
        pinned = {r[0] for r in ctx.conn.execute(f"SELECT corr_id FROM pins WHERE user_id=? AND corr_id IN ({','.join('?' * len(ids))})", [ctx.user["id"]] + ids)}
        for i in items:
            i["pinned"] = i["id"] in pinned
    S.EXT_LIST.append(list_hook)

    @route("POST", r"/api/correspondences/(?P<id>\d+)/pin")
    def pin(ctx):
        cid = int(ctx.params["id"])
        S.get_visible(ctx, cid)
        if ctx.body.get("pinned", True):
            ctx.conn.execute("INSERT OR IGNORE INTO pins VALUES(?,?,?)", (ctx.user["id"], cid, now()))
        else:
            ctx.conn.execute("DELETE FROM pins WHERE user_id=? AND corr_id=?", (ctx.user["id"], cid))
        return 200, {"pinned": bool(ctx.body.get("pinned", True))}

    # ───────────── التهميشات والأختام (طبقة منفصلة لا تمسّ الأصل) ─────────────
    @route("POST", r"/api/correspondences/(?P<id>\d+)/notes")
    def note_add(ctx):
        cid = int(ctx.params["id"])
        c = S.get_visible(ctx, cid)
        kind = ctx.body.get("kind", "note")
        if kind not in ("note", "stamp"):
            raise ApiError(400, "نوع غير صالح")
        text = f_text(ctx.body, "text", 1000, True, "النص")
        if kind == "stamp":
            if text not in STAMPS:
                raise ApiError(400, "ختم غير معروف")
            if not S.can_manage(ctx, c):
                raise ApiError(403, "الأختام لمن يملك صلاحية إدارة المراسلة")
        att = f_int(ctx.body, "att_id")
        if att and not ctx.conn.execute("SELECT 1 FROM attachments WHERE id=? AND corr_id=?", (att, cid)).fetchone():
            raise ApiError(400, "المرفق غير تابع لهذه المراسلة")
        cur = ctx.conn.execute("INSERT INTO notes(corr_id,att_id,user_id,kind,text,created_at) VALUES(?,?,?,?,?,?)", (cid, att, ctx.user["id"], kind, text, now()))
        audit(ctx, "note" if kind == "note" else "stamp", "correspondence", cid, text[:120])
        S.index_corr(ctx.conn, cid)
        return 201, {"id": cur.lastrowid}

    @route("DELETE", r"/api/notes/(?P<id>\d+)")
    def note_del(ctx):
        n = ctx.conn.execute("SELECT * FROM notes WHERE id=?", (int(ctx.params["id"]),)).fetchone()
        if not n or (n["user_id"] != ctx.user["id"] and ctx.user["role"] != "admin"):
            raise ApiError(404, "غير موجود")
        S.get_visible(ctx, n["corr_id"])
        ctx.conn.execute("DELETE FROM notes WHERE id=?", (n["id"],))
        audit(ctx, "note_delete", "correspondence", n["corr_id"], n["text"][:80])
        S.index_corr(ctx.conn, n["corr_id"])
        return 200, {"ok": True}

    # ───────────── الربط وكشف التكرار ─────────────
    def pair(a, b):
        return (a, b) if a < b else (b, a)

    @route("POST", r"/api/correspondences/(?P<id>\d+)/related")
    def related_add(ctx):
        cid = int(ctx.params["id"])
        c = S.get_visible(ctx, cid)
        ref = f_text(ctx.body, "ref_no", 40, True, "رقم القيد")
        vs, vp = S.visible_clause(ctx.user)
        o = ctx.conn.execute(f"SELECT c.* FROM correspondences c WHERE c.ref_no=? AND {vs}", [ref] + vp).fetchone()
        if not o:
            raise ApiError(404, "لا توجد مراسلة بهذا الرقم ضمن ما يحق لك الاطلاع عليه")
        if o["id"] == cid:
            raise ApiError(400, "لا يمكن ربط المراسلة بنفسها")
        a, b = pair(cid, o["id"])
        ctx.conn.execute("INSERT OR IGNORE INTO related VALUES(?,?,?,?)", (a, b, ctx.user["id"], now()))
        audit(ctx, "related_link", "correspondence", cid, f"ربط مع {o['ref_no']}")
        return 201, {"ok": True}

    @route("DELETE", r"/api/correspondences/(?P<id>\d+)/related/(?P<other>\d+)")
    def related_del(ctx):
        cid, oid = int(ctx.params["id"]), int(ctx.params["other"])
        S.get_visible(ctx, cid)
        a, b = pair(cid, oid)
        ctx.conn.execute("DELETE FROM related WHERE a=? AND b=?", (a, b))
        audit(ctx, "related_unlink", "correspondence", cid, f"فك الربط مع #{oid}")
        return 200, {"ok": True}

    def related_of(ctx, cid):
        ids = [r[0] if r[1] == cid else r[1] for r in ctx.conn.execute("SELECT a, b FROM related WHERE a=? OR b=?", (cid, cid))]
        vis = visible_ids(ctx, ids)
        if not vis:
            return []
        return [dict(r) for r in ctx.conn.execute(f"SELECT id, ref_no, subject, direction, status FROM correspondences WHERE id IN ({','.join('?' * len(vis))})", vis)]

    def detail(ctx, c):
        c["related"] = related_of(ctx, c["id"])
        c["notes"] = [dict(r) for r in ctx.conn.execute(
            "SELECT n.id, n.att_id, n.kind, n.text, n.created_at, n.user_id, u.full_name AS author FROM notes n JOIN users u ON u.id=n.user_id WHERE n.corr_id=? ORDER BY n.id", (c["id"],))]
        c["pinned"] = bool(ctx.conn.execute("SELECT 1 FROM pins WHERE user_id=? AND corr_id=?", (ctx.user["id"], c["id"])).fetchone())
    S.EXT_DETAIL.append(detail)

    def jaccard(a, b):
        a, b = set(a), set(b)
        return len(a & b) / len(a | b) if a and b else 0.0

    @route("GET", r"/api/duplicates")
    def duplicates(ctx):
        """تنبيه قبل القيد: معاملة بنفس رقم مرجع الجهة، أو موضوع شبه مطابق لدى الجهة نفسها، أو تطابق شبه تام في أي جهة."""
        q = ctx.query
        party, pref, subject = (q.get(k, "")[:300].strip() for k in ("party", "party_ref", "subject"))
        norm = S.norm_text
        vs, vp = S.visible_clause(ctx.user)
        out = {}
        if pref and party:
            for r in ctx.conn.execute(f"SELECT c.id, c.ref_no, c.subject, c.party, c.created_at FROM correspondences c WHERE c.party_ref=? AND {vs} LIMIT 5", [pref] + vp):
                if norm(r["party"]) == norm(party):
                    out[r["id"]] = {**dict(r), "score": 1.0, "reason": "نفس رقم مرجع الجهة"}
        toks = norm(subject).split()
        if len(toks) >= 2:
            since = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
            for r in ctx.conn.execute(f"SELECT c.id, c.ref_no, c.subject, c.party, c.created_at FROM correspondences c WHERE c.doc_date>=? AND {vs} ORDER BY c.id DESC LIMIT 3000", [since] + vp):
                s = jaccard(toks, norm(r["subject"]).split())
                same_party = party and norm(r["party"]) == norm(party)
                if (same_party and s >= 0.7) or s >= 0.9:
                    if r["id"] not in out or out[r["id"]]["score"] < s:
                        out[r["id"]] = {**dict(r), "score": round(s, 2), "reason": "موضوع مشابه لدى الجهة نفسها" if same_party else "موضوع مطابق تقريبًا"}
        return 200, sorted(out.values(), key=lambda x: -x["score"])[:5]

    # ───────────── الملف الموحّد (Dossier) بخط زمني ─────────────
    @route("GET", r"/api/correspondences/(?P<id>\d+)/dossier")
    def dossier(ctx):
        cid = int(ctx.params["id"])
        S.get_visible(ctx, cid)
        conn = ctx.conn
        root = cid
        for _ in range(12):
            r = conn.execute("SELECT parent_id FROM correspondences WHERE id=?", (root,)).fetchone()
            if not r or not r[0]:
                break
            root = r[0]
        family, frontier = [root], [root]
        for _ in range(6):
            if not frontier:
                break
            kids = [r[0] for r in conn.execute(f"SELECT id FROM correspondences WHERE parent_id IN ({','.join('?' * len(frontier))})", frontier)]
            kids = [k for k in kids if k not in family]
            family += kids
            frontier = kids
        rel = {r[0 if r[1] in family else 1] for r in conn.execute(f"SELECT a, b FROM related WHERE a IN ({','.join('?' * len(family))}) OR b IN ({','.join('?' * len(family))})", family + family)}
        letters_ids = visible_ids(ctx, family + [x for x in rel if x not in family])
        if not letters_ids:
            raise ApiError(404, "لا شيء للعرض")
        ph = ",".join("?" * len(letters_ids))
        letters = [dict(r, relation=("الأصل" if r["id"] == root else "رد/متابعة" if r["id"] in family else "مرتبطة"))
                   for r in conn.execute(f"SELECT id, ref_no, subject, direction, scope, status, party, created_at, parent_id FROM correspondences WHERE id IN ({ph}) ORDER BY created_at, id", letters_ids)]
        ref = {l["id"]: l["ref_no"] for l in letters}
        ev = []
        for l in letters:
            ev.append({"at": l["created_at"], "type": "letter", "corr_id": l["id"], "ref": l["ref_no"], "title": f"قيد {('وارد' if l['direction'] == 'incoming' else 'صادر')} {l['ref_no']}", "detail": l["subject"]})
        for r in conn.execute(f"SELECT a.created_at, a.corr_id, a.filename, u.full_name FROM attachments a JOIN users u ON u.id=a.uploaded_by WHERE a.corr_id IN ({ph})", letters_ids):
            ev.append({"at": r["created_at"], "type": "attachment", "corr_id": r["corr_id"], "ref": ref[r["corr_id"]], "title": "مرفق: " + r["filename"], "detail": r["full_name"]})
        for r in conn.execute(f"SELECT t.*, f.full_name AS fn, o.full_name AS tn FROM transfers t JOIN users f ON f.id=t.from_user JOIN users o ON o.id=t.to_user WHERE t.corr_id IN ({ph})", letters_ids):
            ev.append({"at": r["created_at"], "type": "transfer", "corr_id": r["corr_id"], "ref": ref[r["corr_id"]], "title": f"إحالة: {r['fn']} ← {r['tn']}", "detail": r["instruction"]})
            if r["done_at"]:
                ev.append({"at": r["done_at"], "type": "transfer_done", "corr_id": r["corr_id"], "ref": ref[r["corr_id"]], "title": f"نفّذ {r['tn']} الإحالة", "detail": r["note"]})
        for r in conn.execute(f"SELECT s.*, u.full_name FROM signatures s JOIN users u ON u.id=s.user_id WHERE s.corr_id IN ({ph})", letters_ids):
            ev.append({"at": r["signed_at"], "type": "signature", "corr_id": r["corr_id"], "ref": ref[r["corr_id"]], "title": ("اعتماد" if r["decision"] == "approve" else "رفض") + " — " + r["full_name"], "detail": r["comment"]})
        for r in conn.execute(f"SELECT n.*, u.full_name FROM notes n JOIN users u ON u.id=n.user_id WHERE n.corr_id IN ({ph})", letters_ids):
            ev.append({"at": r["created_at"], "type": r["kind"], "corr_id": r["corr_id"], "ref": ref[r["corr_id"]], "title": ("ختم: " if r["kind"] == "stamp" else "ملاحظة: ") + r["text"][:100], "detail": r["full_name"]})
        for r in conn.execute(f"SELECT at, entity_id, username, action, detail FROM audit_log WHERE entity='correspondence' AND entity_id IN ({ph}) AND action IN('update','reply_linked','link','legal_hold','workflow_start','workflow_done') ORDER BY id", letters_ids):
            ev.append({"at": r["at"], "type": "audit", "corr_id": r["entity_id"], "ref": ref[r["entity_id"]], "title": {"update": "تحديث", "reply_linked": "تسجيل رد", "link": "ربط كرد", "legal_hold": "تجميد قانوني", "workflow_start": "بدء مسار", "workflow_done": "اكتمال المسار"}[r["action"]], "detail": r["detail"]})
        ev.sort(key=lambda e: (e["at"] or "", e["type"]))
        return 200, {"root": root, "current": cid, "letters": letters, "events": ev[:600]}

    # ───────────── ملصق QR + باركود، والبحث بالمسح ─────────────
    @route("GET", r"/api/correspondences/(?P<id>\d+)/label")
    def label(ctx):
        cid = int(ctx.params["id"])
        c = S.get_visible(ctx, cid)
        n = ctx.conn.execute("SELECT COUNT(*) FROM attachments WHERE corr_id=? AND removed_at IS NULL", (cid,)).fetchone()[0]
        payload = f"ACP|{c['ref_no']}|{c['doc_date']}|{n}"
        audit(ctx, "label", "correspondence", cid, c["ref_no"])
        return 200, {"ref_no": c["ref_no"], "date": c["doc_date"], "att_count": n, "subject": c["subject"] if c["conf_level"] == 0 else "(مراسلة سرية)",
                     "party": c["party"] if c["conf_level"] == 0 else "", "direction": c["direction"], "payload": payload,
                     "qr_svg": qr_svg(payload, 3), "barcode_svg": code128_svg(c["ref_no"], 46, 2)}

    @route("GET", r"/api/lookup")
    def lookup(ctx):
        """فتح معاملة بمسح الباركود/QR: يقبل الرقم المجرد أو حمولة QR (ACP|رقم|تاريخ|عدد)."""
        ref = (ctx.query.get("ref") or "").strip()
        if ref.startswith("ACP|"):
            ref = ref.split("|")[1] if len(ref.split("|")) > 1 else ""
        if not re.fullmatch(r"[A-Za-z0-9\-]{5,40}", ref):
            raise ApiError(400, "رقم قيد غير صالح")
        vs, vp = S.visible_clause(ctx.user)
        r = ctx.conn.execute(f"SELECT c.id, c.ref_no FROM correspondences c WHERE UPPER(c.ref_no)=UPPER(?) AND {vs}", [ref] + vp).fetchone()
        if not r:
            raise ApiError(404, "لا توجد معاملة بهذا الرقم ضمن ما يحق لك الاطلاع عليه")
        return 200, {"id": r["id"], "ref_no": r["ref_no"]}

    # ───────────── الخطاب الرسمي بالترويسة (للطباعة/PDF من المتصفح) ─────────────
    @route("GET", r"/api/correspondences/(?P<id>\d+)/letter")
    def letter(ctx):
        cid = int(ctx.params["id"])
        c = S.get_visible(ctx, cid)
        st = S.get_settings(ctx.conn)
        sigs = [dict(r) for r in ctx.conn.execute(
            "SELECT s.decision, s.signed_at, s.snapshot, u.full_name, u.job_title FROM signatures s JOIN users u ON u.id=s.user_id WHERE s.corr_id=? AND s.decision='approve' ORDER BY s.id", (cid,))]
        for s in sigs:
            snap = json.loads(s.pop("snapshot"))
            s["step"] = snap.get("step")
            s["on_behalf_of"] = (ctx.conn.execute("SELECT full_name FROM users WHERE id=?", (snap["on_behalf_of"],)).fetchone() or [None])[0] if snap.get("on_behalf_of") else None
        parent = ctx.conn.execute("SELECT ref_no, subject, doc_date FROM correspondences WHERE id=?", (c["parent_id"],)).fetchone() if c["parent_id"] else None
        audit(ctx, "letter_print", "correspondence", cid, c["ref_no"])
        contact = " • ".join(x for x in (st.get("org_address"), st.get("org_phone"), st.get("org_email"), st.get("org_website")) if x)
        return 200, {"org_name": st["org_name"], "org_header": st["org_header"], "org_footer": st["org_footer"], "org_stamp": st["org_stamp"],
                     "org_contact": contact, "has_logo": bool(st.get("org_logo_type")), "logo_v": st.get("org_logo_v", ""),
                     "ref_no": c["ref_no"], "doc_date": c["doc_date"], "party": c["party"], "subject": c["subject"], "body": c["summary"] or "",
                     "direction": c["direction"], "ref_to": dict(parent) if parent else None, "signatures": sigs, "qr_svg": qr_svg(f"ACP|{c['ref_no']}|{c['doc_date']}|0", 3)}

    # ───────────── تصدير Excel ─────────────
    @route("GET", r"/api/export/xlsx")
    def export_xlsx(ctx):
        if not S.can(ctx.user, "export"):
            raise ApiError(403, "لا تملك صلاحية التصدير")
        where, p = S.build_filters(ctx)
        rows = ctx.conn.execute(f"SELECT {S.FIELDS} {S.JOINS} WHERE {where} ORDER BY c.id DESC LIMIT 20000", p).fetchall()
        names = {k: v["label"] for k, v in S.classes(ctx.conn, False).items()}
        stat = {"draft": "مسودة", "new": "جديد", "in_progress": "جارٍ", "under_review": "قيد المراجعة", "awaiting_signature": "بانتظار التوقيع", "referred": "محال", "closed": "منجز", "archived": "مؤرشف"}
        head = ["رقم القيد", "النطاق", "الاتجاه", "الموضوع", "الجهة", "رقم مرجع الجهة", "تاريخ المراسلة", "التصنيف", "الحالة", "الوحدة", "المسؤول", "الاستحقاق", "تاريخ التسجيل", "نوع المعاملة", "المجلد"]
        data = [head] + [[r["ref_no"], "خارجي" if r["scope"] == "external" else "داخلي", "وارد" if r["direction"] == "incoming" else "صادر", r["subject"], r["party"], r["party_ref"],
                          r["doc_date"], names.get(r["priority"], r["priority"]), stat.get(r["status"], r["status"]), r["department_name"], r["assignee_name"], r["due_date"],
                          r["created_at"], r["type_name"], r["folder_name"]] for r in rows]
        audit(ctx, "export", detail=f"xlsx — {len(rows)} سجل")
        return S.Raw(200, make_xlsx([("المراسلات", data)]), {"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                                             "Content-Disposition": "attachment; filename=correspondences.xlsx"})
