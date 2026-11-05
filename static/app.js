"use strict";
// منصة الاتصالات الإدارية — واجهة المستخدم (JavaScript خالص، بدون مكتبات خارجية).
// كل النصوص تُدرج عبر textContent لمنع هجمات XSS.

const L = {
  status: { new: "جديدة", in_progress: "قيد المعالجة", referred: "محالة", closed: "منجزة", archived: "مؤرشفة" },
  priority: { normal: "عادية", high: "مهمة", urgent: "عاجلة" },
  conf: ["عادي", "سري", "سري للغاية"],
  role: { admin: "مدير النظام", manager: "مدير إدارة", clerk: "مكتب الصادر والوارد", employee: "موظف" },
  scope: { external: "خارجي", internal: "داخلي" },
  direction: { incoming: "وارد", outgoing: "صادر" },
  action: { create: "تسجيل المراسلة", update: "تحديث", transfer: "إحالة", transfer_done: "تنفيذ إحالة", attach: "إضافة مرفق",
    download: "تنزيل مرفق", login: "دخول", logout: "خروج", login_failed: "فشل دخول", export: "تصدير", password_changed: "تغيير كلمة المرور",
    user_create: "إنشاء مستخدم", user_update: "تعديل مستخدم", dept_create: "إنشاء إدارة", apikey_create: "إنشاء مفتاح API",
    apikey_revoke: "إلغاء مفتاح API", webhook_create: "إنشاء Webhook", webhook_delete: "حذف Webhook", integrity_failure: "فشل سلامة ملف" },
};

const state = { token: sessionStorage.getItem("acp_token"), user: null, depts: [], users: [] };

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

function logoutLocal() {
  state.token = null; state.user = null;
  sessionStorage.removeItem("acp_token");
  render();
}

// ───────── الإطار العام ─────────
const NAV = [
  ["#/dashboard", "لوحة المعلومات"],
  ["#/tasks", "معاملاتي"],
  ["#/new", "تسجيل مراسلة جديدة"],
  ["sep", "المراسلات"],
  ["#/list?scope=external&direction=incoming", "الوارد الخارجي"],
  ["#/list?scope=external&direction=outgoing", "الصادر الخارجي"],
  ["#/list?scope=internal&direction=incoming", "الوارد الداخلي"],
  ["#/list?scope=internal&direction=outgoing", "الصادر الداخلي"],
  ["#/list", "كل المراسلات"],
];
const ADMIN_NAV = [["sep", "الإدارة"], ["#/admin/users", "المستخدمون والإدارات"], ["#/admin/audit", "سجل التدقيق"], ["#/admin/integration", "التكامل (API)"]];

function shell(content, taskCount) {
  const cur = location.hash || "#/dashboard";
  const items = [...NAV, ...(state.user.role === "admin" ? ADMIN_NAV : [])].map(([href, label]) =>
    href === "sep" ? h("div", { class: "sep" }, label)
      : h("a", { href, class: cur === href || (!href.includes("?") && cur.split("?")[0] === href && !cur.includes("?scope=")) ? "active" : "" },
        label, href === "#/tasks" && taskCount ? h("span", { class: "badge-n" }, taskCount) : null));
  return h("div", { class: "shell" },
    h("nav", { class: "side", "aria-label": "القائمة الرئيسية" },
      h("h1", {}, "منصة الاتصالات الإدارية"), items,
      h("div", { class: "who" }, h("div", {}, state.user.full_name), h("div", { class: "small" }, L.role[state.user.role]),
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
      const r = await api("/login", { method: "POST", body: { username: form.u.value.trim(), password: form.p.value } });
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
  const typeRows = ["external", "internal"].flatMap((s) => ["incoming", "outgoing"].map((dir) => {
    const n = (d.by_type.find((t) => t.scope === s && t.direction === dir) || {}).n || 0;
    return stat(n, `${L.direction[dir]} ${L.scope[s]}`, `#/list?scope=${s}&direction=${dir}`);
  }));
  return [h("h2", {}, "لوحة المعلومات"),
    h("div", { class: "grid g4" }, stat(d.total, "إجمالي المراسلات", "#/list"), stat(d.my_tasks, "إحالات بانتظارك", "#/tasks"),
      stat(d.overdue, "متأخرة عن موعدها", "#/list?overdue=1", true), stat(d.urgent_open, "عاجلة مفتوحة", "#/list?priority=urgent")),
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
      h("td", {}, h("a", { href: "#/c/" + c.id }, c.subject), " ", c.priority !== "normal" ? pill(c.priority, L.priority[c.priority]) : null, c.conf_level ? pill("conf" + c.conf_level, L.conf[c.conf_level]) : null),
      compact ? null : h("td", {}, c.party),
      h("td", { class: "ltr" }, fmtDate(c.doc_date)),
      h("td", {}, statusPill(c.status)),
      compact ? null : h("td", {}, c.assignee_name || "—"))))));
}

async function listView(q) {
  const filters = ["scope", "direction", "status", "priority", "dept", "q", "from", "to", "overdue", "mine"];
  const params = new URLSearchParams();
  filters.forEach((k) => q.get(k) && params.set(k, q.get(k)));
  const page = Number(q.get("page") || 1);
  params.set("page", page);
  const data = await api("/correspondences?" + params);
  const form = h("form", { class: "card toolbar", onsubmit: (e) => {
    e.preventDefault();
    const p = new URLSearchParams();
    for (const k of filters) { const el = form.elements[k]; if (el && el.value) p.set(k, el.value); }
    if (q.get("overdue")) p.set("overdue", "1");
    if (q.get("mine")) p.set("mine", "1");
    location.hash = "#/list?" + p;
  } },
    h("div", {}, h("label", {}, "بحث (الموضوع، الرقم، الجهة)"), h("input", { name: "q", value: q.get("q") || "" })),
    h("div", {}, h("label", {}, "النطاق"), sel("scope", { "": "الكل", ...L.scope }, q.get("scope"))),
    h("div", {}, h("label", {}, "الاتجاه"), sel("direction", { "": "الكل", ...L.direction }, q.get("direction"))),
    h("div", {}, h("label", {}, "الحالة"), sel("status", { "": "الكل", ...L.status }, q.get("status"))),
    h("div", {}, h("label", {}, "الأولوية"), sel("priority", { "": "الكل", ...L.priority }, q.get("priority"))),
    h("div", {}, h("label", {}, "الإدارة"), sel("dept", { "": "الكل", ...Object.fromEntries(state.depts.map((d) => [d.id, d.name])) }, q.get("dept"))),
    h("div", {}, h("label", {}, "من تاريخ"), h("input", { type: "date", name: "from", value: q.get("from") || "" })),
    h("div", {}, h("label", {}, "إلى تاريخ"), h("input", { type: "date", name: "to", value: q.get("to") || "" })),
    h("div", { class: "fixed row" }, h("button", { class: "btn" }, "بحث"),
      isStaff() ? h("button", { type: "button", class: "btn ghost", onclick: () => exportCsv(params) }, "تصدير CSV") : null));
  const pages = Math.max(1, Math.ceil(data.total / data.size));
  const go = (p) => { const n = new URLSearchParams(params); n.set("page", p); location.hash = "#/list?" + n; };
  return [h("h2", {}, "المراسلات", h("span", { class: "muted small" }, ` (${data.total})`)), form, corrTable(data.items),
    h("div", { class: "pager" }, h("button", { class: "btn ghost sm", disabled: page <= 1, onclick: () => go(page - 1) }, "السابق"),
      h("span", {}, `صفحة ${page} من ${pages}`), h("button", { class: "btn ghost sm", disabled: page >= pages, onclick: () => go(page + 1) }, "التالي"))];
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
  return [h("h2", {}, "معاملاتي (الإحالات بانتظار إجرائي)"),
    tasks.length === 0 ? h("div", { class: "card empty" }, "لا توجد إحالات معلّقة 🎉") :
      tasks.map((t) => {
        const note = h("input", { placeholder: "ملاحظة التنفيذ (اختياري)" });
        return h("div", { class: "card" },
          h("div", { class: "row" }, h("a", { href: "#/c/" + t.id, class: "ltr" }, t.ref_no), h("strong", {}, t.subject),
            t.priority !== "normal" ? pill(t.priority, L.priority[t.priority]) : null, t.conf_level ? pill("conf" + t.conf_level, L.conf[t.conf_level]) : null),
          h("p", { class: "muted small" }, `من: ${t.from_name} — ${fmtDT(t.transferred_at)}`, t.transfer_due ? ` — الاستحقاق: ${t.transfer_due}` : ""),
          t.instruction ? h("p", {}, "التوجيه: ", t.instruction) : null,
          h("div", { class: "row" }, note, h("button", { class: "btn", onclick: async () => {
            try { await api(`/transfers/${t.transfer_id}/complete`, { method: "POST", body: { note: note.value } }); toast("تم تسجيل التنفيذ"); render(); }
            catch (e) { toast(e.message, true); }
          } }, "تم الاطلاع / التنفيذ")));
      })];
}

// ───────── مراسلة جديدة ─────────
function newView(q) {
  const parent = q.get("parent");
  const err = h("div", { class: "err", role: "alert" });
  const canExternal = state.user.role !== "employee";
  const scopes = canExternal ? { ...L.scope } : { internal: "داخلي" };
  const form = h("form", { class: "card", onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    const body = {};
    for (const el of form.elements) if (el.name && el.value !== "") body[el.name] = el.value;
    ["conf_level"].forEach((k) => body[k] != null && (body[k] = Number(body[k])));
    if (parent) body.parent_id = Number(parent);
    try {
      const r = await api("/correspondences", { method: "POST", body });
      const file = form.file.files[0];
      if (file) await upload(r.id, file);
      toast("تم التسجيل برقم " + r.ref_no); location.hash = "#/c/" + r.id;
    } catch (ex) { err.textContent = ex.message; }
  } },
    h("div", { class: "form-grid" },
      f("النطاق", sel("scope", scopes, q.get("scope") || (canExternal ? "external" : "internal"), { required: true }), true),
      f("الاتجاه", sel("direction", L.direction, q.get("direction") || "incoming", { required: true }), true),
      f("الموضوع", h("input", { name: "subject", required: true, maxlength: 300, value: q.get("subject") || "" }), true, "span2"),
      f("الجهة (المرسِل / المرسَل إليه)", h("input", { name: "party", required: true, maxlength: 200 }), true),
      f("رقم مرجع الجهة", h("input", { name: "party_ref", maxlength: 100 })),
      f("تاريخ المراسلة", h("input", { type: "date", name: "doc_date", required: true, value: today() }), true),
      f("تاريخ الاستحقاق", h("input", { type: "date", name: "due_date" })),
      f("الأولوية", sel("priority", L.priority, "normal")),
      f("درجة السرية", sel("conf_level", Object.fromEntries(L.conf.slice(0, state.user.clearance + 1).map((v, i) => [i, v])), 0)),
      f("التصنيف", h("input", { name: "category", maxlength: 60, placeholder: "مثال: عقود، شكاوى، تعاميم" })),
      isStaff() && state.user.role !== "manager" ? f("الإدارة المسؤولة", sel("department_id", Object.fromEntries(state.depts.map((d) => [d.id, d.name])), state.user.department_id)) : null,
      f("الإدارة المعنية (للمراسلات الداخلية)", sel("party_dept_id", { "": "—", ...Object.fromEntries(state.depts.map((d) => [d.id, d.name])) }, "")),
      f("إسناد إلى", sel("assigned_to", { "": "— بدون إسناد —", ...Object.fromEntries(state.users.filter((u) => u.id !== state.user.id).map((u) => [u.id, u.full_name])) }, "")),
      f("الملخص", h("textarea", { name: "summary", maxlength: 5000 }), false, "span2"),
      f("المرفق (PDF, Office, صور… حتى 20MB)", h("input", { type: "file", name: "file" }), false, "span2")),
    err, h("button", { class: "btn" }, "تسجيل المراسلة"));
  return [h("h2", {}, parent ? "تسجيل رد على مراسلة" : "تسجيل مراسلة جديدة"), form];
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
  const noteIn = h("input", { placeholder: "ملاحظة (اختياري)" });
  const manage = c.can_manage ? h("div", { class: "card" }, h("h3", {}, "إجراءات"),
    h("div", { class: "row" }, statusSel, prioSel, noteIn, h("button", { class: "btn", onclick: async () => {
      try { await api("/correspondences/" + id, { method: "PUT", body: { status: statusSel.value, priority: prioSel.value, note: noteIn.value } }); toast("تم التحديث"); render(); }
      catch (e) { toast(e.message, true); }
    } }, "تحديث"))) : null;

  const toSel = sel("to_user", Object.fromEntries(state.users.map((u) => [u.id, `${u.full_name} — ${L.role[u.role]}`])), "");
  const instr = h("input", { placeholder: "التوجيه / المطلوب" });
  const due = h("input", { type: "date" });
  const transferBox = c.can_manage && !["closed", "archived"].includes(c.status) ? h("div", { class: "card" }, h("h3", {}, "إحالة المراسلة"),
    h("div", { class: "row" }, toSel, instr, due, h("button", { class: "btn", onclick: async () => {
      try { await api(`/correspondences/${id}/transfers`, { method: "POST", body: { to_user: toSel.value, instruction: instr.value, due_date: due.value } }); toast("تمت الإحالة"); render(); }
      catch (e) { toast(e.message, true); }
    } }, "إحالة"))) : null;

  const fileIn = h("input", { type: "file" });
  return [
    h("div", { class: "row" }, h("h2", {}, c.subject), statusPill(c.status), c.priority !== "normal" ? pill(c.priority, L.priority[c.priority]) : null,
      c.conf_level ? pill("conf" + c.conf_level, L.conf[c.conf_level]) : null, overdue ? pill("urgent", "متأخرة") : null),
    h("div", { class: "card" }, h("dl", { class: "meta" },
      m("رقم القيد", h("span", { class: "ltr" }, c.ref_no)), m("النوع", typeLabel(c)),
      m("الجهة", c.party), m("رقم مرجع الجهة", c.party_ref || "—"),
      m("تاريخ المراسلة", [fmtDate(c.doc_date), h("div", { class: "small muted" }, hijri(c.doc_date))]),
      m("الاستحقاق", c.due_date || "—"), m("الإدارة المسؤولة", c.department_name || "—"), m("الإدارة المعنية", c.party_dept_name || "—"),
      m("المسؤول الحالي", c.assignee_name || "—"), m("التصنيف", c.category || "—"),
      m("سُجّلت بواسطة", `${c.creator_name} — ${fmtDT(c.created_at)}`),
      c.parent ? m("رد على", h("a", { href: "#/c/" + c.parent.id }, c.parent.ref_no)) : null),
      c.summary ? h("p", {}, h("strong", {}, "الملخص: "), c.summary) : null,
      h("div", { class: "row" }, h("a", { class: "btn ghost sm", href: `#/new?parent=${c.id}&scope=${c.scope}&direction=${c.direction === "incoming" ? "outgoing" : "incoming"}&subject=${encodeURIComponent("رد: " + c.subject)}` }, "تسجيل رد"),
        h("button", { class: "btn ghost sm", onclick: () => window.print() }, "طباعة"))),
    manage, transferBox,
    h("div", { class: "card" }, h("h3", {}, `المرفقات (${c.attachments.length})`),
      c.attachments.length ? h("table", {}, h("tbody", {}, c.attachments.map((a) => h("tr", {},
        h("td", {}, h("a", { href: "#", onclick: (e) => { e.preventDefault(); dl(a); } }, a.filename)), h("td", {}, sizeStr(a.size)),
        h("td", { class: "small muted" }, a.uploader, " — ", fmtDT(a.created_at)),
        h("td", { class: "small muted ltr", title: "بصمة SHA-256 للتحقق من سلامة الملف" }, a.sha256.slice(0, 16) + "…"))))) : h("div", { class: "muted" }, "لا مرفقات"),
      c.status !== "archived" ? h("div", { class: "row" }, fileIn, h("button", { class: "btn sm", onclick: async () => {
        if (!fileIn.files[0]) return toast("اختر ملفًا أولًا", true);
        try { await upload(id, fileIn.files[0]); toast("تم رفع الملف"); render(); } catch (e) { toast(e.message, true); }
      } }, "رفع")) : null),
    h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h3", {}, "مسار الإحالات"), c.transfers.length ? h("ul", { class: "tl" }, c.transfers.map((t) => h("li", {},
        h("strong", {}, t.from_name), " ← ", h("strong", {}, t.to_name), " ", pill(t.status === "done" ? "closed" : t.status === "pending" ? "in_progress" : "archived", { done: "منفّذة", pending: "معلّقة", superseded: "أُعيدت إحالتها" }[t.status]),
        h("div", { class: "small muted" }, fmtDT(t.created_at), t.instruction ? " — " + t.instruction : ""),
        t.note ? h("div", { class: "small" }, "ملاحظة التنفيذ: " + t.note) : null))) : h("div", { class: "muted" }, "لم تُحَل بعد")),
      h("div", { class: "card" }, h("h3", {}, "سجل العمليات"), h("ul", { class: "tl" }, c.timeline.map((t) => h("li", {},
        h("strong", {}, L.action[t.action] || t.action), " — ", t.username, h("div", { class: "small muted" }, fmtDT(t.at), t.detail ? " — " + t.detail : "")))))),
    c.replies.length ? h("div", { class: "card" }, h("h3", {}, "الردود والمراسلات المرتبطة"), corrTable(c.replies.map((r) => ({ ...r, party: "" })), true)) : null,
  ];
}

// ───────── الإدارة ─────────
async function usersAdminView() {
  const users = await api("/users");
  const e1 = h("div", { class: "err" }), e2 = h("div", { class: "err" });
  const uform = h("form", { class: "card", onsubmit: async (e) => {
    e.preventDefault(); e1.textContent = "";
    const b = Object.fromEntries([...uform.elements].filter((x) => x.name && x.value !== "").map((x) => [x.name, x.value]));
    try { await api("/users", { method: "POST", body: b }); toast("تم إنشاء المستخدم"); await loadRefs(); render(); } catch (ex) { e1.textContent = ex.message; }
  } }, h("h3", {}, "مستخدم جديد"),
    h("div", { class: "form-grid" },
      f("اسم المستخدم", h("input", { name: "username", required: true, pattern: "[A-Za-z0-9_.-]{3,32}", autocomplete: "off" }), true),
      f("الاسم الكامل", h("input", { name: "full_name", required: true }), true),
      f("كلمة مرور مؤقتة", h("input", { name: "password", type: "password", required: true, minlength: 8, autocomplete: "new-password" }), true),
      f("الدور", sel("role", L.role, "employee"), true),
      f("الإدارة", sel("department_id", Object.fromEntries(state.depts.map((d) => [d.id, d.name])), state.depts[0] && state.depts[0].id)),
      f("درجة الصلاحية السرّية", sel("clearance", Object.fromEntries(L.conf.map((v, i) => [i, v])), 0))),
    e1, h("button", { class: "btn" }, "إضافة"));
  const dform = h("form", { class: "card", onsubmit: async (e) => {
    e.preventDefault(); e2.textContent = "";
    try { await api("/departments", { method: "POST", body: { name: dform.name.value } }); await loadRefs(); render(); } catch (ex) { e2.textContent = ex.message; }
  } }, h("h3", {}, "إدارة جديدة"), h("div", { class: "row" }, h("input", { name: "name", required: true, placeholder: "اسم الإدارة" }), h("button", { class: "btn" }, "إضافة")), e2,
    h("p", { class: "small muted" }, "الإدارات الحالية: " + state.depts.map((d) => d.name).join("، ")));
  const reset = async (u) => {
    const pw = prompt(`كلمة مرور مؤقتة جديدة للمستخدم ${u.full_name} (8 أحرف، حروف وأرقام):`);
    if (!pw) return;
    try { await api("/users/" + u.id, { method: "PUT", body: { password: pw } }); toast("تم التصفير، سيُطلب تغييرها عند الدخول"); } catch (e) { toast(e.message, true); }
  };
  return [h("h2", {}, "المستخدمون والإدارات"), uform, dform,
    h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["الاسم", "المستخدم", "الدور", "الإدارة", "السرية", "الحالة", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, users.map((u) => h("tr", {}, h("td", {}, u.full_name), h("td", { class: "ltr" }, u.username), h("td", {}, L.role[u.role]), h("td", {}, deptName(u.department_id)),
        h("td", {}, L.conf[u.clearance]), h("td", {}, u.active ? pill("closed", "نشط") : pill("archived", "معطّل")),
        h("td", { class: "row" }, h("button", { class: "btn ghost sm", onclick: () => reset(u) }, "تصفير كلمة المرور"),
          u.id !== state.user.id ? h("button", { class: "btn ghost sm", onclick: async () => {
            try { await api("/users/" + u.id, { method: "PUT", body: { active: !u.active } }); render(); } catch (e) { toast(e.message, true); } } }, u.active ? "تعطيل" : "تفعيل") : null))))))];
}

async function auditView() {
  const rows = await api("/admin/audit?limit=300");
  return [h("h2", {}, "سجل التدقيق"), h("p", { class: "muted small" }, "كل العمليات (الدخول، التسجيل، الإحالة، التنزيل، التصدير…) مع المستخدم وعنوان الشبكة. السجل للقراءة فقط."),
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
  [state.depts, state.users] = await Promise.all([api("/departments"), api("/users")]);
}

async function boot() {
  try {
    state.user = await api("/me");
    if (!state.user.must_change) await loadRefs();
  } catch { state.token = null; sessionStorage.removeItem("acp_token"); }
  render();
}

async function render() {
  const app = document.getElementById("app");
  if (!state.token || !state.user) return app.replaceChildren(loginView());
  if (state.user.must_change) return app.replaceChildren(changePasswordView(true));
  const [path, qs] = (location.hash || "#/dashboard").slice(1).split("?");
  const q = new URLSearchParams(qs || "");
  const my = ++render.n;
  try {
    let view, tasks = 0;
    if (path === "/dashboard") view = await dashboardView();
    else if (path === "/list") view = await listView(q);
    else if (path === "/tasks") view = await tasksView();
    else if (path === "/new") view = newView(q);
    else if (path === "/password") view = changePasswordView(false);
    else if (path.startsWith("/c/")) view = await detailView(Number(path.slice(3)));
    else if (path === "/admin/users" && state.user.role === "admin") view = await usersAdminView();
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
