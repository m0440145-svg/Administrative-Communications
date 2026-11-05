"use strict";
// لوحة التحكم (الوحدات، الموظفون، التصنيفات والخصوصية، الصلاحيات) والتقارير.
// تعتمد على الدوال العامة في app.js (h, api, toast, f, sel, pill, state, L ...) وتُستدعى بعد تحميل الصفحة.

const ADMIN_TABS = [["units", "الوحدات"], ["staff", "الموظفون"], ["classes", "التصنيفات والخصوصية"], ["folders", "المجلدات"], ["types", "أنواع المعاملات"], ["directives", "توجيهات الإسناد"], ["perms", "الصلاحيات"], ["audit", "سجل الإجراءات"], ["integration", "التكامل"]];

function adminHead(current, title, note) {
  return [h("h2", {}, "إعدادات النظام"),
    h("div", { class: "tabs" }, ADMIN_TABS.map(([k, t]) => h("a", { href: "#/admin/" + k, class: "tab" + (k === current ? " on" : "") }, t))),
    h("h3", {}, title), note ? h("p", { class: "muted small" }, note) : null];
}

const CONF_LABELS = { 0: "عادي — يراه من له حق الاطلاع", 1: "سري — لمن صلاحيته «سري» فأعلى", 2: "سري للغاية — لمن صلاحيته «سري للغاية»" };
const confOpts = Object.fromEntries(Object.entries(CONF_LABELS));
const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };

// ───────── الوحدات ─────────
async function unitsView() {
  await loadRefs();
  const err = h("div", { class: "err" });
  const form = h("form", { class: "card row", onsubmit: guard(async (e) => {
    e.preventDefault(); err.textContent = "";
    try { await api("/departments", { method: "POST", body: { name: form.name.value } }); toast("تمت إضافة الوحدة"); render(); } catch (ex) { err.textContent = ex.message; }
  }) }, h("input", { name: "name", required: true, maxlength: 100, placeholder: "اسم الوحدة الجديدة", "aria-label": "اسم الوحدة" }), h("button", { class: "btn" }, "إضافة وحدة"), err);
  const count = (id) => state.users.filter((u) => u.department_id === id && u.active !== false).length;
  return [adminHead("units", "الوحدات (الإدارات والأقسام)", "تظهر الوحدات النشطة في نماذج التسجيل والفلاتر. تعطيل وحدة لا يحذف مراسلاتها."), form,
    h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["الوحدة", "عدد الموظفين", "الحالة", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, state.depts.map((d) => h("tr", {}, h("td", {}, d.name), h("td", {}, count(d.id)), h("td", {}, d.active ? pill("closed", "نشطة") : pill("archived", "معطّلة")),
        h("td", { class: "row" },
          h("button", { class: "btn ghost sm", onclick: guard(async () => { const n = prompt("الاسم الجديد للوحدة:", d.name); if (n && n.trim()) { await api("/departments/" + d.id, { method: "PUT", body: { name: n } }); render(); } }) }, "تعديل الاسم"),
          h("button", { class: "btn ghost sm", onclick: guard(async () => { await api("/departments/" + d.id, { method: "PUT", body: { active: !d.active } }); render(); }) }, d.active ? "تعطيل" : "تفعيل"))))))) ];
}

// ───────── الموظفون ─────────
async function staffView() {
  await loadRefs();
  const users = await api("/users");
  const box = h("div");
  const roleOpts = L.role;
  const clrOpts = Object.fromEntries(L.conf.map((v, i) => [i, `${i} — ${v}`]));

  const userForm = (u) => {
    const creating = !u;
    const err = h("div", { class: "err" });
    const fm = h("form", { class: "card", onsubmit: guard(async (e) => {
      e.preventDefault(); err.textContent = "";
      const b = {};
      for (const n of ["username", "full_name", "job_title", "phone", "email", "role", "department_id", "clearance", "password"]) {
        const el = fm.elements[n]; if (el && (el.value !== "" || (!creating && n !== "password"))) b[n] = el.value;
      }
      if (b.department_id === "") b.department_id = null;
      try {
        await api(creating ? "/users" : "/users/" + u.id, { method: creating ? "POST" : "PUT", body: b });
        toast(creating ? "تم إنشاء الموظف" : "تم الحفظ"); render();
      } catch (ex) { err.textContent = ex.message; }
    }) },
      h("h3", {}, creating ? "موظف جديد" : "تعديل: " + u.full_name),
      h("div", { class: "form-grid" },
        f("الاسم الكامل", h("input", { name: "full_name", required: true, maxlength: 100, value: u ? u.full_name : "" }), true),
        f("اسم المستخدم (إنجليزي)", h("input", { name: "username", required: true, pattern: "[A-Za-z0-9_.\\-]{3,32}", autocomplete: "off", dir: "ltr", value: u ? u.username : "" }), true),
        f("المسمى الوظيفي", h("input", { name: "job_title", maxlength: 100, value: (u && u.job_title) || "" })),
        f("الجوال", h("input", { name: "phone", maxlength: 30, dir: "ltr", value: (u && u.phone) || "" })),
        f("البريد الإلكتروني", h("input", { name: "email", type: "email", maxlength: 150, dir: "ltr", value: (u && u.email) || "" })),
        f("الدور", sel("role", roleOpts, u ? u.role : "employee"), true),
        f("الوحدة", sel("department_id", { "": "—", ...deptOptions() }, u ? u.department_id : "")),
        f("درجة الخصوصية (الصلاحية السرّية)", sel("clearance", clrOpts, u ? u.clearance : 0)),
        creating ? f("كلمة مرور مؤقتة", h("input", { name: "password", type: "password", required: true, minlength: 8, autocomplete: "new-password" }), true) : null),
      err, h("div", { class: "row" }, h("button", { class: "btn" }, "حفظ"), h("button", { type: "button", class: "btn ghost", onclick: () => box.replaceChildren(userForm()) }, creating ? "تفريغ" : "إلغاء")));
    return fm;
  };
  box.append(userForm());
  const edit = (u) => { box.replaceChildren(userForm(u)); box.scrollIntoView({ behavior: "smooth" }); };
  const reset = guard(async (u) => {
    const pw = prompt(`كلمة مرور مؤقتة جديدة للموظف ${u.full_name} (8 أحرف، حروف وأرقام):`);
    if (!pw) return;
    await api("/users/" + u.id, { method: "PUT", body: { password: pw } }); toast("تم التصفير، سيُطلب تغييرها عند الدخول");
  });
  return [adminHead("staff", "الموظفون وبياناتهم", "درجة الخصوصية تحدد أعلى مستوى سرية يُسمح للموظف برؤيته. الدور يحدد الصلاحيات الأساسية (تُضبط من تبويب الصلاحيات)."), box,
    h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["الاسم", "المستخدم", "المسمى", "الوحدة", "الدور", "الخصوصية", "الحالة", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, users.map((u) => h("tr", {}, h("td", {}, u.full_name), h("td", { class: "ltr" }, u.username), h("td", {}, u.job_title || "—"), h("td", {}, deptName(u.department_id)),
        h("td", {}, L.role[u.role]), h("td", {}, L.conf[u.clearance]), h("td", {}, u.active ? pill("closed", "نشط") : pill("archived", "معطّل")),
        h("td", { class: "row" }, h("button", { class: "btn ghost sm", onclick: () => edit(u) }, "تعديل"),
          h("button", { class: "btn ghost sm", onclick: () => reset(u) }, "تصفير كلمة المرور"),
          u.id !== state.user.id ? h("button", { class: "btn ghost sm", onclick: guard(async () => { await api("/users/" + u.id, { method: "PUT", body: { active: !u.active } }); render(); }) }, u.active ? "تعطيل" : "تفعيل") : null)))))) ];
}

// ───────── التصنيفات والخصوصية ─────────
async function classesView() {
  const all = await api("/classifications?all=1");
  const rowFor = (c) => {
    const label = h("input", { value: c.label, maxlength: 40, "aria-label": "الاسم" });
    const color = h("input", { type: "color", value: c.color, "aria-label": "اللون", class: "colorin" });
    const conf = sel("conf", confOpts, c.conf_level, { "aria-label": "الخصوصية" });
    const urg = h("input", { type: "checkbox", checked: !!c.is_urgent, "aria-label": "عاجل" });
    const act = h("input", { type: "checkbox", checked: !!c.active, "aria-label": "نشط" });
    const sort = h("input", { type: "number", value: c.sort, min: 0, max: 1000, class: "narrow", "aria-label": "الترتيب" });
    return h("tr", {}, h("td", {}, label), h("td", {}, color), h("td", {}, conf), h("td", {}, urg), h("td", {}, sort), h("td", {}, act),
      h("td", {}, h("button", { class: "btn sm", onclick: guard(async () => {
        await api("/classifications/" + c.key, { method: "PUT", body: { label: label.value, color: color.value, conf_level: Number(conf.value), is_urgent: urg.checked, sort: Number(sort.value), active: act.checked } });
        toast("تم الحفظ"); await loadRefs(); render();
      }) }, "حفظ")));
  };
  const nl = h("input", { placeholder: "اسم التصنيف الجديد", maxlength: 40, "aria-label": "اسم التصنيف" });
  const nc = h("input", { type: "color", value: "#475569", class: "colorin", "aria-label": "اللون" });
  const nconf = sel("nconf", confOpts, 0, { "aria-label": "الخصوصية" });
  return [adminHead("classes", "تصنيفات الوثيقة والخصوصية", "التصنيف هو ما يختاره المسجِّل للوثيقة (عاجل جدا، سري، عادي…). «الخصوصية» تحدد من يستطيع رؤية المراسلة: كل من صلاحيته مساوية أو أعلى. «عاجل» يُحتسب في مؤشرات العاجلة. التعديل يسري على التسجيلات الجديدة."),
    h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["التصنيف", "اللون", "الخصوصية", "عاجل", "الترتيب", "نشط", ""].map((t) => h("th", {}, t)))), h("tbody", {}, all.map(rowFor)))),
    h("div", { class: "card row" }, nl, nc, nconf, h("button", { class: "btn", onclick: guard(async () => {
      await api("/classifications", { method: "POST", body: { label: nl.value, color: nc.value, conf_level: Number(nconf.value) } });
      toast("تمت الإضافة"); await loadRefs(); render();
    }) }, "إضافة تصنيف"))];
}

// ───────── الصلاحيات ─────────
async function permsView() {
  const d = await api("/admin/perms");
  const roles = ["manager", "clerk", "employee"];
  return [adminHead("perms", "صلاحيات الأدوار", "مدير النظام يملك كل الصلاحيات دائمًا. الاطلاع على المراسلات يخضع دائمًا لدرجة الخصوصية، وللموظف العادي يقتصر على ما سجّله أو أُحيل إليه."),
    h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, [h("th", {}, "الصلاحية"), roles.map((r) => h("th", {}, L.role[r]))])),
      h("tbody", {}, Object.entries(d.perms).map(([perm, label]) => h("tr", {}, h("td", {}, label), roles.map((r) => h("td", {},
        h("input", { type: "checkbox", checked: d.roles[r][perm], "aria-label": `${label} — ${L.role[r]}`, class: "chk", onchange: guard(async (e) => {
          try { await api("/admin/perms", { method: "PUT", body: { role: r, perm, allowed: e.target.checked } }); toast("تم الحفظ"); }
          catch (ex) { e.target.checked = !e.target.checked; throw ex; }
        }) })))))))),
    h("p", { class: "small muted" }, "تسري التغييرات على المستخدمين عند دخولهم التالي أو فور تحديث الصفحة.")];
}

// ───────── المجلدات وأنواع المعاملات (قوائم بسيطة يديرها المدير) ─────────
function lookupView(kind, title, note, placeholder) {
  return async () => {
    const rows = await api(`/lookups/${kind}?all=1`);
    const nm = h("input", { placeholder, maxlength: 80, "aria-label": placeholder });
    const trow = (r) => {
      const name = h("input", { value: r.name, maxlength: 80, "aria-label": "الاسم" });
      const sort = h("input", { type: "number", value: r.sort, min: 0, max: 1000, class: "narrow", "aria-label": "الترتيب" });
      const act = h("input", { type: "checkbox", checked: !!r.active, "aria-label": "نشط" });
      return h("tr", {}, h("td", {}, name), h("td", {}, sort), h("td", {}, act), h("td", {}, h("button", { class: "btn sm", onclick: guard(async () => {
        await api(`/lookups/${kind}/${r.id}`, { method: "PUT", body: { name: name.value, sort: Number(sort.value), active: act.checked } });
        toast("تم الحفظ"); await loadRefs(); render();
      }) }, "حفظ")));
    };
    return [adminHead(kind, title, note),
      h("div", { class: "card row" }, nm, h("button", { class: "btn", onclick: guard(async () => {
        await api(`/lookups/${kind}`, { method: "POST", body: { name: nm.value } }); toast("تمت الإضافة"); await loadRefs(); render();
      }) }, "إضافة")),
      h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["الاسم", "الترتيب", "نشط", ""].map((t) => h("th", {}, t)))), h("tbody", {}, rows.map(trow))))];
  };
}

const ADMIN_VIEWS = {
  units: unitsView, staff: staffView, classes: classesView, perms: permsView,
  folders: lookupView("folders", "المجلدات", "المجلد هو مكان حفظ المراسلة في الأرشيف (مثل: التعاميم، العقود). يُختار عند التسجيل ويمكن تغييره لاحقًا ويُفلتر به.", "اسم المجلد الجديد"),
  directives: lookupView("directives", "توجيهات الإسناد", "خيارات قسيمة الإحالة (للاطلاع، للدراسة والإفادة، للرد…) التي تظهر تحت «إسناد إلى» ويُختار منها أكثر من توجيه، وتصل نصًا للموظف المُسنَد إليه. خيار «أخرى» نصي ثابت في النموذج.", "اسم التوجيه الجديد"),
  types: lookupView("types", "أنواع المعاملات", "نوع المعاملة يصف موضوع الخطاب (طلب، شكوى، تعميم…) ويظهر في القوائم والتقارير. تعطيل نوع لا يؤثر على المراسلات القديمة.", "اسم نوع المعاملة الجديد"),
};

// ───────── التقارير ─────────
async function reportsView(q) {
  const keys = ["from", "to", "scope", "direction", "dept", "priority", "status"];
  const params = new URLSearchParams();
  keys.forEach((k) => q.get(k) && params.set(k, q.get(k)));
  const d = await api("/reports?" + params);
  const apply = () => {
    const p = new URLSearchParams();
    for (const k of keys) { const el = form.elements[k]; if (el && el.value) p.set(k, el.value); }
    location.hash = "#/reports?" + p;
  };
  const dd = (name, label, opts) => sel(name, opts, q.get(name), { "aria-label": label, onchange: apply });
  const form = h("form", { class: "card filters", onsubmit: (e) => { e.preventDefault(); apply(); } }, h("div", { class: "frow" },
    h("label", { class: "inl" }, "من", h("input", { type: "date", name: "from", value: q.get("from") || "", onchange: apply })),
    h("label", { class: "inl" }, "إلى", h("input", { type: "date", name: "to", value: q.get("to") || "", onchange: apply })),
    dd("direction", "الاتجاه", { "": "وارد وصادر", ...L.direction }), dd("scope", "نوع البريد", { "": "داخلي وخارجي", ...L.scope }),
    dd("dept", "الوحدة", { "": "كل الوحدات", ...Object.fromEntries(state.depts.map((x) => [x.id, x.name])) }),
    dd("priority", "التصنيف", { "": "كل التصنيفات", ...L.priority }), dd("status", "الحالة", { "": "كل الحالات", ...L.status }),
    h("button", { type: "button", class: "btn ghost sm", onclick: () => window.print() }, "طباعة"),
    can("export") ? h("button", { type: "button", class: "btn ghost sm", onclick: () => exportCsv(params) }, "تصدير CSV") : null));

  const stat = (n, l) => h("div", { class: "card stat" }, h("div", { class: "n" }, n ?? "—"), h("div", { class: "l" }, l));
  const bars = (rows, label, val, color) => {
    const max = Math.max(1, ...rows.map(val));
    return h("table", { class: "bartbl" }, h("tbody", {}, rows.length ? rows.map((r) => {
      const b = h("div", { class: "hbar" }); b.style.width = (val(r) / max) * 100 + "%"; const c = color && color(r); if (c) b.style.background = c;
      return h("tr", {}, h("td", { class: "bl" }, label(r)), h("td", { class: "bb" }, b), h("td", { class: "bn" }, val(r)));
    }) : h("tr", {}, h("td", { class: "muted" }, "لا بيانات"))));
  };
  const sec = (title, body) => h("div", { class: "card" }, h("h3", {}, title), body);
  const type = (s, dr) => (d.by_type.find((t) => t.scope === s && t.direction === dr) || {}).n || 0;
  return [h("h2", {}, "التقارير والمؤشرات"), form,
    h("div", { class: "grid g4" }, stat(d.total, "إجمالي المراسلات"), stat(d.overdue, "متأخرة عن موعدها"), stat(d.awaiting_reply, "تحتاج رد ولم يُرد عليها"), stat(d.avg_close_days, "متوسط أيام الإنجاز")),
    h("div", { class: "grid g2" },
      sec("حسب الوحدة (وارد + صادر)", bars(d.by_unit, (r) => `${r.k}  (وارد ${r.incoming} · صادر ${r.outgoing})`, (r) => r.n)),
      sec("حسب تصنيف الوثيقة", bars(d.by_class, (r) => r.k, (r) => r.n, (r) => r.color)),
      sec("حسب الحالة", bars(d.by_status, (r) => L.status[r.k] || r.k, (r) => r.n)),
      sec("حسب النوع", bars([["خارجي", "external", "incoming"], ["خارجي", "external", "outgoing"], ["داخلي", "internal", "incoming"], ["داخلي", "internal", "outgoing"]]
        .map(([t, s, dr]) => ({ k: `${L.direction[dr]} ${t}`, n: type(s, dr) })), (r) => r.k, (r) => r.n)),
      sec("الحركة الشهرية (وارد / صادر)", bars(d.by_month, (r) => `${r.k}  (وارد ${r.incoming} · صادر ${r.outgoing})`, (r) => r.incoming + r.outgoing)),
      sec("حسب نوع المعاملة", bars(d.by_ctype, (r) => r.k, (r) => r.n)),
      sec("حسب المجلد", bars(d.by_folder, (r) => r.k, (r) => r.n)),
      sec("أكثر الجهات مراسلةً", bars(d.top_parties, (r) => r.k, (r) => r.n)))];
}
