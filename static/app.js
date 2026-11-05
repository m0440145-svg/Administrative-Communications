"use strict";
// منصة الاتصالات الإدارية — واجهة المستخدم (JavaScript خالص، بدون مكتبات خارجية).
// كل النصوص تُدرج عبر textContent لمنع هجمات XSS.

const L = {
  status: { draft: "مسودة", new: "جديد", in_progress: "جارٍ", under_review: "قيد المراجعة", awaiting_signature: "بانتظار التوقيع", referred: "محال", closed: "منجز", archived: "مؤرشف" },
  priority: {}, // تُحمَّل من الخادم (تصنيفات الوثيقة) في loadRefs
  conf: ["عادي", "سري", "سري للغاية"],
  role: { admin: "مدير النظام", manager: "مدير إدارة", clerk: "مكتب الصادر والوارد", employee: "موظف" },
  scope: { external: "خارجي", internal: "داخلي" },
  direction: { incoming: "وارد", outgoing: "صادر" },
  action: { create: "تسجيل المراسلة", update: "تحديث", transfer: "إحالة", transfer_done: "تنفيذ إحالة", attach: "إضافة مرفق",
    download: "تنزيل مرفق", login: "دخول", logout: "خروج", login_failed: "فشل دخول", export: "تصدير", password_changed: "تغيير كلمة المرور",
    reply_linked: "تسجيل رد / ربط صادر", link: "ربط كرد", lookup_create: "إضافة مجلد/نوع", lookup_update: "تعديل مجلد/نوع",
    class_create: "إضافة تصنيف", class_update: "تعديل تصنيف", perm_update: "تعديل صلاحية", dept_update: "تعديل وحدة", user_create: "إنشاء مستخدم", user_update: "تعديل مستخدم", dept_create: "إنشاء إدارة", apikey_create: "إنشاء مفتاح API",
    apikey_revoke: "إلغاء مفتاح API", webhook_create: "إنشاء Webhook", webhook_delete: "حذف Webhook", integrity_failure: "فشل سلامة ملف" },
};

const state = { token: sessionStorage.getItem("acp_token"), user: null, depts: [], users: [], cls: [], clsMap: {}, folders: [], types: [], directives: [], filters: [], prefs: { theme: "auto", page_size: 20 }, unread: 0 };

// ───────── أدوات ─────────
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function toast(msg, bad) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "show" + (bad ? " bad" : "");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (t.className = ""), 3500);
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  let body = opts.body;
  if (body && !(body instanceof Blob)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
  const res = await fetch("/api" + path, { method: opts.method || "GET", headers, body });
  if (opts.raw && res.ok) return res;
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && state.token) { logoutLocal(); throw new Error("انتهت الجلسة، سجّل الدخول من جديد"); }
  if (!res.ok) { const e = new Error(data.error || "حدث خطأ"); e.data = data; throw e; }
  return data;
}

const fmtDate = (s) => (s ? s.slice(0, 10) : "—");
const fmtDT = (s) => (s ? s.slice(0, 16).replace("T", " ") : "—");
function hijri(s) {
  if (!s) return "";
  try { return new Intl.DateTimeFormat("ar-SA-u-ca-islamic-umalqura-nu-latn", { dateStyle: "long" }).format(new Date(s.slice(0, 10) + "T12:00:00")); }
  catch { return ""; }
}
const today = () => new Date().toISOString().slice(0, 10);
const sizeStr = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.ceil(n / 1024) + " KB");
const pill = (cls, text) => h("span", { class: "pill " + cls }, text);
const statusPill = (s) => pill(s, L.status[s] || s);
const typeLabel = (c) => `${L.direction[c.direction]} ${L.scope[c.scope]}`;
const deptName = (id) => (state.depts.find((d) => d.id === id) || {}).name || "—";
const isStaff = () => ["admin", "manager", "clerk"].includes(state.user.role);
const can = (p) => state.user.role === "admin" || (state.user.perms || []).includes(p);
const folderOptions = () => Object.fromEntries(state.folders.map((f) => [f.id, "\u00a0\u00a0".repeat(f.depth || 0) + (f.depth ? "↳ " : "") + f.name])); // شجرة المجلدات
const activeDepts = () => state.depts.filter((d) => d.active);
const deptOptions = () => Object.fromEntries(activeDepts().map((d) => [d.id, d.name]));
// حالة SLA (مهلة التصنيف بساعات العمل): سليم / يقترب (75٪) / متجاوز → يُلوَّن الصف بالأحمر
function slaBadge(c) {
  if (!c.sla_state) return "—";
  const due = (c.sla_due_at || "").slice(0, 16).replace("T", " ");
  return h("span", { class: "sla " + c.sla_state, title: "الموعد: " + due }, { ok: "✔ ضمن المهلة", warn: "⚠ يقترب", breach: "⛔ متجاوز" }[c.sla_state], h("div", { class: "small muted ltr" }, due.slice(5)));
}

// حالة الرد: للوارد الذي يحتاج رد → «بانتظار الرد» أو «تم الرد»؛ وللصادر المرتبط بوارد → «رد على…»
function replyBadge(c) {
  if (c.reply_count > 0) return pill("closed", `✔ تم الرد (${c.reply_count})`);
  if (c.needs_reply && !["closed", "archived"].includes(c.status)) return pill("in_progress", "⏳ بانتظار الرد");
  if (c.parent_id) return h("a", { href: "#/c/" + c.parent_id, class: "small" }, "↩ رد على وارد");
  return "—";
}
function prioPill(key) {
  const c = state.clsMap[key];
  const el = pill("", c ? c.label : key);
  if (c) { el.style.background = c.color + "22"; el.style.color = c.color; el.style.fontWeight = "600"; el.style.border = "1px solid " + c.color + "55"; }
  return el;
}

function logoutLocal() {
  state.token = null; state.user = null;
  sessionStorage.removeItem("acp_token");
  render();
}

// ───────── الإطار العام ─────────
const NAV = [
  ["#/dashboard", "لوحة التحكم"],
  ["#/tasks", "مهامي"],
  ["#/new", "تسجيل مراسلة جديدة"],
  ["sep", "المراسلات"],
  ["#/list?direction=incoming", "الوارد"],
  ["#/list?direction=outgoing", "الصادر"],
  ["#/list", "المعاملات"],
  ["#/search", "بحث متقدم"],
  ["#/notifications", "الإشعارات"],
  ["#/delegation", "التفويض"],
  ["#/security", "الأمان والتفضيلات"],
];
const EXEC_NAV = [["#/executive", "واجهة القيادة"]];
const REPORT_NAV = [["#/reports", "التقارير والمؤشرات"]];
const ADMIN_NAV = [["sep", "إعدادات النظام"], ["#/admin/units", "الوحدات"], ["#/admin/staff", "الموظفون"], ["#/admin/classes", "التصنيفات والخصوصية"],
  ["#/admin/workflows", "مسارات العمل"], ["#/admin/sla", "اتفاقيات الخدمة SLA"], ["#/admin/channels", "قنوات الإشعار"], ["#/admin/schedules", "التقارير المجدولة"], ["#/admin/security", "سياسات الأمان"], ["#/admin/branding", "الهوية والترويسة"], ["#/admin/retention", "الاحتجاز والإتلاف"], ["#/admin/ops", "النسخ والسلامة"], ["#/admin/import", "الاستيراد"],
  ["#/admin/perms", "الصلاحيات"], ["#/admin/audit", "سجل الإجراءات"], ["#/admin/integration", "التكامل (API)"]];

const THEMES = { auto: "تلقائي", light: "فاتح", dark: "داكن" };
function applyTheme(t) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  try { localStorage.setItem("acp_theme", t || "auto"); } catch { /* التخزين غير متاح */ }
}
(() => { try { applyTheme(localStorage.getItem("acp_theme") || "auto"); } catch { /* */ } })();

function shell(content, taskCount) {
  const cur = location.hash || "#/dashboard";
  // قوائم منسدلة (مجموعات قابلة للطيّ): تُحفظ حالة كل مجموعة، وتُفتح تلقائيًا المجموعة التي فيها الصفحة الحالية
  const role = state.user.role, leader = ["admin", "manager"].includes(role), reports = isStaff() || can("export");
  const GROUPS = [
    { id: "home", title: "🏠 الرئيسية", items: [["#/dashboard", "لوحة التحكم"], ["#/tasks", "مهامي"], ...(leader ? EXEC_NAV : [])] },
    { id: "mail", title: "✉️ المراسلات", items: [["#/new", "تسجيل مراسلة جديدة"], ["#/list?direction=incoming", "الوارد"], ["#/list?direction=outgoing", "الصادر"], ["#/list", "المعاملات"], ["#/search", "بحث متقدم"]] },
    { id: "follow", title: "📊 المتابعة والتقارير", items: [["#/notifications", "الإشعارات"], ["#/delegation", "التفويض"], ...(reports ? REPORT_NAV : [])] },
    ...(role === "admin" ? [
      { id: "adm1", title: "🏢 الهيكل والصلاحيات", items: [["#/admin/units", "الوحدات"], ["#/admin/staff", "الموظفون"], ["#/admin/perms", "الصلاحيات"], ["#/admin/security", "سياسات الأمان"]] },
      { id: "adm2", title: "🗂 التصنيف والمسارات", items: [["#/admin/classes", "التصنيفات والخصوصية"], ["#/admin/folders", "المجلدات"], ["#/admin/types", "أنواع المعاملات"], ["#/admin/directives", "توجيهات الإسناد"],
        ["#/admin/workflows", "مسارات العمل"], ["#/admin/rules", "قواعد التوجيه"], ["#/admin/templates", "القوالب"], ["#/admin/sla", "اتفاقيات الخدمة SLA"]] },
      { id: "adm3", title: "⚙️ التشغيل والحوكمة", items: [["#/admin/retention", "الاحتجاز والإتلاف"], ["#/admin/channels", "قنوات الإشعار"], ["#/admin/schedules", "التقارير المجدولة"], ["#/admin/branding", "الهوية والترويسة"],
        ["#/admin/ops", "النسخ والسلامة"], ["#/admin/import", "الاستيراد"], ["#/admin/audit", "سجل الإجراءات"], ["#/admin/integration", "التكامل (API)"]] },
    ] : []),
    { id: "me", title: "👤 حسابي", items: [["#/security", "الأمان والتفضيلات"]] },
  ];
  let navState = {};
  try { navState = JSON.parse(localStorage.getItem("acp_nav") || "{}"); } catch { /* */ }
  const isActive = (href) => cur === href || (!href.includes("?") && cur.split("?")[0] === href && !cur.includes("?"));
  const items = GROUPS.map((g) => {
    const hasActive = g.items.some(([href]) => isActive(href));
    const open = hasActive || (navState[g.id] !== undefined ? navState[g.id] : ["home", "mail"].includes(g.id));
    const badge = g.id === "home" && taskCount ? taskCount : g.id === "follow" && state.unread ? state.unread : 0;
    return h("details", { class: "navgroup" + (hasActive ? " has-active" : ""), open, ontoggle: (e) => { navState[g.id] = e.target.open; try { localStorage.setItem("acp_nav", JSON.stringify(navState)); } catch { /* */ } } },
      h("summary", {}, h("span", {}, g.title), badge ? h("span", { class: "badge-n" }, badge) : null),
      h("div", { class: "navitems" }, g.items.map(([href, label]) => h("a", { href, class: isActive(href) ? "active" : "" }, label,
        href === "#/tasks" && taskCount ? h("span", { class: "badge-n" }, taskCount) : null,
        href === "#/notifications" ? h("span", { class: "badge-n", id: "notif-badge", hidden: !state.unread }, state.unread || "") : null))));
  });
  const saved = state.filters.length ? [h("div", { class: "sep" }, "فلاتري المحفوظة"), ...state.filters.map((fl) => h("div", { class: "savedf" },
    h("a", { href: (fl.kind === "report" ? "#/reports?" : "#/list?") + fl.query }, fl.name),
    h("button", { class: "x", title: "حذف الفلتر", "aria-label": "حذف الفلتر " + fl.name, onclick: async () => { try { await api("/me/filters/" + fl.id, { method: "DELETE" }); await loadRefs(); render(); } catch (e) { toast(e.message, true); } } }, "×")))] : [];
  const themeSel = sel("theme", THEMES, (state.prefs || {}).theme || "auto", { "aria-label": "السمة", class: "themesel", onchange: async (e) => {
    applyTheme(e.target.value);
    try { await api("/me/prefs", { method: "PUT", body: { theme: e.target.value, page_size: (state.prefs || {}).page_size || 20 } }); state.prefs.theme = e.target.value; } catch { /* */ } } });
  return h("div", { class: "shell" },
    h("nav", { class: "side", "aria-label": "القائمة الرئيسية" },
      h("h1", {}, "منصة الاتصالات الإدارية"), scanBox(), items, saved,
      h("div", { class: "who" }, h("div", {}, state.user.full_name), h("div", { class: "small" }, L.role[state.user.role]),
        h("label", { class: "small themelb" }, "السمة ", themeSel),
        h("button", { class: "btn ghost sm", onclick: doLogout }, "تسجيل الخروج"))),
    h("main", {}, content));
}

async function doLogout() { try { await api("/logout", { method: "POST" }); } catch {} logoutLocal(); }

// ───────── الدخول وتغيير كلمة المرور ─────────
function loginView() {
  const err = h("div", { class: "err", role: "alert" });
  const form = h("form", { class: "card", onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    try {
      let r = await api("/login", { method: "POST", body: { username: form.u.value.trim(), password: form.p.value } });
      if (r.mfa_required) {  // خطوة ثانية: رمز TOTP أو رمز استرداد
        const code = prompt("أدخل رمز المصادقة الثنائية (6 أرقام من تطبيق المصادقة) أو أحد رموز الاسترداد:");
        if (!code) throw new Error("لم يُدخَل رمز التحقق");
        r = await api("/login/mfa", { method: "POST", body: { ticket: r.ticket, code: code.trim() } });
      }
      state.token = r.token; sessionStorage.setItem("acp_token", r.token); state.user = r.user;
      await boot();
    } catch (ex) { err.textContent = ex.message; }
  } },
    h("h2", {}, "تسجيل الدخول"),
    h("div", { class: "field" }, h("label", { for: "u" }, "اسم المستخدم"), h("input", { id: "u", name: "u", autocomplete: "username", required: true, autofocus: true })),
    h("div", { class: "field" }, h("label", { for: "p" }, "كلمة المرور"), h("input", { id: "p", name: "p", type: "password", autocomplete: "current-password", required: true })),
    err, h("button", { class: "btn", style: null }, "دخول"));
  return h("div", { class: "login" }, h("h1", {}, "منصة الاتصالات الإدارية"), form);
}

function changePasswordView(forced) {
  const err = h("div", { class: "err", role: "alert" });
  const form = h("form", { class: "card", onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    if (form.n.value !== form.n2.value) { err.textContent = "كلمتا المرور غير متطابقتين"; return; }
    try {
      await api("/me/password", { method: "POST", body: { current: form.c.value, new: form.n.value } });
      state.user.must_change = false; toast("تم تغيير كلمة المرور"); location.hash = "#/dashboard"; render();
    } catch (ex) { err.textContent = ex.message; }
  } },
    forced ? h("div", { class: "alert" }, "يجب تغيير كلمة المرور قبل المتابعة.") : null,
    f("كلمة المرور الحالية", h("input", { name: "c", type: "password", required: true, autocomplete: "current-password" })),
    f("كلمة المرور الجديدة (8 أحرف على الأقل، حروف وأرقام)", h("input", { name: "n", type: "password", required: true, minlength: 8, autocomplete: "new-password" })),
    f("تأكيد كلمة المرور", h("input", { name: "n2", type: "password", required: true, autocomplete: "new-password" })),
    err, h("button", { class: "btn" }, "حفظ"));
  return h("div", { class: "login" }, h("h2", {}, "تغيير كلمة المرور"), form);
}

// ───────── لوحة المعلومات ─────────
async function dashboardView() {
  const d = await api("/dashboard");
  const stat = (n, l, href, warn) => h("a", { class: "card stat" + (warn && n ? " warn" : ""), href }, h("div", { class: "n" }, n), h("div", { class: "l" }, l));
  const months = [...new Set(d.monthly.map((m) => m.month))];
  const max = Math.max(1, ...months.map((m) => d.monthly.filter((x) => x.month === m).reduce((s, x) => s + x.n, 0)));
  const bars = h("div", { class: "bars" }, months.length ? months.map((m) => {
    const inn = (d.monthly.find((x) => x.month === m && x.direction === "incoming") || {}).n || 0;
    const out = (d.monthly.find((x) => x.month === m && x.direction === "outgoing") || {}).n || 0;
    const seg = (cls, n) => { const s = h("div", { class: "seg " + cls, title: n }); s.style.height = (n / max) * 100 + "%"; return s; };
    return h("div", { class: "col" }, h("div", { class: "stack" }, seg("out", out), seg("in", inn)), h("div", { class: "small muted ltr" }, m));
  }) : h("div", { class: "empty" }, "لا بيانات بعد"));
  const typeRows = ["incoming", "outgoing"].map((dir) => {
    const n = (s) => (d.by_type.find((t) => t.scope === s && t.direction === dir) || {}).n || 0;
    return stat(n("external") + n("internal"), `${L.direction[dir]} (خارجي ${n("external")} · داخلي ${n("internal")})`, `#/list?direction=${dir}`);
  });
  return [h("h2", {}, "لوحة التحكم"),
    h("div", { class: "grid g4" }, stat(d.total, "إجمالي المراسلات", "#/list"), stat(d.my_tasks, "إحالات بانتظارك", "#/tasks"),
      stat(d.overdue, "متأخرة عن موعدها", "#/list?overdue=1", true), stat(d.urgent_open, "عاجلة مفتوحة", "#/list?priority=urgent_all")),
    h("div", { class: "grid g4", }, typeRows),
    h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h3", {}, "الحركة الشهرية (آخر 6 أشهر)"), bars,
        h("div", { class: "legend" }, h("span", {}, h("i", { class: "seg in" }), "وارد"), h("span", {}, h("i", { class: "seg out" }), "صادر"))),
      h("div", { class: "card" }, h("h3", {}, "حسب الحالة"),
        h("table", {}, h("tbody", {}, Object.entries(L.status).map(([k, v]) =>
          h("tr", {}, h("td", {}, h("a", { href: "#/list?status=" + k }, statusPill(k))), h("td", {}, d.by_status[k] || 0)))))),
    ),
    h("div", { class: "card" }, h("h3", {}, "آخر المراسلات"), corrTable(d.recent, true))];
}

// ───────── قوائم المراسلات ─────────
function corrTable(items, compact) {
  if (!items.length) return h("div", { class: "empty" }, "لا توجد مراسلات");
  return h("div", { class: "tbl-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, ["رقم القيد", "النوع", "الموضوع", compact ? null : "الجهة", "التاريخ", "الحالة", compact ? null : "المسؤول"].filter(Boolean).map((t) => h("th", {}, t)))),
    h("tbody", {}, items.map((c) => h("tr", {},
      h("td", {}, h("a", { href: "#/c/" + c.id, class: "ltr" }, c.ref_no)),
      h("td", {}, typeLabel(c)),
      h("td", {}, h("a", { href: "#/c/" + c.id }, c.subject), " ", c.priority !== "normal" ? prioPill(c.priority) : null, c.conf_level ? pill("conf" + c.conf_level, L.conf[c.conf_level]) : null),
      compact ? null : h("td", {}, c.party),
      h("td", { class: "ltr" }, fmtDate(c.doc_date)),
      h("td", {}, statusPill(c.status)),
      compact ? null : h("td", {}, c.assignee_name || "—"))))));
}


async function listView(q) {
  const filters = ["scope", "direction", "status", "priority", "dept", "q", "from", "to", "overdue", "mine", "read", "has_att", "period", "sort", "size", "folder", "type", "reply"];
  const params = new URLSearchParams();
  filters.forEach((k) => q.get(k) && params.set(k, q.get(k)));
  const page = Number(q.get("page") || 1);
  params.set("page", page);
  const data = await api("/correspondences?" + params);
  const dir = q.get("direction");
  const title = { incoming: "البريد الوارد", outgoing: "البريد الصادر" }[dir] || "المعاملات";
  const subtitle = { incoming: "إدارة الرسائل والمراسلات الواردة", outgoing: "إدارة الرسائل والمراسلات الصادرة" }[dir] || "كل الوارد والصادر، الداخلي والخارجي";
  const base = new URLSearchParams();
  ["scope", "direction"].forEach((k) => q.get(k) && base.set(k, q.get(k)));
  const link = (extra) => "#/list?" + new URLSearchParams({ ...Object.fromEntries(base), ...extra });

  const apply = () => {
    const p = new URLSearchParams();
    for (const k of filters) { const el = form.elements[k]; if (el && el.value) p.set(k, el.value); }
    if (q.get("direction") && !p.get("direction")) p.set("direction", q.get("direction"));
    if (q.get("overdue")) p.set("overdue", "1");
    if (q.get("mine")) p.set("mine", "1");
    location.hash = "#/list?" + p;
  };
  const dd = (name, label, opts) => sel(name, opts, q.get(name), { "aria-label": label, onchange: apply });
  const depts = { "": "كل الإدارات", ...Object.fromEntries(state.depts.map((d) => [d.id, d.name])) };
  const form = h("form", { class: "card filters", onsubmit: (e) => { e.preventDefault(); apply(); } },
    h("div", { class: "frow" },
      h("input", { name: "q", type: "search", placeholder: "ابحث باسم المرسل أو موضوع الرسالة أو رقم المعاملة…", "aria-label": "بحث", value: q.get("q") || "", class: "grow" }),
      dd("status", "حالة المعاملة", { "": "حالة المعاملة", ...L.status }),
      dd("priority", "الأولوية", { "": "الأولوية", ...L.priority }),
      dd("read", "حالة القراءة", { "": "حالة القراءة", unread: "غير مقروءة", read: "مقروءة" }),
      dd("has_att", "المرفقات", { "": "المرفقات", 1: "مع مرفقات" }),
      dd("period", "الفترة الزمنية", { "": "الفترة الزمنية", today: "اليوم", week: "آخر 7 أيام", month: "آخر شهر", year: "آخر سنة" }),
      dd("reply", "حالة الرد", { "": "حالة الرد", awaiting: "بانتظار الرد", replied: "تم الرد عليها" }),
      dd("sort", "الترتيب", { "": "الأحدث أولًا", old: "الأقدم أولًا" })),
    h("details", { class: "more", open: !!(q.get("scope") || q.get("dept") || q.get("from") || q.get("to") || q.get("type") || q.get("folder")) },
      h("summary", {}, "مزيد من الفلاتر"),
      h("div", { class: "frow" },
        dd("type", "نوع المعاملة", { "": "كل أنواع المعاملات", ...Object.fromEntries(state.types.map((t) => [t.id, t.name])) }),
        dd("folder", "المجلد", { "": "كل المجلدات", ...folderOptions() }),
        dd("scope", "نوع البريد", { "": "داخلي وخارجي", ...L.scope }), dd("dept", "الإدارة", depts),
        h("input", { type: "date", name: "from", "aria-label": "من تاريخ", value: q.get("from") || "", onchange: apply }),
        h("input", { type: "date", name: "to", "aria-label": "إلى تاريخ", value: q.get("to") || "", onchange: apply }),
        h("button", { type: "button", class: "btn ghost sm", onclick: () => saveCurrentFilter("list") }, "💾 حفظ الفلتر"),
        can("export") ? h("button", { type: "button", class: "btn ghost sm", onclick: () => exportCsv(params) }, "تصدير CSV") : null)));

  const k = data.kpi;
  const kpi = (cls, icon, n, label, href) => h("a", { class: "kpi " + cls, href }, h("div", { class: "ico" }, icon), h("div", {}, h("div", { class: "n" }, n), h("div", { class: "l" }, label)));
  const kpis = h("div", { class: "kpis" },
    kpi("k-total", "📥", k.total, "إجمالي " + (dir === "outgoing" ? "الصادر" : dir === "incoming" ? "الوارد" : "المراسلات"), link({})),
    kpi("k-unread", "✉️", k.unread, "غير مقروءة", link({ read: "unread" })),
    kpi("k-prog", "🕓", k.in_progress, "قيد المعالجة", link({ status: "in_progress" })),
    kpi("k-urgent", "⏰", k.urgent, "عاجلة", link({ priority: "urgent_all" })));

  // التحديد الجماعي
  const sel$ = new Set();
  const bar = h("div", { class: "bulk", hidden: true });
  const checks = [];
  const refreshBar = () => {
    bar.hidden = sel$.size === 0;
    bar.replaceChildren(h("span", {}, `المحدد: ${sel$.size}`), h("button", { type: "button", class: "btn sm", onclick: async () => {
      try { await api("/correspondences/read", { method: "POST", body: { ids: [...sel$] } }); toast("تم التعليم كمقروء"); render(); } catch (e) { toast(e.message, true); }
    } }, "تعليم كمقروء"));
    all.checked = sel$.size === data.items.length && data.items.length > 0;
    checks.forEach(([id, el]) => { el.checked = sel$.has(id); el.closest("tr").classList.toggle("sel", el.checked); });
  };
  const all = h("input", { type: "checkbox", "aria-label": "تحديد الكل", onchange: () => { data.items.forEach((c) => (all.checked ? sel$.add(c.id) : sel$.delete(c.id))); refreshBar(); } });

  const timeOf = (s) => { const d = new Date(s.replace(" ", "T")); return d.toLocaleTimeString("ar-u-nu-latn", { hour: "2-digit", minute: "2-digit", hour12: true }); };
  // أعمدة قابلة للتخصيص (تفضيل المستخدم): المفتاح ← عنوان وخلية
  const replyHref = (c) => `#/new?parent=${c.id}&scope=${c.scope}&direction=${c.direction === "incoming" ? "outgoing" : "incoming"}&subject=${encodeURIComponent("رد: " + c.subject)}`;
  const COLS = {
    read: ["حالة القراءة", (c) => h("td", { class: "nowrap" }, h("span", { class: "dot " + (c.is_read ? "r" : "u") }), c.is_read ? "مقروءة" : "غير مقروءة")],
    party: ["المرسل / الجهة", (c) => h("td", {}, c.party, h("div", { class: "small muted" }, typeLabel(c)))],
    subject: ["موضوع الرسالة", (c) => h("td", { class: "subj" },
      h("button", { class: "pinbtn" + (c.pinned ? " on" : ""), title: c.pinned ? "إلغاء التثبيت" : "تثبيت", "aria-label": "تثبيت", onclick: async () => { try { await api(`/correspondences/${c.id}/pin`, { method: "POST", body: { pinned: !c.pinned } }); render(); } catch (e) { toast(e.message, true); } } }, c.pinned ? "★" : "☆"),
      h("a", { href: "#/c/" + c.id }, c.subject), c.conf_level ? [" ", pill("conf" + c.conf_level, L.conf[c.conf_level])] : null,
      c.type_name || c.folder_name ? h("div", { class: "small muted" }, [c.type_name, c.folder_name && "📁 " + c.folder_name].filter(Boolean).join(" • ")) : null)],
    ref: ["رقم المعاملة", (c) => h("td", { class: "ltr nowrap" }, c.ref_no)],
    date: ["تاريخ الاستلام", (c) => h("td", { class: "nowrap" }, c.created_at.slice(0, 10).replaceAll("-", "/"), h("div", { class: "small muted" }, timeOf(c.created_at)))],
    priority: ["التصنيف", (c) => h("td", {}, prioPill(c.priority))],
    status: ["حالة المعاملة", (c) => h("td", {}, statusPill(c.status))],
    reply: ["الرد", (c) => h("td", { class: "nowrap" }, replyBadge(c))],
    att: ["المرفقات", (c) => h("td", { class: "nowrap" }, c.att_count ? `📎 ${c.att_count}` : "—")],
    sla: ["SLA", (c) => h("td", { class: "nowrap" }, slaBadge(c))],
    actions: ["الإجراءات", (c) => h("td", { class: "acts" },
      h("a", { class: "iconbtn", href: "#/c/" + c.id, title: "عرض", "aria-label": "عرض" }, "👁"),
      h("a", { class: "iconbtn", href: replyHref(c), title: "رد", "aria-label": "رد" }, "↩"),
      h("a", { class: "iconbtn", href: "#/c/" + c.id, title: "إحالة", "aria-label": "إحالة" }, "↪"))],
  };
  const DEFAULT_COLS = ["read", "party", "subject", "ref", "date", "priority", "status", "sla", "reply", "att", "actions"];
  const cols = ((state.prefs.columns || DEFAULT_COLS).filter((k) => COLS[k]));
  const colMenu = h("details", { class: "colmenu" }, h("summary", { class: "btn ghost sm" }, "⚙ الأعمدة"),
    h("div", { class: "card colbox" }, Object.entries(COLS).map(([k, [title]]) => h("label", { class: "opt" },
      h("input", { type: "checkbox", checked: cols.includes(k), onchange: async (e) => {
        const next = Object.keys(COLS).filter((x) => (x === k ? e.target.checked : cols.includes(x)));
        try { await api("/me/prefs", { method: "PUT", body: { theme: state.prefs.theme, page_size: state.prefs.page_size, columns: next } }); state.prefs.columns = next; render(); } catch (ex) { toast(ex.message, true); }
      } }), title)),
      h("button", { class: "btn ghost sm", onclick: async () => { await api("/me/prefs", { method: "PUT", body: { theme: state.prefs.theme, page_size: state.prefs.page_size, columns: [] } }); state.prefs.columns = null; render(); } }, "الأعمدة الافتراضية")));
  const rows = data.items.map((c) => {
    const cb = h("input", { type: "checkbox", "aria-label": "تحديد", onchange: () => { cb.checked ? sel$.add(c.id) : sel$.delete(c.id); refreshBar(); } });
    checks.push([c.id, cb]);
    return h("tr", { class: [c.is_read ? "" : "unread", c.sla_state === "breach" ? "sla-breach" : c.sla_state === "warn" ? "sla-warn" : "", c.pinned ? "pinned" : ""].join(" ").trim() },
      h("td", {}, cb), cols.map((k) => COLS[k][1](c)));
  });
  const table = rows.length ? h("div", { class: "tbl-wrap" }, h("table", { class: "mail" },
    h("thead", {}, h("tr", {}, h("th", {}, all), cols.map((k) => h("th", {}, COLS[k][0])))),
    h("tbody", {}, rows))) : h("div", { class: "card empty" }, "لا توجد مراسلات مطابقة");

  const pages = Math.max(1, Math.ceil(data.total / data.size));
  const go = (p) => { const n = new URLSearchParams(params); n.set("page", p); location.hash = "#/list?" + n; };
  const nums = [...new Set([1, page - 1, page, page + 1, pages].filter((p) => p >= 1 && p <= pages))].sort((a, b) => a - b);
  const pager = h("div", { class: "pager2" },
    h("div", { class: "pgs" },
      h("button", { class: "pg", disabled: page <= 1, onclick: () => go(page - 1), "aria-label": "السابق" }, "›"),
      nums.map((p, i) => [i && p - nums[i - 1] > 1 ? h("span", { class: "muted" }, "…") : null, h("button", { class: "pg" + (p === page ? " on" : ""), onclick: () => go(p) }, p)]),
      h("button", { class: "pg", disabled: page >= pages, onclick: () => go(page + 1), "aria-label": "التالي" }, "‹"),
      h("label", { class: "size" }, "عدد الصفوف في الصفحة", sel("size", { 10: "10", 25: "25", 50: "50", 100: "100" }, data.size, { onchange: (e) => { const n = new URLSearchParams(params); n.set("size", e.target.value); n.set("page", 1); location.hash = "#/list?" + n; } }))),
    h("div", { class: "muted small" }, `إجمالي ${data.total} مراسلة • الصفحة ${page} من ${pages}`));

  return [h("div", { class: "page-head" }, h("div", { class: "ph-ico" }, dir === "outgoing" ? "📤" : "📥"), h("div", {}, h("h2", {}, title), h("div", { class: "muted small" }, subtitle)),
    h("a", { class: "btn", href: `#/new?direction=${dir || "incoming"}` }, "+ تسجيل جديد")),
    kpis, form, h("div", { class: "row tools" }, colMenu,
      h("a", { class: "btn ghost sm", href: "#/list?" + new URLSearchParams({ ...Object.fromEntries(params), pinned: "1", page: 1 }) }, "★ المثبّتة"),
      h("span", { class: "small muted" }, "اختصارات: N معاملة جديدة · / فتح بالباركود")), bar, table, pager];
}

function sel(name, opts, val, extra) {
  const s = h("select", { name, ...(extra || {}) }, Object.entries(opts).map(([k, v]) => h("option", { value: k }, v)));
  s.value = val == null ? "" : String(val);
  return s;
}

async function exportCsv(params) {
  try {
    const p = new URLSearchParams(params); p.delete("page");
    const res = await api("/export?" + p, { raw: true });
    const url = URL.createObjectURL(await res.blob());
    h("a", { href: url, download: "correspondences.csv" }).click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (e) { toast(e.message, true); }
}

// ───────── معاملاتي ─────────
async function tasksView() {
  const tasks = await api("/tasks");
  return [h("h2", {}, "مهامي (الإحالات والطلبات بانتظار إجرائي)"),
    tasks.length === 0 ? h("div", { class: "card empty" }, "لا توجد إحالات معلّقة 🎉") :
      tasks.map((t) => {
        const note = h("input", { placeholder: "ملاحظة التنفيذ (اختياري)" });
        return h("div", { class: "card" },
          h("div", { class: "row" }, h("a", { href: "#/c/" + t.id, class: "ltr" }, t.ref_no), h("strong", {}, t.subject),
            t.priority !== "normal" ? prioPill(t.priority) : null, t.conf_level ? pill("conf" + t.conf_level, L.conf[t.conf_level]) : null),
          h("p", { class: "muted small" }, `من: ${t.from_name} — ${fmtDT(t.transferred_at)}`, t.transfer_due ? ` — الاستحقاق: ${t.transfer_due}` : ""),
          t.instruction ? h("p", {}, "التوجيه: ", t.instruction) : null,
          t.kind === "step" ? h("div", { class: "row" }, h("a", { class: "btn", href: "#/c/" + t.id }, "فتح المراسلة وتنفيذ الخطوة"), t.transfer_due ? h("span", { class: "small muted" }, "الاستحقاق: " + t.transfer_due) : null)
          : t.kind === "reply" ? h("div", { class: "row" }, h("a", { class: "btn", href: `#/new?parent=${t.id}&direction=outgoing&subject=${encodeURIComponent("رد: " + t.subject)}` }, "تسجيل الرد"),
            h("a", { class: "btn ghost", href: "#/c/" + t.id }, "فتح المراسلة"))
          : h("div", { class: "row" }, note, h("button", { class: "btn", onclick: async () => {
            try { await api(`/transfers/${t.transfer_id}/complete`, { method: "POST", body: { note: note.value } }); toast("تم تسجيل التنفيذ"); render(); }
            catch (e) { toast(e.message, true); }
          } }, "تم الاطلاع / التنفيذ")));
      })];
}

// ───────── مراسلة جديدة ─────────
// قائمة بحث منسدلة + زر «إضافة»؛ المختارون يظهرون كبطاقات قابلة للإزالة (لاختيار أكثر من موظف)
function peoplePicker(candidates, hint, placeholder = "ابحث عن موظف…", initial = []) {
  const sel$ = new Map(initial.map((u) => [u.id, u]));
  const chips = h("div", { class: "chips" });
  const renderChips = () => chips.replaceChildren(...[...sel$.values()].map((u) => h("div", { class: "chip" },
    u.full_name, u.job_title ? h("span", { class: "muted small" }, ` (${u.job_title})`) : null,
    h("button", { type: "button", class: "x", "aria-label": "إزالة " + u.full_name, onclick: () => { sel$.delete(u.id); renderChips(); } }, "×"))));
  let picked = null, active = -1, shown = [];
  const search = h("input", { type: "search", placeholder, autocomplete: "off", role: "combobox", "aria-label": placeholder });
  const dd = h("ul", { class: "dd", role: "listbox", hidden: true });
  const choose = (u) => { picked = u; search.value = u.full_name; dd.hidden = true; };
  const paint = () => {
    dd.replaceChildren(...shown.map((u, i) => h("li", { class: i === active ? "on" : "", role: "option", onmousedown: (e) => { e.preventDefault(); choose(u); } },
      u.full_name, u.job_title ? h("span", { class: "sub" }, " — " + u.job_title) : null)));
    dd.hidden = !shown.length;
  };
  const filter = () => {
    const t = search.value.trim();
    shown = candidates.filter((u) => !sel$.has(u.id) && (!t || (u.full_name + " " + (u.job_title || "")).includes(t))).slice(0, 50);
    active = shown.length ? 0 : -1; paint();
  };
  const add = () => {
    picked = picked || candidates.find((u) => u.full_name === search.value.trim()) || null;
    if (!picked) return toast("اختر موظفًا من القائمة", true);
    sel$.set(picked.id, picked); picked = null; search.value = ""; dd.hidden = true; renderChips();
  };
  search.addEventListener("input", () => { picked = null; filter(); });
  search.addEventListener("focus", filter);
  search.addEventListener("blur", () => setTimeout(() => (dd.hidden = true), 120));
  search.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(shown.length - 1, active + 1); paint(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(0, active - 1); paint(); }
    else if (e.key === "Enter") { e.preventDefault(); if (!dd.hidden && shown[active]) choose(shown[active]); else add(); }
    else if (e.key === "Escape") dd.hidden = true;
  });
  renderChips();
  const node = h("div", { class: "replyers" },
    h("div", { class: "combo" }, h("div", { class: "cbx" }, search, dd), h("button", { type: "button", class: "btn sm", onclick: add }, "إضافة")),
    chips, hint ? h("div", { class: "small muted" }, hint) : null);
  return { node, ids: () => [...sel$.keys()], add: (u) => { sel$.set(u.id, u); renderChips(); } };
}

async function newView(q) {
  const parent = q.get("parent");
  const [templates, workflows, parentC] = await Promise.all([api("/templates"), api("/workflows"), parent ? api("/correspondences/" + parent).catch(() => null) : null]);
  const tagsBox = tagsInput();
  const err = h("div", { class: "err", role: "alert" });
  const canExternal = can("create_external");
  let dir = q.get("direction") === "outgoing" ? "outgoing" : "incoming";
  const dyn = []; // عناصر تتغير نصوصها مع الاتجاه: [عنصر, نص الوارد, نص الصادر]
  const dl = (inText, outText, cls) => { const el = h("span", { class: cls || "lb" }); dyn.push([el, inText, outText]); return el; };
  const title = h("h2", {});
  const syncDir = () => {
    title.textContent = parent ? "تسجيل رد على مراسلة" : dir === "incoming" ? "إنشاء مراسلة واردة" : "إنشاء مراسلة صادرة";
    dyn.forEach(([el, a, b]) => (el.textContent = dir === "incoming" ? a : b));
  };
  const radio = (name, value, text, checked, onchange) => h("label", { class: "opt" },
    h("input", { type: "radio", name, value, checked, onchange }), text);
  const row = (label, control, req) => {
    if (control.tagName && ["INPUT", "SELECT", "TEXTAREA"].includes(control.tagName)) control.id = control.id || "fld" + ++fieldSeq;
    const lb = typeof label === "string" ? h("label", { class: "lb" + (req ? " req" : ""), for: control.id }, label)
      : (label.classList.add("lb"), req && label.classList.add("req"), label);
    return h("div", { class: "frow2" }, lb, control);
  };

  // تصنيف الوثيقة من جدول التصنيفات؛ تُخفى التصنيفات التي تفوق درجة سريتها صلاحية المستخدم
  const classes = h("div", { class: "classes", role: "group", "aria-label": "تصنيف الوثيقة" },
    state.cls.filter((c) => c.conf_level <= state.user.clearance).map((c) => radio("priority", c.key, c.label, c.key === "normal")));

  // الموظفون الذين يحق لهم الرد على الخطاب الوارد (لا يظهر إلا عند اختيار «تحتاج إلى رد: نعم»)
  const everyone = state.users.filter((u) => u.active !== false);
  const replyPicker = peoplePicker(everyone, "إن لم تضف أحدًا فيحق الرد لكل من يطّلع على المراسلة.");
  const assignPicker = peoplePicker(everyone.filter((u) => u.id !== state.user.id), "يمكنك إسناد المراسلة لأكثر من موظف.");
  const replyBox = replyPicker.node;
  // قائمة توجيهات الإسناد (مربعات اختيار على عمودين كالقسيمة الورقية) + «أخرى» بنص حر
  const otherCb = h("input", { type: "checkbox", name: "dir_other_cb", onchange: () => { otherTxt.disabled = !otherCb.checked; if (otherCb.checked) otherTxt.focus(); } });
  const otherTxt = h("input", { type: "text", name: "directive_other", maxlength: 200, disabled: true, placeholder: "اكتب التوجيه…", "aria-label": "توجيه آخر", class: "dir-other" });
  const dirBox = h("div", { class: "dirgrid", role: "group", "aria-label": "توجيه الإسناد" },
    state.directives.map((d) => h("label", { class: "opt" }, h("input", { type: "checkbox", name: "directive", value: d.id }), d.name)),
    h("label", { class: "opt other" }, otherCb, "أخرى"), otherTxt);
  // الوحدات المسؤولة: واحدة أو أكثر، وتبدأ بوحدة المسجِّل
  const unitItems = activeDepts().map((d) => ({ id: d.id, full_name: d.name }));
  const unitPicker = peoplePicker(unitItems, "يمكنك اختيار أكثر من وحدة مسؤولة؛ الأولى هي الأساسية.", "ابحث عن وحدة…",
    unitItems.filter((d) => d.id === state.user.department_id));
  const replyRow = row(h("span", {}, "المخوّلون بالرد"), replyBox);
  replyRow.hidden = true; // يظهر عند اختيار «تحتاج إلى رد: نعم»

  // اقتراحات ذكية (تعلّم من الأرشيف + قواعد التوجيه) تظهر أثناء الكتابة
  const val = (n) => (form.elements[n] ? form.elements[n].value : "");
  const sug = suggestPanel(() => ({ subject: val("subject"), summary: val("summary"), party: val("party"), direction: dir }), {
    type: (id) => (form.elements.type_id.value = id), folder: (id) => (form.elements.folder_id.value = id),
    priority: (key) => { const r = form.querySelector(`input[name=priority][value="${key}"]`); if (r) r.checked = true; },
    unit: (u) => unitPicker.add({ id: u.id, full_name: u.name }), assignee: (u) => assignPicker.add({ id: u.id, full_name: u.name }),
  });
  // كشف التكرار قبل القيد: نفس رقم مرجع الجهة أو موضوع مطابق، مع اقتراح الربط بعد الحفظ
  const dupBox = h("div", { class: "dupbox", hidden: true, "aria-live": "polite" });
  let dupTimer = null, dupLast = "";
  const dupLinks = new Set();
  const dupCheck = () => {
    clearTimeout(dupTimer);
    dupTimer = setTimeout(async () => {
      const inp = { party: val("party"), party_ref: val("party_ref"), subject: val("subject") };
      const key = JSON.stringify(inp);
      if (key === dupLast || (!inp.party_ref && inp.subject.length < 6)) return;
      dupLast = key;
      try {
        const rows = await api("/duplicates?" + new URLSearchParams(inp));
        dupLinks.clear();
        dupBox.replaceChildren(h("strong", {}, "⚠ قد تكون المراسلة مسجّلة سابقًا:"), ...rows.map((d) => h("div", { class: "dup" },
          h("a", { href: "#/c/" + d.id, target: "_blank", class: "ltr" }, d.ref_no), ` — ${d.subject} `, h("span", { class: "small muted" }, `(${d.reason}${d.score < 1 ? " " + Math.round(d.score * 100) + "٪" : ""})`),
          h("label", { class: "opt" }, h("input", { type: "checkbox", onchange: (e) => (e.target.checked ? dupLinks.add(d.ref_no) : dupLinks.delete(d.ref_no)) }), "اربطها بهذه المعاملة بعد الحفظ"))));
        dupBox.hidden = !rows.length;
      } catch { dupBox.hidden = true; }
    }, 900);
  };
  let asDraft = false;
  // قوالب الخطابات: تملأ الموضوع والرسالة بمتغيرات الأصل والجهة
  const tplPick = templates.length ? sel("tpl", { "": "— بدون قالب —", ...Object.fromEntries(templates.map((t) => [t.id, t.name])) }, "", { "aria-label": "قالب الخطاب", onchange: (e) => {
    const t = templates.find((x) => x.id === Number(e.target.value)); if (!t) return;
    const vars = { party: val("party") || (parentC && parentC.party) || "", date: today(), ref_orig: (parentC && parentC.ref_no) || "", subject_orig: (parentC && parentC.subject) || val("subject") };
    if (t.subject_tpl) form.elements.subject.value = fillTemplate(t.subject_tpl, vars);
    if (t.body_tpl) form.elements.summary.value = fillTemplate(t.body_tpl, vars);
    if (t.type_id) form.elements.type_id.value = t.type_id;
    sug.trigger();
  } }) : null;
  const wfPick = workflows.length ? sel("wf", { "": "— بدون مسار عمل —", ...Object.fromEntries(workflows.map((w) => [w.id, w.name])) }, "", { "aria-label": "مسار العمل" }) : null;

  const form = h("form", { class: "card fm", onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    const body = { direction: dir, scope: form.elements.scope.value, priority: form.elements.priority.value, needs_reply: Number(form.elements.needs_reply.value) };
    if (body.needs_reply) body.reply_users = replyPicker.ids();
    if (assignPicker.ids().length) body.assigned_to = assignPicker.ids();
    if (unitPicker.ids().length) body.department_id = unitPicker.ids();
    const dirs = [...form.querySelectorAll("input[name=directive]:checked")].map((x) => Number(x.value));
    if (dirs.length) body.directives = dirs;
    if (otherCb.checked && otherTxt.value.trim()) body.directive_other = otherTxt.value.trim();
    for (const n of ["subject", "party", "summary", "party_ref", "doc_date", "due_date", "type_id", "folder_id",
      "deliverer_name", "deliverer_phone", "deliverer_email", "delivery_notes"]) {
      const el = form.elements[n]; if (el && el.value !== "") body[n] = el.value;
    }
    if (parent) body.parent_id = Number(parent);
    if (asDraft) body.draft = true;
    asDraft = false;
    const tags = tagsBox.values();
    if (tags.length) body.tags = tags;
    try {
      const r = await api("/correspondences", { method: "POST", body });
      const file = form.file.files[0];
      if (file) await upload(r.id, file);
      for (const ref of dupLinks) { try { await api(`/correspondences/${r.id}/related`, { method: "POST", body: { ref_no: ref } }); } catch { /* الربط اختياري */ } }
      if (wfPick && wfPick.value && !body.draft) {
        try { await api(`/correspondences/${r.id}/workflow`, { method: "POST", body: { workflow_id: Number(wfPick.value) } }); }
        catch (we) { toast("سُجّلت المراسلة لكن تعذّر بدء المسار: " + we.message, true); }
      }
      toast("تم التسجيل برقم " + r.ref_no); location.hash = "#/c/" + r.id;
    } catch (ex) { err.textContent = ex.message; }
  } },
    row(h("span", {}, "الاتجاه"), h("div", { class: "classes" },
      radio("dir", "incoming", "وارد", dir === "incoming", () => { dir = "incoming"; syncDir(); }),
      radio("dir", "outgoing", "صادر", dir === "outgoing", () => { dir = "outgoing"; syncDir(); }))),
    row(h("span", {}, "نوع البريد"), h("div", { class: "classes" },
      canExternal ? radio("scope", "external", "خارجي", (q.get("scope") || "external") === "external") : null,
      radio("scope", "internal", "داخلي", !canExternal || q.get("scope") === "internal"))),
    tplPick ? row("قالب الخطاب", tplPick) : null,
    row("الموضوع", h("input", { name: "subject", required: true, maxlength: 300, placeholder: "الموضوع", value: q.get("subject") || "", oninput: () => { sug.trigger(); dupCheck(); } }), true),
    row(dl("واردة من", "صادرة إلى"), h("input", { name: "party", required: true, maxlength: 200, placeholder: "اسم الجهة / الإدارة", value: q.get("party") || "", oninput: () => { sug.trigger(); dupCheck(); } }), true),
    row("الرسالة", h("textarea", { name: "summary", maxlength: 5000, placeholder: "الرسالة", oninput: () => { sug.trigger(); dupCheck(); } })),
    row(h("span", {}, "اقتراحات ذكية"), sug.node),
    row(h("span", {}, "تنبيه التكرار"), dupBox),
    row("الرقم المتسلسل للمراسلة", h("input", { name: "party_ref", maxlength: 100, placeholder: "حروف وأرقام", oninput: () => dupCheck() })),
    row("المرفق", h("input", { type: "file", name: "file" })),
    row(h("span", {}, "تصنيف الوثيقة"), classes),
    row("نوع المعاملة", sel("type_id", { "": "— اختر —", ...Object.fromEntries(state.types.map((t) => [t.id, t.name])) }, "")),
    row("المجلد", sel("folder_id", { "": "— بدون مجلد —", ...folderOptions() }, "")),
    row("الوسوم", tagsBox.node),
    row(h("span", {}, "تحتاج إلى رد"), h("div", { class: "classes" },
      radio("needs_reply", "1", "نعم", false, () => (replyRow.hidden = false)), radio("needs_reply", "0", "لا", true, () => (replyRow.hidden = true)))),
    replyRow,
    row("الوحدة المسؤولة", unitPicker.node),
    row("إسناد إلى", assignPicker.node),
    row(h("span", {}, "توجيه الإسناد"), dirBox),
    wfPick ? row("مسار العمل", wfPick) : null,
    h("details", { class: "more2" },
      h("summary", {}, dl("معلومات إضافية عن حالة التوريد (المزيد)", "معلومات إضافية عن حالة الإصدار (المزيد)", "")),
      row(dl("اسم مسلّم المراسلة", "اسم مستلم المراسلة"), h("input", { name: "deliverer_name", maxlength: 100, placeholder: "الاسم" })),
      row("رقم الجوال", h("input", { name: "deliverer_phone", maxlength: 30, type: "tel", dir: "ltr", placeholder: "رقم الجوال" })),
      row(dl("واردة عبر البريد الإلكتروني", "صادرة عبر البريد الإلكتروني"), h("input", { name: "deliverer_email", maxlength: 150, type: "email", dir: "ltr", placeholder: "البريد الإلكتروني" })),
      row("تاريخ المراسلة", h("input", { type: "date", name: "doc_date" })),
      row("تاريخ الاستحقاق", h("input", { type: "date", name: "due_date" })),
      row("ملاحظات التسليم", h("textarea", { name: "delivery_notes", maxlength: 1000, placeholder: "ملاحظات التسليم" }))),
    err, h("div", { class: "fm-actions" }, h("button", { class: "btn" }, "حفظ"), h("button", { type: "button", class: "btn ghost", onclick: () => { asDraft = true; form.requestSubmit(); } }, "حفظ كمسودة"), h("a", { class: "btn ghost", href: "#/list" }, "إلغاء")));
  syncDir();
  return [title, form];
}
let fieldSeq = 0;
function f(label, input, req, cls) {
  input.id = input.id || "fld" + ++fieldSeq;
  return h("div", { class: "field " + (cls || "") }, h("label", { class: req ? "req" : "", for: input.id }, label), input);
}

async function upload(cid, file) {
  await api(`/correspondences/${cid}/attachments`, { method: "POST", body: file, headers: { "X-Filename": encodeURIComponent(file.name) } });
}

// ───────── تفاصيل المراسلة ─────────
async function detailView(id) {
  const c = await api("/correspondences/" + id);
  const extras = await detailExtras(c); // وسوم، مسار عمل، توقيع، احتجاز (ext.js)
  const dl = async (a) => {
    try {
      const res = await api("/attachments/" + a.id, { raw: true });
      const url = URL.createObjectURL(await res.blob());
      h("a", { href: url, download: a.filename }).click(); setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e) { toast(e.message, true); }
  };
  const m = (k, v) => [h("dt", {}, k), h("dd", {}, v)];
  const overdue = c.due_date && c.due_date < today() && !["closed", "archived"].includes(c.status);

  const statusSel = sel("status", L.status, c.status);
  const prioSel = sel("priority", L.priority, c.priority);
  const typeSel = sel("type_id", { "": "— نوع المعاملة —", ...Object.fromEntries(state.types.map((t) => [t.id, t.name])) }, c.type_id || "", { "aria-label": "نوع المعاملة" });
  const folderSel = sel("folder_id", { "": "— المجلد —", ...folderOptions() }, c.folder_id || "", { "aria-label": "المجلد" });
  const noteIn = h("input", { placeholder: "ملاحظة (اختياري)" });
  const manage = c.can_manage ? h("div", { class: "card" }, h("h3", {}, "إجراءات"),
    h("div", { class: "row" }, statusSel, prioSel, typeSel, folderSel, noteIn, h("button", { class: "btn", onclick: async () => {
      try { await api("/correspondences/" + id, { method: "PUT", body: { status: statusSel.value, priority: prioSel.value, type_id: typeSel.value || null, folder_id: folderSel.value || null, note: noteIn.value } }); toast("تم التحديث"); render(); }
      catch (e) { toast(e.message, true); }
    } }, "تحديث"))) : null;

  const toSel = sel("to_user", Object.fromEntries(state.users.map((u) => [u.id, `${u.full_name} — ${L.role[u.role]}`])), "");
  const instr = h("input", { placeholder: "التوجيه / المطلوب" });
  const due = h("input", { type: "date" });
  const transferBox = c.can_manage && !["closed", "archived"].includes(c.status) ? h("div", { class: "card" }, h("h3", {}, "إحالة جديدة وتوجيه"),
    h("div", { class: "row" }, toSel, instr, due, h("button", { class: "btn", onclick: async () => {
      try { await api(`/correspondences/${id}/transfers`, { method: "POST", body: { to_user: toSel.value, instruction: instr.value, due_date: due.value } }); toast("تمت الإحالة"); render(); }
      catch (e) { toast(e.message, true); }
    } }, "إحالة"))) : null;

  // ربط الوارد بالصادر ومتابعة الرد
  const refIn = h("input", { placeholder: "رقم قيد الصادر، مثل EXT-OUT-2026-00001", dir: "ltr", "aria-label": "رقم قيد الصادر" });
  const replyCard = c.direction === "incoming" ? h("div", { class: "card" }, h("h3", {}, "الرد على هذه المراسلة"),
    h("div", { class: "row" }, c.replies.length ? pill("closed", `✔ تم الرد (${c.replies.length})`) : c.needs_reply ? pill("in_progress", "⏳ بانتظار الرد") : pill("archived", "لا تحتاج رد")),
    c.replies.length ? h("ul", { class: "tl" }, c.replies.map((r) => h("li", {}, h("a", { href: "#/c/" + r.id, class: "ltr" }, r.ref_no), " — ", r.subject, h("span", { class: "small muted" }, ` (${fmtDate(r.doc_date)})`)))) : null,
    c.reply_users.length ? h("p", { class: "small" }, h("strong", {}, "المخوّلون بالرد: "), c.reply_users.map((u, i) => [i ? "، " : "", u.full_name, u.status === "done" ? " ✔" : " ⏳"])) : null,
    c.can_reply ? h("div", { class: "row" }, refIn, h("button", { class: "btn sm", onclick: async () => {
      if (!refIn.value.trim()) return toast("اكتب رقم قيد المراسلة الصادرة", true);
      try { await api(`/correspondences/${id}/link`, { method: "POST", body: { ref_no: refIn.value.trim() } }); toast("تم ربط الرد"); render(); }
      catch (e) { toast(e.message, true); }
    } }, "ربط صادر قائم كرد")) : null) : null;

  const fileIn = h("input", { type: "file" });
  return [
    h("div", { class: "row" }, h("h2", {}, c.subject), statusPill(c.status), c.priority !== "normal" ? prioPill(c.priority) : null,
      c.conf_level ? pill("conf" + c.conf_level, L.conf[c.conf_level]) : null, overdue ? pill("urgent", "متأخرة") : null),
    h("div", { class: "card" }, h("dl", { class: "meta" },
      m("رقم القيد", h("span", { class: "ltr" }, c.ref_no)), m("النوع", typeLabel(c)),
      m("الجهة", c.party), m("رقم مرجع الجهة", c.party_ref || "—"),
      m("تاريخ المراسلة", [fmtDate(c.doc_date), h("div", { class: "small muted" }, hijri(c.doc_date))]),
      m("الاستحقاق", c.due_date || "—"), c.sla_due_at ? m("مهلة SLA", `${c.sla_hours} ساعة عمل — حتى ${c.sla_due_at.slice(0, 16)}`) : null, m(c.units.length > 1 ? "الوحدات المسؤولة" : "الوحدة المسؤولة", c.units.length ? c.units.join("، ") : c.department_name || "—"), c.party_dept_name ? m("الإدارة المعنية", c.party_dept_name) : null,
      m("المُسنَد إليهم", c.assignees.length ? c.assignees.join("، ") : "—"),
      c.directives.length || c.directive_other ? m("توجيه الإسناد", [...c.directives, c.directive_other].filter(Boolean).join("، ")) : null, m("نوع المعاملة", c.type_name || c.category || "—"), m("المجلد", c.folder_name || "—"),
      m("تحتاج إلى رد", c.needs_reply ? "نعم" : "لا"),
      c.deliverer_name ? m(c.direction === "incoming" ? "مسلّم المراسلة" : "مستلم المراسلة", c.deliverer_name) : null,
      c.deliverer_phone ? m("رقم الجوال", h("span", { class: "ltr" }, c.deliverer_phone)) : null,
      c.deliverer_email ? m("البريد الإلكتروني", h("span", { class: "ltr" }, c.deliverer_email)) : null,
      c.delivery_notes ? m("ملاحظات التسليم", c.delivery_notes) : null,
      m("سُجّلت بواسطة", `${c.creator_name} — ${fmtDT(c.created_at)}`),
      c.parent ? m("رد على", h("a", { href: "#/c/" + c.parent.id }, c.parent.ref_no)) : null),
      c.summary ? h("p", {}, h("strong", {}, "الملخص: "), c.summary) : null,
      h("div", { class: "row" }, detailTools(c), c.can_reply ? h("a", { class: "btn ghost sm", href: `#/new?parent=${c.id}&scope=${c.scope}&direction=${c.direction === "incoming" ? "outgoing" : "incoming"}&party=${encodeURIComponent(c.party)}&subject=${encodeURIComponent("رد: " + c.subject)}` }, "تسجيل رد") : null,
        h("button", { class: "btn ghost sm", onclick: () => window.print() }, "طباعة"))),
    replyCard, manage, transferBox, ...extras,
    h("div", { class: "card" }, h("h3", {}, `المرفقات (${c.attachments.length})`),
      c.attachments.length ? h("table", {}, h("tbody", {}, c.attachments.map((a) => h("tr", {},
        h("td", {}, h("a", { href: "#", onclick: (e) => { e.preventDefault(); dl(a); } }, a.filename), " ",
          previewable(a.filename) ? h("button", { class: "btn ghost sm", onclick: () => previewAttachment(a, c) }, "👁 معاينة") : null), h("td", {}, sizeStr(a.size)),
        h("td", { class: "small muted" }, a.uploader, " — ", fmtDT(a.created_at)),
        h("td", { class: "small muted ltr", title: "بصمة SHA-256 للتحقق من سلامة الملف" }, a.sha256.slice(0, 16) + "…"))))) : h("div", { class: "muted" }, "لا مرفقات"),
      c.status !== "archived" ? h("div", { class: "row" }, fileIn, h("button", { class: "btn sm", onclick: async () => {
        if (!fileIn.files[0]) return toast("اختر ملفًا أولًا", true);
        try { await upload(id, fileIn.files[0]); toast("تم رفع الملف"); render(); } catch (e) { toast(e.message, true); }
      } }, "رفع")) : null),
    h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h3", {}, "الإحالات والتوجيه"), c.transfers.length ? h("ul", { class: "tl" }, c.transfers.map((t) => h("li", {},
        h("strong", {}, t.from_name), " ← ", h("strong", {}, t.to_name), " ", pill(t.status === "done" ? "closed" : t.status === "pending" ? "in_progress" : "archived", { done: "منفّذة", pending: "معلّقة", superseded: "أُعيدت إحالتها" }[t.status]),
        h("div", { class: "small muted" }, fmtDT(t.created_at), t.instruction ? " — " + t.instruction : ""),
        t.note ? h("div", { class: "small" }, "ملاحظة التنفيذ: " + t.note) : null))) : h("div", { class: "muted" }, "لم تُحَل بعد")),
      h("div", { class: "card" }, h("h3", {}, "سجل الإجراءات"), h("ul", { class: "tl" }, c.timeline.map((t) => h("li", {},
        h("strong", {}, L.action[t.action] || t.action), " — ", t.username, h("div", { class: "small muted" }, fmtDT(t.at), t.detail ? " — " + t.detail : "")))))),
    c.replies.length ? h("div", { class: "card" }, h("h3", {}, "الردود والمراسلات المرتبطة"), corrTable(c.replies.map((r) => ({ ...r, party: "" })), true)) : null,
  ];
}

// ───────── الإدارة ─────────
async function auditView() {
  const rows = await api("/admin/audit?limit=300");
  return [h("h2", {}, "سجل الإجراءات"), h("p", { class: "muted small" }, "كل العمليات (الدخول، التسجيل، الإحالة، التنزيل، التصدير…) مع المستخدم وعنوان الشبكة. السجل للقراءة فقط."),
    h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["الوقت", "المستخدم", "العملية", "التفاصيل", "IP"].map((t) => h("th", {}, t)))),
      h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "ltr small" }, fmtDT(r.at)), h("td", {}, r.username || "—"), h("td", {}, L.action[r.action] || r.action),
        h("td", { class: "small" }, r.entity_id ? h("a", { href: "#/c/" + r.entity_id }, r.detail || "") : r.detail || ""), h("td", { class: "ltr small" }, r.ip))))))];
}

async function integrationView() {
  const [keys, hooks, users] = await Promise.all([api("/admin/apikeys"), api("/admin/webhooks"), api("/users")]);
  const shown = h("div");
  const kName = h("input", { placeholder: "اسم النظام (مثال: نظام الموارد البشرية)" });
  const kUser = sel("u", Object.fromEntries(users.filter((u) => u.active).map((u) => [u.id, `${u.full_name} (${L.role[u.role]})`])), "");
  const hUrl = h("input", { placeholder: "https://example.org/hooks/acp", dir: "ltr" });
  return [h("h2", {}, "التكامل مع الأنظمة الحالية"),
    h("div", { class: "card" }, h("p", {}, "للربط مع أنظمة أخرى (الموارد البشرية، المالية، البوابة الإلكترونية، البريد…): أنشئ مفتاح API يعمل بصلاحيات مستخدم خدمي، وأرسل الترويسة ",
      h("code", { class: "ltr" }, "X-API-Key"), "، وللإشعارات الفورية سجّل Webhook. التوثيق الكامل في ملف README.")),
    shown,
    h("div", { class: "card" }, h("h3", {}, "مفاتيح API"), h("div", { class: "row" }, kName, kUser, h("button", { class: "btn", onclick: async () => {
      try { const r = await api("/admin/apikeys", { method: "POST", body: { name: kName.value, user_id: kUser.value } });
        shown.replaceChildren(h("div", { class: "alert" }, "انسخ المفتاح الآن — لن يظهر مرة أخرى:", h("div", { class: "secret" }, r.key))); toast("تم إنشاء المفتاح"); }
      catch (e) { toast(e.message, true); } } }, "إنشاء مفتاح")),
    keys.length ? h("table", {}, h("tbody", {}, keys.map((k) => h("tr", {}, h("td", {}, k.name), h("td", { class: "ltr" }, k.prefix + "…"), h("td", {}, k.user_name),
      h("td", {}, k.active ? pill("closed", "فعّال") : pill("archived", "ملغى")), h("td", {}, k.active ? h("button", { class: "btn danger sm", onclick: async () => { await api("/admin/apikeys/" + k.id, { method: "DELETE" }); render(); } }, "إلغاء") : null))))) : null),
    h("div", { class: "card" }, h("h3", {}, "Webhooks (إشعارات الأحداث)"), h("div", { class: "row" }, hUrl, h("button", { class: "btn", onclick: async () => {
      try { const r = await api("/admin/webhooks", { method: "POST", body: { url: hUrl.value } });
        shown.replaceChildren(h("div", { class: "alert" }, "السر المشترك للتحقق من التوقيع (HMAC-SHA256) — يظهر مرة واحدة:", h("div", { class: "secret" }, r.secret))); toast("تمت الإضافة"); }
      catch (e) { toast(e.message, true); } } }, "إضافة")),
    hooks.length ? h("table", {}, h("tbody", {}, hooks.map((w) => h("tr", {}, h("td", { class: "ltr" }, w.url), h("td", { class: "small muted ltr" }, w.events),
      h("td", {}, h("button", { class: "btn danger sm", onclick: async () => { await api("/admin/webhooks/" + w.id, { method: "DELETE" }); render(); } }, "حذف"))))) ) : null)];
}

// ───────── التوجيه ─────────
async function loadRefs() {
  [state.depts, state.users, state.cls, state.folders, state.types, state.directives] = await Promise.all([
    api("/departments"), api("/users"), api("/classifications"), api("/lookups/folders"), api("/lookups/types"), api("/lookups/directives")]);
  state.clsMap = Object.fromEntries(state.cls.map((c) => [c.key, c]));
  // الأولويات المعطّلة تبقى معروضة للمراسلات القديمة
  if (state.user.role === "admin") {
    const all = await api("/classifications?all=1");
    all.forEach((c) => (state.clsMap[c.key] = c));
  }
  L.priority = Object.fromEntries(Object.values(state.clsMap).sort((a, b) => a.sort - b.sort).map((c) => [c.key, c.label]));
  [state.filters, state.prefs] = await Promise.all([api("/me/filters"), api("/me/prefs")]);
  applyTheme(state.prefs.theme);
}

// يُحفظ الفلتر الحالي (استعلام الصفحة) باسم يختاره المستخدم، ويظهر في القائمة الجانبية
async function saveCurrentFilter(kind) {
  const query = (location.hash.split("?")[1] || "").replace(/&?page=\d+/, "");
  const name = prompt("اسم الفلتر المحفوظ:");
  if (!name || !name.trim()) return;
  try { await api("/me/filters", { method: "POST", body: { name: name.trim(), kind, query } }); toast("حُفظ الفلتر"); await loadRefs(); render(); }
  catch (e) { toast(e.message, true); }
}

async function boot() {
  try {
    state.user = await api("/me"); // يعمل أيضًا دون رمز عند الدخول الموحّد (SSO) عبر الوسيط الموثوق
    if (!state.user.must_change) await loadRefs();
    if (!boot.polling) { boot.polling = true; setInterval(pollNotifications, 30000); pollNotifications(); startRealtime(); }
  } catch { state.user = null; state.token = null; sessionStorage.removeItem("acp_token"); }
  render();
}

async function render() {
  const app = document.getElementById("app");
  if (!state.user) return app.replaceChildren(loginView());
  if (state.user.must_change) return app.replaceChildren(changePasswordView(true));
  const [path, qs] = (location.hash || "#/dashboard").slice(1).split("?");
  const q = new URLSearchParams(qs || "");
  const my = ++render.n;
  try {
    let view, tasks = 0;
    if (path === "/dashboard") view = await dashboardView();
    else if (path === "/list") view = await listView(q);
    else if (path === "/tasks") view = await tasksView();
    else if (path === "/new") view = await newView(q);
    else if (EXT_ROUTES[path]) view = await EXT_ROUTES[path](q);
    else if (Object.keys(EXT_PREFIX).some((p) => path.startsWith(p))) { const pre = Object.keys(EXT_PREFIX).find((p) => path.startsWith(p)); view = await EXT_PREFIX[pre](Number(path.slice(pre.length)), q); }
    else if (path === "/password") view = changePasswordView(false);
    else if (path.startsWith("/c/")) view = await detailView(Number(path.slice(3)));
    else if (path === "/reports" && (isStaff() || can("export"))) view = await reportsView(q);
    else if (path.startsWith("/admin/") && state.user.role === "admin" && ADMIN_VIEWS[path.slice(7)]) view = await ADMIN_VIEWS[path.slice(7)]();
    else if (path === "/admin/audit" && state.user.role === "admin") view = await auditView();
    else if (path === "/admin/integration" && state.user.role === "admin") view = await integrationView();
    else view = [h("h2", {}, "الصفحة غير موجودة")];
    if (my !== render.n) return;
    try { tasks = (await api("/dashboard")).my_tasks; } catch {}
    app.replaceChildren(shell(view, tasks));
  } catch (e) {
    if (my !== render.n) return;
    app.replaceChildren(shell([h("h2", {}, "تعذّر عرض الصفحة"), h("div", { class: "alert" }, e.message)], 0));
  }
}
render.n = 0;

window.addEventListener("hashchange", render);
boot();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { /* التثبيت اختياري */ });
