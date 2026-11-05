// هوية الجهة: ألوان المنصة، الشعار، من نحن، وشاشة الإدارة (تُحمَّل قبل app.js وتضيف فقط)
const BRAND = { name: "", colors: { primary: "", side: "" }, logo: false, logo_v: "", login_message: "" };

const _hex = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
const _toHex = (a) => "#" + a.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");
const _mix = (c, to, t) => _toHex(_hex(c).map((v, i) => v + (to[i] - v) * t));

function _isDark() {
  const t = document.documentElement.dataset.theme;
  return t === "dark" || (t !== "light" && window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
}

// الألوان تُضبط عبر CSSOM (لا style مضمّن) فتتوافق مع سياسة CSP. في السمة الداكنة تبقى الألوان الافتراضية المضبوطة للتباين.
function applyBrandColors() {
  const root = document.documentElement.style, c = BRAND.colors || {};
  const vars = ["--brand", "--brand-2", "--brand-soft", "--accent", "--side"];
  vars.forEach((v) => root.removeProperty(v));
  if (_isDark()) return;
  if (c.primary) {
    root.setProperty("--brand", _mix(c.primary, [0, 0, 0], 0.15));
    root.setProperty("--brand-2", c.primary);
    root.setProperty("--brand-soft", _mix(c.primary, [255, 255, 255], 0.9));
    root.setProperty("--accent", _mix(c.primary, [0, 0, 0], 0.25));
  }
  if (c.side) root.setProperty("--side", c.side);
  const m = document.querySelector('meta[name="theme-color"]');
  if (m && c.primary) m.setAttribute("content", c.primary);
}

async function loadBrand() {
  try {
    const r = await fetch("/api/public/branding");
    if (r.ok) Object.assign(BRAND, await r.json());
  } catch { /* الهوية اختيارية: تبقى الألوان الافتراضية */ }
  applyBrandColors();
  if (BRAND.name) document.title = BRAND.name;
}
new MutationObserver(applyBrandColors).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
try { matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyBrandColors); } catch { /* */ }
const brandReady = loadBrand().then(() => { if (typeof render === "function" && typeof state !== "undefined" && state.user) render(); });

function brandLogo(cls) {
  return BRAND.logo ? h("img", { class: cls || "brandlogo", src: "/api/public/logo?v=" + encodeURIComponent(BRAND.logo_v), alt: BRAND.name ? "شعار " + BRAND.name : "شعار الجهة" }) : null;
}

// ───────── صفحة «من نحن» ─────────
EXT_ROUTES["/about"] = async () => {
  const p = await api("/org/profile");
  const sec = (title, text) => (text ? h("div", { class: "card" }, h("h3", {}, title), h("p", { class: "about-txt" }, text)) : null);
  const contact = [["العنوان", p.org_address], ["الهاتف", p.org_phone], ["البريد", p.org_email], ["الموقع", p.org_website]].filter(([, v]) => v);
  const empty = !(p.org_about || p.org_vision || p.org_mission || p.org_goals);
  return [h("div", { class: "about-head" }, brandLogo("about-logo"), h("div", {}, h("h2", {}, p.org_name || "من نحن"), p.org_name_en ? h("div", { class: "muted ltr" }, p.org_name_en) : null)),
    empty ? h("div", { class: "card muted" }, "لم تُدخَل بيانات الجهة بعد." + (state.user.role === "admin" ? " يمكنك إدخالها من إعدادات النظام ← الهوية والترويسة." : "")) : null,
    sec("من نحن", p.org_about), sec("الرؤية", p.org_vision), sec("الرسالة", p.org_mission), sec("الأهداف", p.org_goals),
    contact.length ? h("div", { class: "card" }, h("h3", {}, "التواصل"), h("table", {}, h("tbody", {}, contact.map(([k, v]) => h("tr", {}, h("th", {}, k), h("td", {}, v)))))) : null];
};

// ───────── شاشة الإدارة (تحلّ محل «الهوية والترويسة» السابقة وتحتفظ بحقولها) ─────────
async function brandingAdminView() {
  const p = await api("/admin/org");
  const inp = (key, label, opts = {}) => {
    const el = opts.area ? h("textarea", { rows: opts.rows || 4, maxlength: opts.max, "aria-label": label }) : h("input", { maxlength: opts.max, "aria-label": label, dir: opts.ltr ? "ltr" : null });
    el.value = p[key] || ""; return [key, label, el];
  };
  const text = [inp("org_name", "اسم الجهة", { max: 120 }), inp("org_name_en", "اسم الجهة بالإنجليزية", { max: 120, ltr: true }), inp("org_address", "العنوان", { max: 200 }), inp("org_phone", "الهاتف", { max: 40, ltr: true }),
    inp("org_email", "البريد الإلكتروني", { max: 120, ltr: true }), inp("org_website", "الموقع الإلكتروني (https://…)", { max: 150, ltr: true }), inp("login_message", "رسالة صفحة الدخول", { max: 300 })];
  const letter = [inp("org_header", "نص الترويسة (تحت الاسم في الخطاب الرسمي)", { area: true, rows: 3, max: 500 }), inp("org_footer", "التذييل (عنوان/هاتف/موقع)", { area: true, rows: 3, max: 500 }), inp("org_stamp", "نص الختم الرسمي", { max: 100 })];
  const about = [inp("org_about", "من نحن", { area: true, rows: 5, max: 3000 }), inp("org_vision", "الرؤية", { area: true, rows: 3, max: 1500 }), inp("org_mission", "الرسالة", { area: true, rows: 3, max: 1500 }), inp("org_goals", "الأهداف (هدف في كل سطر)", { area: true, rows: 5, max: 3000 })];
  const all = [...text, ...letter, ...about];

  const cp = h("input", { type: "color", "aria-label": "اللون الأساسي", value: p.colors.primary || "#c2570c" }), cs = h("input", { type: "color", "aria-label": "لون القائمة الجانبية", value: p.colors.side || "#1f2937" });
  const usePrimary = h("input", { type: "checkbox", checked: !!p.colors.primary }), useSide = h("input", { type: "checkbox", checked: !!p.colors.side });
  const preview = () => { BRAND.colors = { primary: usePrimary.checked ? cp.value : "", side: useSide.checked ? cs.value : "" }; applyBrandColors(); };
  [cp, cs, usePrimary, useSide].forEach((e) => e.addEventListener("input", preview));
  const logoFile = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", "aria-label": "ملف الشعار" });

  return [adminHead("branding", "الهوية والترويسة", "ألوان المنصة وشعارها وبيانات الجهة؛ تظهر في القائمة وصفحة الدخول وصفحة «من نحن» والخطاب الرسمي المطبوع."),
    h("div", { class: "card" }, h("h3", {}, "الشعار"), h("div", { class: "row" }, BRAND.logo ? brandLogo("brandlogo big") : h("span", { class: "muted" }, "لا يوجد شعار"), logoFile,
      h("button", { class: "btn", onclick: guardX(async () => {
        const fl = logoFile.files[0]; if (!fl) throw new Error("اختر ملف الشعار أولًا");
        await api("/admin/org/logo", { method: "POST", body: fl }); toast("رُفع الشعار"); await loadBrand(); render();
      }) }, "رفع الشعار"), BRAND.logo ? h("button", { class: "btn ghost", onclick: guardX(async () => { await api("/admin/org/logo", { method: "DELETE" }); toast("حُذف الشعار"); await loadBrand(); render(); }) }, "حذف الشعار") : null),
      h("p", { class: "small muted" }, "PNG أو JPEG أو WebP، حتى 1 ميغابايت. يُفضَّل خلفية شفافة وأبعاد مربعة تقريبًا. لا يُقبل SVG لأسباب أمنية.")),
    h("div", { class: "card" }, h("h3", {}, "ألوان المنصة"),
      h("div", { class: "form-grid" }, f("اللون الأساسي (الأزرار والروابط والتمييز)", h("div", { class: "row" }, usePrimary, " تخصيص ", cp)),
        f("لون القائمة الجانبية", h("div", { class: "row" }, useSide, " تخصيص ", cs))),
      h("p", { class: "small muted" }, "المعاينة فورية؛ لا تُحفظ إلا بالضغط على «حفظ الألوان». يُرفض لون القائمة الفاتح جدًا حفاظًا على وضوح النص، وتبقى السمة الداكنة بألوانها المضبوطة."),
      h("button", { class: "btn", onclick: guardX(async () => {
        await api("/admin/org", { method: "PUT", body: { color_primary: usePrimary.checked ? cp.value : "", color_side: useSide.checked ? cs.value : "" } });
        toast("حُفظت الألوان"); await loadBrand();
      }) }, "حفظ الألوان"), " ", h("button", { class: "btn ghost", onclick: guardX(async () => {
        await api("/admin/org", { method: "PUT", body: { color_primary: "", color_side: "" } }); toast("أُعيدت الألوان الافتراضية"); await loadBrand(); render();
      }) }, "استعادة الألوان الافتراضية")),
    h("div", { class: "card" }, h("h3", {}, "بيانات الجهة"), h("div", { class: "form-grid" }, text.map(([, l, el]) => f(l, el)))),
    h("div", { class: "card" }, h("h3", {}, "الترويسة والتذييل (الخطاب الرسمي)"), h("div", { class: "form-grid" }, letter.map(([, l, el]) => f(l, el)))),
    h("div", { class: "card" }, h("h3", {}, "من نحن والرؤية والرسالة والأهداف"), h("div", { class: "form-grid" }, about.map(([, l, el]) => f(l, el)))),
    h("button", { class: "btn", onclick: guardX(async () => {
      await api("/admin/org", { method: "PUT", body: Object.fromEntries(all.map(([k, , el]) => [k, el.value])) }); toast("حُفظت بيانات الجهة"); await loadBrand();
    }) }, "حفظ بيانات الجهة"),
    h("div", { class: "card" }, h("h3", {}, "توثيق واجهة API"), h("p", { class: "small muted" }, "وثيقة OpenAPI مولَّدة آليًا من المسارات الفعلية، ومصادقة OAuth2 (client-credentials) بمفتاح API لإصدار JWT."),
      h("a", { class: "btn ghost", href: "/apidocs.html", target: "_blank" }, "فتح توثيق API"), " ", h("a", { class: "btn ghost", href: "/api/openapi.json", target: "_blank" }, "openapi.json"))];
}
ADMIN_VIEWS.branding = brandingAdminView;
