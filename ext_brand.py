"""وحدة ملحقة: هوية الجهة — ألوان المنصة بيد المدير، الشعار، بيانات الجهة، من نحن/الرؤية/الرسالة/الأهداف، الترويسة والتذييل.

تُخزَّن القيم في جدول الإعدادات نفسه (المفاتيح org_name وorg_header وorg_footer وorg_stamp موجودة سابقًا ويقرؤها الخطاب الرسمي)،
فلا يتغير سلوك أي ميزة سابقة. نقطة عامة بلا مصادقة تُعيد الاسم والألوان وحالة الشعار فقط (لتلوين صفحة الدخول).
"""
import os
import re

TEXT_LIMITS = {"org_name": 120, "org_name_en": 120, "org_address": 200, "org_phone": 40, "org_email": 120, "org_website": 150,
               "org_about": 3000, "org_vision": 1500, "org_mission": 1500, "org_goals": 3000,
               "org_header": 500, "org_footer": 500, "org_stamp": 100, "login_message": 300}
COLOR_KEYS = ("color_primary", "color_side")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_LOGO = 1024 * 1024


def luminance(hexc):
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def image_mime(data):
    """نوع الصورة من التوقيع الفعلي للملف (لا من الامتداد). SVG مرفوض عمدًا لأنه قد يحمل سكربتًا."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def install(S):
    route, ApiError, now, audit = S.route, S.ApiError, S.now, S.audit
    keys = set(TEXT_LIMITS) | set(COLOR_KEYS) | {"org_logo_type", "org_logo_v"}
    S.SETTING_DEFAULTS.update({k: "" for k in keys if k not in S.SETTING_DEFAULTS})
    S.SETTING_TEXT_KEYS.update(keys)
    BRAND_DIR = os.path.join(S.DATA, "branding")
    LOGO = os.path.join(BRAND_DIR, "logo.bin")

    def upsert(conn, k, v):
        conn.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    def profile(conn):
        st = S.get_settings(conn)
        out = {k: st.get(k, "") for k in TEXT_LIMITS}
        out["colors"] = {"primary": st.get("color_primary", ""), "side": st.get("color_side", "")}
        out["logo"] = bool(st.get("org_logo_type")) and os.path.isfile(LOGO)
        out["logo_v"] = st.get("org_logo_v", "")
        return out

    @route("GET", r"/api/public/branding", auth=False)
    def public_branding(ctx):
        p = profile(ctx.conn)
        return 200, {"name": p["org_name"], "colors": p["colors"], "logo": p["logo"], "logo_v": p["logo_v"], "login_message": p["login_message"]}

    @route("GET", r"/api/org/profile")
    def org_profile(ctx):
        return 200, profile(ctx.conn)

    @route("GET", r"/api/admin/org", roles=("admin",))
    def org_admin_get(ctx):
        return 200, profile(ctx.conn)

    @route("PUT", r"/api/admin/org", roles=("admin",))
    def org_update(ctx):
        b = ctx.body
        if not isinstance(b, dict) or not b:
            raise ApiError(400, "لا يوجد ما يُحدَّث")
        clean = {}
        for k, v in b.items():
            if k not in TEXT_LIMITS and k not in COLOR_KEYS:
                raise ApiError(400, f"حقل غير معروف: {k}")
            v = "" if v is None else v
            if not isinstance(v, str):
                raise ApiError(400, f"قيمة غير صالحة: {k}")
            v = v.strip()
            if k in COLOR_KEYS:
                if v and not HEX.match(v):
                    raise ApiError(400, "اللون بصيغة #RRGGBB")
                if v and k == "color_side" and 1.05 / (luminance(v) + 0.05) < 4.5:
                    raise ApiError(400, "لون القائمة الجانبية فاتح جدًا: النص الأبيض لن يكون مقروءًا؛ اختر لونًا أغمق")
                v = v.lower()
            else:
                if len(v) > TEXT_LIMITS[k]:
                    raise ApiError(400, f"النص طويل جدًا ({k}: الحد {TEXT_LIMITS[k]})")
                if k == "org_email" and v and not S.EMAIL_RE.match(v):
                    raise ApiError(400, "البريد الإلكتروني غير صالح")
                if k == "org_website" and v and not re.match(r"^https?://[^\s]+$", v):
                    raise ApiError(400, "الموقع يبدأ بـ http:// أو https://")
                if k == "org_phone" and v and not re.fullmatch(r"[0-9+\-()\s/،,]{3,40}", v):
                    raise ApiError(400, "رقم هاتف غير صالح")
            clean[k] = v
        with S.tx(ctx.conn):
            for k, v in clean.items():
                upsert(ctx.conn, k, v)
            audit(ctx, "org_update", detail="، ".join(sorted(clean)))
        return 200, profile(ctx.conn)

    @route("POST", r"/api/admin/org/logo", roles=("admin",))
    def logo_upload(ctx):
        n = int(ctx.h.headers.get("Content-Length") or 0)
        if n <= 0:
            raise ApiError(400, "الملف فارغ")
        if n > MAX_LOGO:
            raise ApiError(413, "حجم الشعار يتجاوز 1MB")
        data = ctx.h.rfile.read(n)
        mime = image_mime(data)
        if not mime:
            raise ApiError(400, "صيغة الشعار: PNG أو JPEG أو WebP فقط (يُفحص محتوى الملف لا امتداده)")
        os.makedirs(BRAND_DIR, exist_ok=True)
        with open(LOGO, "wb") as f:
            f.write(data)
        with S.tx(ctx.conn):
            upsert(ctx.conn, "org_logo_type", mime)
            upsert(ctx.conn, "org_logo_v", str(int(__import__("time").time())))
            audit(ctx, "org_logo", detail=f"{mime} — {n} بايت")
        return 200, profile(ctx.conn)

    @route("DELETE", r"/api/admin/org/logo", roles=("admin",))
    def logo_delete(ctx):
        try:
            os.remove(LOGO)
        except FileNotFoundError:
            pass
        with S.tx(ctx.conn):
            upsert(ctx.conn, "org_logo_type", "")
            audit(ctx, "org_logo_delete")
        return 200, profile(ctx.conn)

    @route("GET", r"/api/public/logo", auth=False)
    def logo_get(ctx):
        st = S.get_settings(ctx.conn)
        if not st.get("org_logo_type") or not os.path.isfile(LOGO):
            raise ApiError(404, "لا يوجد شعار")
        with open(LOGO, "rb") as f:
            return S.Raw(200, f.read(), {"Content-Type": st["org_logo_type"], "Cache-Control": "public, max-age=300"})
