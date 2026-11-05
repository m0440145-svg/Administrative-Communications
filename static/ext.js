"use strict";
// واجهة الميزات المضافة: الإشعارات، البحث المتقدم، الوسوم، الاقتراحات الذكية، القوالب، مسارات العمل والتوقيع،
// التفويض، الاحتجاز، النسخ الاحتياطي، الاستيراد، ومؤشرات الأداء.  تعتمد على دوال app.js و admin.js وتُحمَّل قبلهما.

const EXT_ROUTES = {};
const NOTIF_ICON = { assigned: "📌", reply_needed: "↩️", transfer: "↪️", status: "🔄", done: "✅", replied: "✅", overdue: "⚠️", due_soon: "⏰",
  kpi_alert: "📉", wf_step: "📝", wf_rejected: "❌", wf_done: "🏁", delegation: "🤝" };
const SIMPLE_KINDS = { review: "مراجعة", approve: "اعتماد" };
const STEP_STATUS = { waiting: "بانتظار الدور", pending: "بانتظار الإجراء", done: "تمّت", rejected: "مرفوضة", skipped: "ملغاة" };
const ROLE_PICK = { manager: "مدير الوحدة", clerk: "مكتب الصادر والوارد", admin: "مدير النظام" };
const guardX = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  h("a", { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// يعرض مقتطفًا فيه «كلمة» مظلّلة بأمان (بدون innerHTML)
function snippetNode(s) {
  const parts = String(s || "").split(/[«»]/);
  return h("span", { class: "snip" }, parts.map((t, i) => (i % 2 ? h("mark", {}, t) : t)));
}

// ───────────────────── الإشعارات ─────────────────────
async function pollNotifications() {
  if (!state.token || !state.user) return;
  try {
    const { unread } = await api("/notifications/count");
    state.unread = unread;
    const b = document.getElementById("notif-badge");
    if (b) { b.textContent = unread || ""; b.hidden = !unread; }
  } catch { /* صامت */ }
}

EXT_ROUTES["/notifications"] = async () => {
  const d = await api("/notifications?limit=100");
  state.unread = d.unread;
  const open = guardX(async (n) => {
    if (!n.read_at) await api("/notifications/read", { method: "POST", body: { ids: [n.id] } });
    if (n.corr_id) location.hash = "#/c/" + n.corr_id; else render();
  });
  return [h("div", { class: "row" }, h("h2", {}, "الإشعارات"),
    h("button", { class: "btn ghost sm", onclick: guardX(async () => { await api("/notifications/read", { method: "POST", body: { all: true } }); render(); }) }, "تعليم الكل كمقروء")),
    d.items.length ? h("div", { class: "card notifs" }, d.items.map((n) => h("div", { class: "notif" + (n.read_at ? "" : " unread"), role: "button", tabindex: 0, onclick: () => open(n),
      onkeydown: (e) => e.key === "Enter" && open(n) },
      h("span", { class: "ni" }, NOTIF_ICON[n.kind] || "🔔"), h("div", { class: "nb" }, h("strong", {}, n.title), n.body ? h("div", { class: "small muted" }, n.body) : null),
      h("span", { class: "small muted nowrap" }, fmtDT(n.created_at))))) : h("div", { class: "card empty" }, "لا توجد إشعارات")];
};

// ───────────────────── البحث المتقدم ─────────────────────
EXT_ROUTES["/search"] = async (q) => {
  const term = q.get("q") || "";
  const input = h("input", { type: "search", placeholder: "ابحث في الموضوع والملخص والجهة والأرقام والوسوم ونصوص المرفقات…", value: term, "aria-label": "بحث", class: "grow", autofocus: true });
  const form = h("form", { class: "card filters", onsubmit: (e) => { e.preventDefault(); location.hash = "#/search?q=" + encodeURIComponent(input.value.trim()); } },
    h("div", { class: "frow" }, input, h("button", { class: "btn" }, "بحث")),
    h("div", { class: "small muted" }, "يتجاوز البحث اختلاف الهمزات والتاء المربوطة والتشكيل وأداة التعريف. أضف * بعد الكلمة للبحث بالبداية."));
  let body;
  if (term) {
    const r = await api("/search?q=" + encodeURIComponent(term));
    body = r.items.length ? h("div", { class: "card" }, h("div", { class: "small muted" }, `${r.items.length} نتيجة`),
      r.items.map((i) => h("div", { class: "hit" }, h("div", { class: "row" }, h("a", { href: "#/c/" + i.id, class: "ltr" }, i.ref_no), h("strong", {}, h("a", { href: "#/c/" + i.id }, i.subject)),
        statusPill(i.status), i.priority !== "normal" ? prioPill(i.priority) : null), h("div", { class: "small muted" }, `${L.direction[i.direction]} ${L.scope[i.scope]} — ${i.party} — ${fmtDate(i.doc_date)}`),
        i.snip ? h("div", { class: "small" }, snippetNode(i.snip)) : null))) : h("div", { class: "card empty" }, "لا نتائج مطابقة");
  } else {
    const tags = await api("/tags");
    body = h("div", { class: "card" }, h("h3", {}, "الوسوم"), tags.length ? h("div", { class: "chips" }, tags.map((t) => h("a", { class: "chip", href: "#/list?tag=" + encodeURIComponent(t) }, "# " + t))) : h("div", { class: "muted" }, "لا وسوم بعد"));
  }
  return [h("h2", {}, "بحث متقدم"), form, body];
};

// ───────────────────── الوسوم / القوالب / الاقتراحات (نموذج التسجيل) ─────────────────────
function tagsInput(initial = []) {
  const tags = [...initial];
  const chips = h("div", { class: "chips" });
  const input = h("input", { placeholder: "اكتب وسمًا ثم Enter", maxlength: 30, "aria-label": "وسم جديد" });
  const paint = () => chips.replaceChildren(...tags.map((t) => h("div", { class: "chip" }, "# " + t,
    h("button", { type: "button", class: "x", "aria-label": "إزالة", onclick: () => { tags.splice(tags.indexOf(t), 1); paint(); } }, "×"))));
  const add = () => { const v = input.value.trim().replace(/^#/, ""); if (v && !tags.includes(v) && tags.length < 20) { tags.push(v); paint(); } input.value = ""; };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === "،" || e.key === ",") { e.preventDefault(); add(); } });
  input.addEventListener("blur", add);
  paint();
  return { node: h("div", {}, input, chips), values: () => { add(); return tags; } };
}

function fillTemplate(text, ctx) {
  return (text || "").replace(/\{(party|date|ref_orig|subject_orig)\}/g, (_, k) => ctx[k] || "");
}

// لوحة اقتراحات ذكية: تستدعي /suggest بعد توقف الكتابة وتعرض خيارات قابلة للتطبيق
function suggestPanel(getInput, apply) {
  const box = h("div", { class: "suggest", hidden: true, "aria-live": "polite" });
  let timer = null, last = "";
  const run = async () => {
    const inp = getInput();
    const key = JSON.stringify(inp);
    if ((inp.subject || "").length < 4 || key === last) return;
    last = key;
    try {
      const s = await api("/suggest?" + new URLSearchParams(inp));
      const items = [];
      const add = (label, v, fn) => v && items.push(h("button", { type: "button", class: "sug" + (v.source === "rule" ? " rule" : ""), onclick: () => { fn(v); toast("طُبّق الاقتراح"); },
        title: v.source === "rule" ? "من قاعدة توجيه" : `اقتراح آلي — ثقة ${Math.round(v.confidence * 100)}٪ من ${v.support} مراسلة مشابهة` },
        `${label}: ${v.name || v.label}`, v.source === "ml" ? h("span", { class: "small" }, ` ${Math.round(v.confidence * 100)}٪`) : " ⚙"));
      add("نوع المعاملة", s.type, (v) => apply.type(v.id)); add("المجلد", s.folder, (v) => apply.folder(v.id));
      add("التصنيف", s.priority && { ...s.priority, name: s.priority.label }, (v) => apply.priority(v.key)); add("الوحدة", s.unit, (v) => apply.unit(v));
      add("إسناد إلى", s.assignee, (v) => apply.assignee(v));
      box.replaceChildren(h("span", { class: "small muted" }, "💡 اقتراحات: "), ...items);
      box.hidden = !items.length;
    } catch { box.hidden = true; }
  };
  return { node: box, trigger: () => { clearTimeout(timer); timer = setTimeout(run, 700); } };
}

// ───────────────────── تفاصيل المراسلة: وسوم / مسار عمل / توقيع / احتجاز ─────────────────────
async function detailExtras(c) {
  const cards = [];
  // الوسوم
  const edit = tagsInput(c.tags);
  cards.push(h("div", { class: "card" }, h("h3", {}, "الوسوم"),
    c.can_manage ? h("div", {}, edit.node, h("button", { class: "btn sm", onclick: guardX(async () => { await api(`/correspondences/${c.id}/tags`, { method: "PUT", body: { tags: edit.values() } }); toast("حُفظت الوسوم"); render(); }) }, "حفظ الوسوم"))
      : (c.tags.length ? h("div", { class: "chips" }, c.tags.map((t) => h("a", { class: "chip", href: "#/list?tag=" + encodeURIComponent(t) }, "# " + t))) : h("div", { class: "muted" }, "لا وسوم"))));
  // مسار العمل
  const wf = c.workflow;
  const open = !["closed", "archived"].includes(c.status);
  if (wf) {
    const act = (st) => {
      const comment = h("input", { placeholder: "تعليق (إلزامي عند الرفض)", "aria-label": "تعليق" });
      const pw = st.kind === "approve" ? h("input", { type: "password", placeholder: "كلمة المرور لتأكيد التوقيع", autocomplete: "current-password", "aria-label": "كلمة المرور" }) : null;
      const send = (decision) => guardX(async () => {
        await api(`/wf/steps/${st.id}/decide`, { method: "POST", body: { decision, comment: comment.value, password: pw && pw.value } });
        toast(decision === "reject" ? "تم الرفض" : st.kind === "approve" ? "تم الاعتماد والتوقيع" : "تم"); render();
      })();
      return h("div", { class: "row wfact" }, comment, pw,
        h("button", { class: "btn sm", onclick: () => send(st.kind === "approve" ? "approve" : "done") }, st.kind === "approve" ? "اعتماد وتوقيع" : "تمت المراجعة"),
        h("button", { class: "btn danger sm", onclick: () => send("reject") }, "رفض"));
    };
    cards.push(h("div", { class: "card" }, h("div", { class: "row" }, h("h3", {}, "مسار العمل: " + wf.name),
      pill(wf.status === "done" ? "closed" : wf.status === "active" ? "in_progress" : "archived", { active: "جارٍ", done: "مكتمل", rejected: "مرفوض", cancelled: "ملغى" }[wf.status]),
      wf.status === "active" && c.can_manage ? h("button", { class: "btn ghost sm", onclick: guardX(async () => { if (confirm("إلغاء مسار العمل الجاري؟")) { await api(`/correspondences/${c.id}/workflow`, { method: "DELETE" }); render(); } }) }, "إلغاء المسار") : null),
      h("ol", { class: "wfsteps" }, wf.steps.map((st) => h("li", { class: "wfs " + st.status },
        h("div", { class: "row" }, h("strong", {}, `${st.idx + 1}. ${st.name}`), h("span", { class: "small muted" }, `(${SIMPLE_KINDS[st.kind]} — ${st.user_name})`),
          h("span", { class: "pill " + ({ done: "closed", pending: "in_progress", rejected: "urgent" }[st.status] || "archived") }, STEP_STATUS[st.status])),
        st.due_date && st.status === "pending" ? h("div", { class: "small muted" }, "الاستحقاق: " + st.due_date) : null,
        st.comment ? h("div", { class: "small" }, "التعليق: " + st.comment) : null, st.can_decide ? act(st) : null)))));
  } else if (c.can_manage && open) {
    const wfs = await api("/workflows");
    if (wfs.length) {
      const pick = sel("workflow_id", Object.fromEntries(wfs.map((w) => [w.id, w.name])), wfs[0].id, { "aria-label": "مسار العمل" });
      cards.push(h("div", { class: "card" }, h("h3", {}, "بدء مسار عمل"), h("div", { class: "row" }, pick,
        h("button", { class: "btn sm", onclick: guardX(async () => { await api(`/correspondences/${c.id}/workflow`, { method: "POST", body: { workflow_id: Number(pick.value) } }); toast("بدأ المسار"); render(); }) }, "بدء")),
        h("div", { class: "small muted" }, wfs.find((w) => w.id === Number(pick.value))?.description || "")));
    }
  }
  // التوقيعات والاعتماد الرسمي
  if (c.signatures.length || (c.can_approve && c.direction === "outgoing")) {
    const comment = h("input", { placeholder: "تعليق", "aria-label": "تعليق" });
    const pw = h("input", { type: "password", placeholder: "كلمة المرور لتأكيد التوقيع", autocomplete: "current-password", "aria-label": "كلمة المرور" });
    const direct = (decision) => guardX(async () => { await api(`/correspondences/${c.id}/approve`, { method: "POST", body: { decision, comment: comment.value, password: pw.value } }); toast("تم التوقيع"); render(); });
    cards.push(h("div", { class: "card" }, h("h3", {}, "الاعتماد والتوقيع الإلكتروني"),
      c.signatures.length ? h("table", {}, h("tbody", {}, c.signatures.map((s) => h("tr", {},
        h("td", {}, s.decision === "approve" ? "✅ اعتماد" : "❌ رفض"), h("td", {}, s.signer, s.step ? h("div", { class: "small muted" }, s.step) : null),
        h("td", { class: "small muted" }, fmtDT(s.signed_at), s.comment ? " — " + s.comment : ""),
        h("td", {}, s.signature_valid ? pill("closed", "التوقيع سليم") : pill("urgent", "التوقيع غير صالح"), " ",
          s.document_unchanged ? pill("closed", "الوثيقة كما اعتُمدت") : pill("in_progress", "تغيّرت الوثيقة بعد الاعتماد")))))) : h("div", { class: "muted" }, "لا توقيعات بعد"),
      c.can_approve && c.direction === "outgoing" && open ? h("div", { class: "row" }, comment, pw, h("button", { class: "btn sm", onclick: direct("approve") }, "اعتماد رسمي"),
        h("button", { class: "btn danger sm", onclick: direct("reject") }, "رفض")) : null,
      h("div", { class: "small muted" }, "توقيع اعتماد داخلي: بصمة الوثيقة والمرفقات + هوية المعتمِد + الوقت، موقّعة بمفتاح الخادم ويمكن التحقق منها في أي وقت.")));
  }
  // الاحتجاز
  if (state.user.role === "admin" || c.legal_hold || c.disposed_at) {
    cards.push(h("div", { class: "card" }, h("h3", {}, "الاحتجاز"),
      h("div", { class: "row" }, c.legal_hold ? pill("urgent", "🔒 تجميد قانوني") : pill("archived", "بلا تجميد"), c.disposed_at ? pill("archived", "أُتلفت المرفقات " + fmtDate(c.disposed_at)) : null,
        state.user.role === "admin" ? h("button", { class: "btn ghost sm", onclick: guardX(async () => {
          if (c.legal_hold) { await api(`/correspondences/${c.id}/legal-hold`, { method: "POST", body: { hold: false } }); }
          else { const r = prompt("سبب التجميد القانوني (يمنع الإتلاف):"); if (!r) return; await api(`/correspondences/${c.id}/legal-hold`, { method: "POST", body: { hold: true, reason: r } }); }
          render();
        }) }, c.legal_hold ? "رفع التجميد" : "تجميد قانوني") : null)));
  }
  return cards;
}

// ───────────────────── التفويض ─────────────────────
EXT_ROUTES["/delegation"] = async () => {
  const d = await api("/delegations");
  const iso = (n) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);
  const people = Object.fromEntries(state.users.filter((u) => u.id !== state.user.id && u.active !== false).map((u) => [u.id, u.full_name]));
  const to = sel("to_user", people, ""), a = h("input", { type: "date", value: iso(0), "aria-label": "من" }), b = h("input", { type: "date", value: iso(7), "aria-label": "إلى" });
  const note = h("input", { placeholder: "ملاحظة (مثل: إجازة)", maxlength: 200, "aria-label": "ملاحظة" });
  const tbl = (rows, who) => rows.length ? h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, [who, "من", "إلى", "ملاحظة", "الحالة", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, h("td", {}, who === "المفوَّض إليه" ? r.to_name : r.from_name), h("td", {}, r.start_date), h("td", {}, r.end_date), h("td", {}, r.note || "—"),
      h("td", {}, r.active ? pill("closed", "ساري") : pill("archived", "ملغى")),
      h("td", {}, r.active && who === "المفوَّض إليه" ? h("button", { class: "btn danger sm", onclick: guardX(async () => { await api("/delegations/" + r.id, { method: "DELETE" }); render(); }) }, "إلغاء") : null)))))) : h("div", { class: "muted" }, "لا يوجد");
  return [h("h2", {}, "التفويض"), h("p", { class: "muted small" }, "فوّض زميلًا خلال إجازتك: يرى مهامك ومراسلاتك ويتصرف فيها نيابةً عنك طوال المدة، ضمن درجة سريته هو لا درجتك، وتُسجَّل إجراءاته باسمه."),
    h("div", { class: "card" }, h("h3", {}, "تفويض جديد"), h("div", { class: "row" }, to, h("label", { class: "inl" }, "من", a), h("label", { class: "inl" }, "إلى", b), note,
      h("button", { class: "btn", onclick: guardX(async () => { await api("/delegations", { method: "POST", body: { to_user: Number(to.value), start_date: a.value, end_date: b.value, note: note.value } }); toast("تم التفويض"); render(); }) }, "تفويض"))),
    h("div", { class: "card" }, h("h3", {}, "فوّضتُهم"), tbl(d.given, "المفوَّض إليه")), h("div", { class: "card" }, h("h3", {}, "فُوّضت إليّ"), tbl(d.received, "المفوِّض"))];
};

// ───────────────────── مؤشرات الأداء (تُضاف لصفحة التقارير) ─────────────────────
async function kpiBlock(params) {
  const p = new URLSearchParams(); ["from", "to"].forEach((k) => params.get(k) && p.set(k, params.get(k)));
  const k = await api("/kpi?" + p);
  const card = (n, l, suffix = "") => h("div", { class: "card stat" }, h("div", { class: "n" }, n == null ? "—" : n + suffix), h("div", { class: "l" }, l));
  const bars = (rows, label, val) => { const mx = Math.max(1, ...rows.map(val)); return h("table", { class: "bartbl" }, h("tbody", {}, rows.map((r) => {
    const bar = h("div", { class: "hbar" }); bar.style.width = (val(r) / mx) * 100 + "%";
    return h("tr", {}, h("td", { class: "bl" }, label(r)), h("td", { class: "bb" }, bar), h("td", { class: "bn" }, val(r))); }))); };
  return [h("h3", {}, "مؤشرات الأداء (KPI)"),
    h("div", { class: "grid g4" }, card(k.sla_compliance_pct, "الالتزام بالمواعيد", "٪"), card(k.avg_first_response_hours, "متوسط زمن أول استجابة (ساعة)"),
      card(k.reply_rate_pct, "نسبة الرد على ما يحتاج رد", "٪"), card(k.avg_reply_days, "متوسط أيام الرد")),
    h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h3", {}, "أعمار المفتوح (أيام)"), bars(Object.entries(k.backlog_age).map(([a, n]) => ({ a, n })), (r) => r.a, (r) => r.n)),
      h("div", { class: "card" }, h("h3", {}, "الوارد الجديد مقابل المنجز شهريًا"), k.monthly.length ? h("table", {}, h("thead", {}, h("tr", {}, ["الشهر", "جديد", "منجز"].map((t) => h("th", {}, t)))),
        h("tbody", {}, k.monthly.map((m) => h("tr", {}, h("td", { class: "ltr" }, m.month), h("td", {}, m.created), h("td", {}, m.closed))))) : h("div", { class: "muted" }, "لا بيانات")),
      h("div", { class: "card" }, h("h3", {}, "أداء الوحدات"), h("table", {}, h("thead", {}, h("tr", {}, ["الوحدة", "الإجمالي", "مفتوح", "متأخر"].map((t) => h("th", {}, t)))),
        h("tbody", {}, k.units.map((u) => h("tr", {}, h("td", {}, u.unit), h("td", {}, u.total), h("td", {}, u.open_n), h("td", {}, u.overdue ? h("strong", { class: "bad" }, u.overdue) : 0)))))),
      h("div", { class: "card" }, h("h3", {}, "الأعباء على الموظفين (إحالات معلّقة)"), k.workload.length ? bars(k.workload, (r) => r.employee, (r) => r.pending) : h("div", { class: "muted" }, "لا إحالات معلّقة")))];
}

// ───────────────────── صفحات الإدارة المضافة ─────────────────────
const optsOf = (arr, key = "id", name = "name") => Object.fromEntries(arr.map((x) => [x[key], x[name]]));
const activeUsers = () => state.users.filter((u) => u.active !== false);

async function workflowsView() {
  const wfs = await api("/workflows?all=1");
  const people = optsOf(activeUsers(), "id", "full_name");
  const stepsBox = h("div", { class: "stepsb" });
  const addStep = (s = {}) => {
    const name = h("input", { placeholder: "اسم الخطوة", value: s.name || "", maxlength: 60, "aria-label": "اسم الخطوة" });
    const kind = sel("kind", SIMPLE_KINDS, s.kind || "review", { "aria-label": "النوع" });
    const who = sel("who", { ...Object.fromEntries(Object.entries(ROLE_PICK).map(([k, v]) => ["role:" + k, "دور: " + v])), ...Object.fromEntries(Object.entries(people).map(([k, v]) => ["user:" + k, "موظف: " + v])) }, "role:manager", { "aria-label": "المسؤول" });
    const days = h("input", { type: "number", min: 1, max: 60, value: s.days || 3, class: "narrow", "aria-label": "المهلة بالأيام" });
    const row = h("div", { class: "row steprow" }, name, kind, who, h("label", { class: "inl" }, "أيام", days), h("button", { type: "button", class: "btn ghost sm", onclick: () => row.remove() }, "حذف"));
    row.read = () => { const [t, v] = who.value.split(":"); return { name: name.value, kind: kind.value, days: Number(days.value), ...(t === "role" ? { role: v } : { user_id: Number(v) }) }; };
    stepsBox.append(row);
  };
  addStep({ name: "مراجعة", kind: "review" }); addStep({ name: "اعتماد", kind: "approve" });
  const nm = h("input", { placeholder: "اسم المسار", maxlength: 80, "aria-label": "اسم المسار" }), ds = h("input", { placeholder: "وصف مختصر", maxlength: 300, "aria-label": "الوصف" });
  const close = h("input", { type: "checkbox" });
  return [adminHead("workflows", "مسارات العمل", "تعريف تسلسل الخطوات (مراجعة / اعتماد) وكل خطوة لدور أو موظف معيّن ومهلة بالأيام. تُطبَّق على أي مراسلة من صفحة تفاصيلها، ويُرسل إشعار لصاحب كل خطوة. خطوة «اعتماد» تتطلب كلمة المرور وتُنتج توقيعًا إلكترونيًا."),
    h("div", { class: "card" }, h("h3", {}, "مسار جديد"), h("div", { class: "row" }, nm, ds, h("label", { class: "opt" }, close, "يُغلق الخطاب عند اكتمال المسار")), stepsBox,
      h("div", { class: "row" }, h("button", { type: "button", class: "btn ghost sm", onclick: () => addStep() }, "+ خطوة"),
        h("button", { class: "btn", onclick: guardX(async () => { await api("/workflows", { method: "POST", body: { name: nm.value, description: ds.value, close_on_finish: close.checked, steps: [...stepsBox.children].map((r) => r.read()) } }); toast("حُفظ المسار"); render(); }) }, "حفظ المسار"))),
    h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["المسار", "الخطوات", "الإغلاق التلقائي", "الحالة", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, wfs.map((w) => h("tr", {}, h("td", {}, w.name, w.description ? h("div", { class: "small muted" }, w.description) : null),
        h("td", { class: "small" }, w.steps.map((s, i) => `${i + 1}) ${s.name} [${SIMPLE_KINDS[s.kind]} — ${s.role ? ROLE_PICK[s.role] : (people[s.user_id] || "موظف")} — ${s.days}ي]`).join("  ←  ")),
        h("td", {}, w.close_on_finish ? "نعم" : "لا"), h("td", {}, w.active ? pill("closed", "فعّال") : pill("archived", "معطّل")),
        h("td", {}, h("button", { class: "btn ghost sm", onclick: guardX(async () => { await api("/workflows/" + w.id, { method: "PUT", body: { active: !w.active } }); render(); }) }, w.active ? "تعطيل" : "تفعيل"))))))) ];
}

async function rulesView() {
  const rules = await api("/admin/rules");
  const nameOf = (arr, id) => (arr.find((x) => x.id === id) || {}).name || "—";
  const f_ = (id) => document.getElementById(id);
  const fld = { name: h("input", { placeholder: "اسم القاعدة", maxlength: 80, "aria-label": "اسم القاعدة" }),
    kw: h("input", { placeholder: "كلمات مفتاحية (افصل بينها بـ ،)", maxlength: 300, "aria-label": "كلمات مفتاحية" }),
    party: h("input", { placeholder: "جزء من اسم الجهة", maxlength: 100, "aria-label": "الجهة" }),
    dir: sel("dir", { "": "وارد وصادر", ...L.direction }, "", { "aria-label": "الاتجاه" }),
    type: sel("type", { "": "— نوع المعاملة —", ...optsOf(state.types) }, "", { "aria-label": "نوع المعاملة" }),
    folder: sel("folder", { "": "— المجلد —", ...folderOptions() }, "", { "aria-label": "المجلد" }),
    prio: sel("prio", { "": "— التصنيف —", ...Object.fromEntries(state.cls.map((c) => [c.key, c.label])) }, "", { "aria-label": "التصنيف" }),
    unit: sel("unit", { "": "— وحدة مسؤولة —", ...deptOptions() }, "", { "aria-label": "الوحدة" }),
    user: sel("user", { "": "— إسناد إلى —", ...optsOf(activeUsers(), "id", "full_name") }, "", { "aria-label": "الإسناد" }),
    auto: h("input", { type: "checkbox" }) };
  const num = (v) => (v ? Number(v) : null);
  return [adminHead("rules", "قواعد التوجيه الذكي", "إذا طابقت المراسلة الكلمات المفتاحية أو الجهة اقترحت القاعدة (بثقة 100٪) النوع والمجلد والتصنيف والوحدة والمُسنَد إليه عند التسجيل. فعّل «تطبيق تلقائي» لتُنفَّذ دون تدخل. القواعد تتفوق على الاقتراح الآلي المتعلَّم من الأرشيف."),
    h("div", { class: "card" }, h("h3", {}, "قاعدة جديدة"), h("div", { class: "row" }, fld.name, fld.kw, fld.party, fld.dir), h("div", { class: "row" }, fld.type, fld.folder, fld.prio, fld.unit, fld.user),
      h("div", { class: "row" }, h("label", { class: "opt" }, fld.auto, "تطبيق تلقائي عند التسجيل"), h("button", { class: "btn", onclick: guardX(async () => {
        await api("/admin/rules", { method: "POST", body: { name: fld.name.value, keywords: fld.kw.value, party_contains: fld.party.value, direction: fld.dir.value || null, set_type_id: num(fld.type.value), set_folder_id: num(fld.folder.value),
          set_priority: fld.prio.value || null, set_dept_id: num(fld.unit.value), assign_user_id: num(fld.user.value), auto_apply: fld.auto.checked } });
        toast("أُضيفت القاعدة"); render();
      }) }, "إضافة القاعدة"))),
    rules.length ? h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["القاعدة", "الشرط", "الإجراء", "تلقائي", "نشطة", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, rules.map((r) => h("tr", {}, h("td", {}, r.name), h("td", { class: "small" }, [r.keywords && "كلمات: " + r.keywords, r.party_contains && "الجهة: " + r.party_contains, r.direction && L.direction[r.direction]].filter(Boolean).join(" • ")),
        h("td", { class: "small" }, [r.set_type_id && "نوع: " + nameOf(state.types, r.set_type_id), r.set_folder_id && "مجلد: " + nameOf(state.folders, r.set_folder_id), r.set_priority && "تصنيف: " + (L.priority[r.set_priority] || r.set_priority),
          r.set_dept_id && "وحدة: " + nameOf(state.depts, r.set_dept_id), r.assign_user_id && "إسناد: " + ((state.users.find((u) => u.id === r.assign_user_id) || {}).full_name || "—")].filter(Boolean).join(" • ")),
        h("td", {}, r.auto_apply ? "✔" : "—"), h("td", {}, r.active ? "✔" : "—"),
        h("td", { class: "row" }, h("button", { class: "btn ghost sm", onclick: guardX(async () => { await api("/admin/rules/" + r.id, { method: "PUT", body: { active: !r.active } }); render(); }) }, r.active ? "تعطيل" : "تفعيل"),
          h("button", { class: "btn danger sm", onclick: guardX(async () => { if (confirm("حذف القاعدة؟")) { await api("/admin/rules/" + r.id, { method: "DELETE" }); render(); } }) }, "حذف")))))) ) : h("div", { class: "card empty" }, "لا قواعد بعد — الاقتراح الآلي يعمل من الأرشيف تلقائيًا")];
}

async function templatesView() {
  const rows = await api("/templates?all=1");
  const nm = h("input", { placeholder: "اسم القالب", maxlength: 80, "aria-label": "اسم القالب" }), dir = sel("dir", { any: "أي اتجاه", outgoing: "صادر", incoming: "وارد" }, "outgoing", { "aria-label": "الاتجاه" });
  const sb = h("input", { placeholder: "عنوان الموضوع (يدعم {subject_orig})", maxlength: 300, "aria-label": "الموضوع" });
  const bd = h("textarea", { placeholder: "نص الخطاب… المتغيرات: {party} الجهة · {date} تاريخ اليوم · {ref_orig} رقم الأصل · {subject_orig} موضوع الأصل", maxlength: 5000, "aria-label": "النص" });
  return [adminHead("templates", "قوالب الخطابات", "قوالب جاهزة تُملأ تلقائيًا عند تسجيل خطاب أو رد (الموضوع والملخص). المتغيرات: {party} {date} {ref_orig} {subject_orig}."),
    h("div", { class: "card" }, h("h3", {}, "قالب جديد"), h("div", { class: "row" }, nm, dir), h("div", { class: "field" }, sb), h("div", { class: "field" }, bd),
      h("button", { class: "btn", onclick: guardX(async () => { await api("/templates", { method: "POST", body: { name: nm.value, direction: dir.value, subject_tpl: sb.value, body_tpl: bd.value } }); toast("أُضيف القالب"); render(); }) }, "إضافة")),
    rows.map((t) => {
      const n = h("input", { value: t.name, maxlength: 80, "aria-label": "الاسم" }), s = h("input", { value: t.subject_tpl || "", maxlength: 300, "aria-label": "الموضوع" });
      const b = h("textarea", { maxlength: 5000, "aria-label": "النص" }); b.value = t.body_tpl || "";
      const a = h("input", { type: "checkbox", checked: !!t.active, "aria-label": "نشط" });
      return h("div", { class: "card" }, h("div", { class: "row" }, n, h("label", { class: "opt" }, a, "نشط"), h("span", { class: "pill" }, { any: "أي اتجاه", outgoing: "صادر", incoming: "وارد" }[t.direction]),
        h("button", { class: "btn sm", onclick: guardX(async () => { await api("/templates/" + t.id, { method: "PUT", body: { name: n.value, subject_tpl: s.value, body_tpl: b.value, active: a.checked } }); toast("حُفظ"); render(); }) }, "حفظ")),
        h("div", { class: "field" }, s), h("div", { class: "field" }, b));
    })];
}

async function retentionView() {
  const [pols, cands, log] = await Promise.all([api("/admin/retention"), api("/admin/retention/candidates"), api("/admin/disposal-log")]);
  const nm = h("input", { placeholder: "اسم السياسة", maxlength: 80, "aria-label": "الاسم" }), days = h("input", { type: "number", min: 1, value: 365, class: "narrow", "aria-label": "بعد (يوم)" });
  const act = sel("action", { archive: "أرشفة المنجز بعد…", dispose: "إتلاف مرفقات المؤرشف بعد…" }, "archive", { "aria-label": "الإجراء" });
  const cls = sel("cls", { "": "كل التصنيفات", ...Object.fromEntries(state.cls.map((c) => [c.key, c.label])) }, "", { "aria-label": "التصنيف" });
  const ty = sel("ty", { "": "كل الأنواع", ...optsOf(state.types) }, "", { "aria-label": "النوع" });
  const checks = new Map();
  const doApply = (action) => guardX(async () => {
    const ids = [...checks].filter(([, el]) => el.checked).map(([id]) => id);
    if (!ids.length) return toast("حدّد مراسلات أولًا", true);
    const pw = prompt(`تأكيد ${action === "dispose" ? "الإتلاف (لا رجعة فيه — تُحذف الملفات ويبقى السجل)" : "الأرشفة"} لـ ${ids.length} مراسلة. أدخل كلمة مرورك:`);
    if (!pw) return;
    const reason = action === "dispose" ? prompt("سبب الإتلاف (إلزامي):") : "سياسة احتجاز";
    if (action === "dispose" && !reason) return;
    const r = await api("/admin/retention/apply", { method: "POST", body: { action, ids, password: pw, reason } });
    toast(`نُفّذ على ${r.done} مراسلة`); render();
  });
  const candRows = (action) => cands.filter((c) => c.action === action).map((c) => {
    const cb = h("input", { type: "checkbox", "aria-label": "تحديد" }); checks.set(c.id, cb);
    return h("tr", {}, h("td", {}, cb), h("td", {}, h("a", { href: "#/c/" + c.id, class: "ltr" }, c.ref_no)), h("td", {}, c.subject), h("td", { class: "small muted" }, c.policy), h("td", { class: "small muted ltr" }, c.updated_at.slice(0, 10)));
  });
  const candTable = (action, label) => { const rs = candRows(action); return h("div", { class: "card" }, h("h3", {}, label + ` (${rs.length})`),
    rs.length ? [h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["", "الرقم", "الموضوع", "السياسة", "آخر تحديث"].map((t) => h("th", {}, t)))), h("tbody", {}, rs))),
      h("button", { class: action === "dispose" ? "btn danger" : "btn", onclick: doApply(action) }, action === "dispose" ? "تنفيذ الإتلاف للمحدد" : "أرشفة المحدد")] : h("div", { class: "muted" }, "لا مراسلات مؤهلة حاليًا")); };
  return [adminHead("retention", "سياسات الاحتجاز والإتلاف", "الأرشفة والإتلاف لا يحدثان تلقائيًا: تُعرض المؤهّلات للمراجعة ثم تنفّذها أنت بكلمة مرورك. الإتلاف يحذف ملفات المرفقات فقط ويُبقي بيانات المراسلة وسجل الإتلاف (بصمات الملفات). المراسلات ذات «التجميد القانوني» مستثناة دائمًا."),
    h("div", { class: "card" }, h("h3", {}, "سياسة جديدة"), h("div", { class: "row" }, nm, act, h("label", { class: "inl" }, "بعد", days, "يوم"), cls, ty,
      h("button", { class: "btn", onclick: guardX(async () => { await api("/admin/retention", { method: "POST", body: { name: nm.value, after_days: Number(days.value), action: act.value, class_key: cls.value || null, type_id: ty.value ? Number(ty.value) : null } }); render(); }) }, "إضافة"))),
    pols.length ? h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["السياسة", "الإجراء", "بعد", "التصنيف", "النوع", "نشطة", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, pols.map((p) => h("tr", {}, h("td", {}, p.name), h("td", {}, p.action === "archive" ? "أرشفة" : "إتلاف مرفقات"), h("td", {}, p.after_days + " يوم"), h("td", {}, L.priority[p.class_key] || "الكل"),
        h("td", {}, (state.types.find((t) => t.id === p.type_id) || {}).name || "الكل"), h("td", {}, p.active ? "✔" : "—"),
        h("td", { class: "row" }, h("button", { class: "btn ghost sm", onclick: guardX(async () => { await api("/admin/retention/" + p.id, { method: "PUT", body: { active: !p.active } }); render(); }) }, p.active ? "تعطيل" : "تفعيل"),
          h("button", { class: "btn danger sm", onclick: guardX(async () => { if (confirm("حذف السياسة؟")) { await api("/admin/retention/" + p.id, { method: "DELETE" }); render(); } }) }, "حذف")))))) ) : null,
    candTable("archive", "مؤهّلة للأرشفة"), candTable("dispose", "مؤهّلة للإتلاف"),
    h("div", { class: "card" }, h("h3", {}, "سجل الإتلاف"), log.length ? h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["الرقم", "الموضوع", "بواسطة", "التاريخ", "السبب", "الملفات (بصماتها)"].map((t) => h("th", {}, t)))),
      h("tbody", {}, log.map((l) => h("tr", {}, h("td", { class: "ltr" }, l.ref_no), h("td", {}, l.subject), h("td", {}, l.by_name), h("td", { class: "small ltr" }, fmtDT(l.disposed_at)), h("td", {}, l.reason),
        h("td", { class: "small" }, JSON.parse(l.attachments).map((a) => `${a.filename} (${a.sha256.slice(0, 10)}…)`).join("، ") || "—")))))) : h("div", { class: "muted" }, "لا عمليات إتلاف"))];
}

async function opsView() {
  const [sys, backups, st] = await Promise.all([api("/admin/system"), api("/admin/backups"), api("/admin/settings")]);
  const fmt = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.ceil(n / 1024) + " KB");
  const out = h("div", { class: "small" });
  const inputs = Object.fromEntries(Object.entries({ reminder_days_before: "التذكير قبل الاستحقاق بـ (أيام)", alert_overdue_pct: "تنبيه عند تجاوز نسبة المتأخر (٪)",
    alert_avg_close_days: "تنبيه عند تجاوز متوسط أيام الإنجاز", alert_backlog_max: "تنبيه عند تجاوز عدد المفتوح (0 = معطّل)" }).map(([k, l]) => [k, [l, h("input", { type: "number", min: 0, step: "any", value: st[k], "aria-label": l })]]));
  const row = (k, v, ok) => h("tr", {}, h("td", {}, k), h("td", {}, v, ok === undefined ? null : [" ", ok ? pill("closed", "✔") : pill("urgent", "✖")]));
  return [adminHead("ops", "النسخ الاحتياطي والسلامة", "حالة النظام، النسخ الاحتياطية (تلقائية كل 24 ساعة افتراضيًا)، التحقق من سلامة سجل الإجراءات، وحدود التنبيه بانحراف الأداء."),
    h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h3", {}, "حالة النظام"), h("table", {}, h("tbody", {},
        row("الإصدار", `${sys.version} (Python ${sys.python} · SQLite ${sys.sqlite})`), row("المراسلات / المستخدمون النشطون", `${sys.correspondences} / ${sys.users}`),
        row("قاعدة البيانات", fmt(sys.db_size)), row("المرفقات", `${sys.attachments} ملف — ${fmt(sys.attachments_size)}`),
        row("سلسلة سجل الإجراءات", `${sys.audit_chain.checked} سجلًا موثّقًا` + (sys.audit_chain.legacy_unverified ? ` (+${sys.audit_chain.legacy_unverified} قديمة)` : ""), sys.audit_chain.ok),
        row("آخر نسخة احتياطية", sys.last_backup || "لا يوجد", !!sys.last_backup), row("تشفير المرفقات عند الراحة", sys.encryption_at_rest ? "مفعّل" : "غير مفعّل (ACP_ENC_KEY)", sys.encryption_at_rest),
        row("الدخول الموحّد SSO", sys.sso ? "مفعّل" : "غير مفعّل"), row("اتصال HTTPS", sys.tls ? "مفعّل" : "غير مفعّل (استخدم وكيلًا عكسيًا أو ACP_TLS_CERT)", sys.tls)))),
      h("div", { class: "card" }, h("h3", {}, "الأدوات"), h("div", { class: "row" },
        h("button", { class: "btn", onclick: guardX(async () => { const r = await api("/admin/audit/verify"); out.textContent = r.ok ? `✔ السلسلة سليمة (${r.checked} سجلًا)` : `✖ تلاعب مكتشف عند السجل #${r.first_bad_id}`; out.className = "small " + (r.ok ? "ok" : "bad"); }) }, "التحقق من سلامة سجل الإجراءات"),
        h("button", { class: "btn ghost", onclick: guardX(async () => { const r = await api("/admin/reminders/run", { method: "POST", body: {} }); out.textContent = `تذكيرات جديدة: ${r.reminders} • تنبيهات: ${r.alerts.length ? r.alerts.join("؛ ") : "لا"}`; out.className = "small"; }) }, "تشغيل التذكيرات الآن")), out)),
    h("div", { class: "card" }, h("div", { class: "row" }, h("h3", {}, "النسخ الاحتياطية"), h("button", { class: "btn sm", onclick: guardX(async () => { const r = await api("/admin/backup", { method: "POST", body: {} }); toast("تمت النسخة " + r.name); render(); }) }, "نسخة احتياطية الآن")),
      backups.length ? h("table", {}, h("tbody", {}, backups.slice(0, 15).map((b) => h("tr", {}, h("td", { class: "ltr" }, b.name), h("td", {}, fmt(b.size)), h("td", { class: "small muted ltr" }, b.at),
        h("td", {}, h("button", { class: "btn ghost sm", onclick: guardX(async () => downloadBlob(await (await api("/admin/backups/download?name=" + encodeURIComponent(b.name), { raw: true })).blob(), b.name)) }, "تنزيل")))))) : h("div", { class: "muted" }, "لا نسخ بعد"),
      h("div", { class: "small muted" }, "الاسترجاع من سطر الأوامر بعد إيقاف الخادم: ", h("code", { class: "ltr" }, "python server.py restore acp-….zip --force"), " — النسخة تحوي مفتاح التوقيع، فاحفظها في مكان آمن.")),
    h("div", { class: "card" }, h("h3", {}, "حدود التنبيهات والتذكير"), h("div", { class: "form-grid" }, Object.values(inputs).map(([l, i]) => f(l, i))),
      h("button", { class: "btn", onclick: guardX(async () => { await api("/admin/settings", { method: "PUT", body: Object.fromEntries(Object.entries(inputs).map(([k, [, i]]) => [k, i.value])) }); toast("حُفظت الحدود"); }) }, "حفظ"))];
}

async function importView() {
  const csvTemplate = (name, cols, sample) => downloadBlob(new Blob(["﻿" + cols.join(",") + "\n" + sample + "\n"], { type: "text/csv" }), name);
  const card = (title, note, path, cols, sample, tplName) => {
    const file = h("input", { type: "file", accept: ".csv,text/csv", "aria-label": "ملف CSV" }), out = h("div");
    const run = (dry) => guardX(async () => {
      if (!file.files[0]) return toast("اختر ملف CSV أولًا", true);
      const r = await api(`${path}?dry_run=${dry ? 1 : 0}`, { method: "POST", body: file.files[0] });
      const created = r.rows.filter((x) => x.temp_password);
      out.replaceChildren(h("div", { class: "alert" }, `${dry ? "نتيجة الفحص (لم يُكتب شيء): " : "تم التنفيذ: "}منشأ ${r.created} • ${r.updated !== undefined ? "محدَّث " + r.updated : "متخطى " + r.skipped} • أخطاء ${r.errors}`),
        created.length ? h("div", { class: "alert" }, "كلمات المرور المؤقتة (تظهر مرة واحدة — سلّمها لأصحابها): ", h("div", { class: "secret" }, created.map((x) => `${x.username}: ${x.temp_password}`).join("\n"))) : null,
        r.errors || r.rows.length <= 40 ? h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["الصف", "النتيجة", "تفاصيل"].map((t) => h("th", {}, t)))),
          h("tbody", {}, r.rows.filter((x) => r.errors ? x.result === "error" : true).slice(0, 60).map((x) => h("tr", {}, h("td", {}, x.row), h("td", {}, x.result), h("td", { class: "small" }, x.error || x.reason || x.ref_no || x.username || "")))))) : null);
      if (!dry) await loadRefs();
    });
    return h("div", { class: "card" }, h("h3", {}, title), h("p", { class: "muted small" }, note), h("div", { class: "row" }, file, h("button", { class: "btn ghost", onclick: run(true) }, "فحص (تجريبي)"), h("button", { class: "btn", onclick: run(false) }, "تنفيذ الاستيراد"),
      h("button", { class: "btn ghost sm", onclick: () => csvTemplate(tplName, cols, sample) }, "تنزيل قالب CSV")), out);
  };
  return [adminHead("import", "الاستيراد والترحيل", "ابدأ دائمًا بـ«فحص (تجريبي)» لرؤية الأخطاء دون كتابة أي شيء، ثم نفّذ. الترميز UTF-8، حتى 5000 صف."),
    card("مزامنة الموظفين (من الموارد البشرية)", "يُحدَّث الموجودون حسب اسم المستخدم، ويُنشأ الجدد بكلمة مرور مؤقتة تُعرض لك مرة واحدة. الوحدات غير الموجودة تُنشأ. الأدوار: مدير النظام / مدير إدارة / مكتب الصادر والوارد / موظف.",
      "/admin/import/users", ["username", "full_name", "job_title", "phone", "email", "unit", "role", "clearance", "active"], "hr_user,اسم الموظف,محاسب,0500000000,user@org.sa,الشؤون المالية,موظف,0,1", "users-template.csv"),
    card("ترحيل المراسلات من نظام قديم", "تُنشأ بأرقام قيد جديدة ويُحفظ الرقم القديم (legacy_ref) ويُفهرس للبحث؛ الإعادة لا تكرّر ما استُورد. الاتجاه: وارد/صادر، النطاق: خارجي/داخلي، التصنيف باسمه أو مفتاحه، والحالة الافتراضية «مؤرشف».",
      "/admin/import/correspondences", ["legacy_ref", "direction", "scope", "subject", "party", "party_ref", "doc_date", "classification", "type", "folder", "unit", "status", "summary"],
      "OLD-001,وارد,خارجي,موضوع الخطاب,الجهة المرسلة,م/55,2019-05-01,عادي,طلب,أرشيف 2019,الشؤون الإدارية,منجز,ملخص", "correspondences-template.csv")];
}

Object.assign(ADMIN_VIEWS, { workflows: workflowsView, rules: rulesView, templates: templatesView, retention: retentionView, ops: opsView, import: importView });
ADMIN_TABS.splice(ADMIN_TABS.findIndex((t) => t[0] === "perms"), 0, ["workflows", "مسارات العمل"], ["rules", "قواعد التوجيه"], ["templates", "القوالب"], ["retention", "الاحتجاز"]);
ADMIN_TABS.push(["ops", "النسخ والسلامة"], ["import", "الاستيراد"]);
