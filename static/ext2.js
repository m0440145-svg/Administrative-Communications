"use strict";
// واجهة الإصدار 2.1: ملف المعاملة الموحّد، ملصق QR/باركود، الخطاب الرسمي، معاينة المرفقات بعلامة مائية، واجهة القيادة،
// الأمان (MFA)، SLA، قنوات الإشعار، التقارير المجدولة، ولجنة الوثائق. تعتمد على app.js وext.js.

const EXT_PREFIX = {};
const DOSSIER_ICON = { letter: "📄", attachment: "📎", transfer: "↪️", transfer_done: "✅", signature: "🖊️", note: "📝", stamp: "🔖", audit: "🔧" };

function svgNode(str) {
  return document.importNode(new DOMParser().parseFromString(str, "image/svg+xml").documentElement, true);
}

// ───────────── الباركود/QR: فتح معاملة بالمسح (قارئ لوحة المفاتيح) واختصارات ─────────────
function scanBox() {
  const input = h("input", { id: "scanbox", type: "search", placeholder: "امسح الباركود أو اكتب رقم القيد…", "aria-label": "فتح بالباركود", autocomplete: "off" });
  input.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter" || !input.value.trim()) return;
    e.preventDefault();
    try { const r = await api("/lookup?ref=" + encodeURIComponent(input.value.trim())); input.value = ""; location.hash = "#/c/" + r.id; }
    catch (ex) { toast(ex.message, true); input.select(); }
  });
  return h("div", { class: "scan" }, input);
}

document.addEventListener("keydown", (e) => {
  const t = e.target;
  if (typeof state === "undefined" || !state.user || e.ctrlKey || e.metaKey || e.altKey) return;
  if (t && (["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.isContentEditable)) return;
  if (e.key === "/") { const s = document.getElementById("scanbox"); if (s) { e.preventDefault(); s.focus(); } }
  else if (e.key.toLowerCase() === "n") location.hash = "#/new";
});

// ───────────── الإشعارات اللحظية (SSE) ─────────────
async function startRealtime() {
  if (startRealtime.running) return;
  startRealtime.running = true;
  while (state.user) {
    const ctl = new AbortController();
    try {
      const res = await fetch("/api/events", { headers: state.token ? { Authorization: "Bearer " + state.token } : {}, signal: ctl.signal });
      if (!res.ok || !res.body) throw new Error("no stream");
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done || !state.user) { ctl.abort(); break; }
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          if (block.includes("event: notification")) {
            const line = block.split("\n").find((l) => l.startsWith("data: "));
            try { const ev = JSON.parse(line.slice(6)); toast("🔔 " + ev.title); pollNotifications(); if (location.hash.startsWith("#/notifications") || location.hash === "#/tasks") render(); } catch { /* */ }
          }
        }
      }
    } catch { /* انقطع الاتصال: نعيد المحاولة */ }
    if (!state.user) break;
    await new Promise((r) => setTimeout(r, 5000));
  }
  startRealtime.running = false;
}

// ───────────── معاينة المرفقات: عارض داخلي + علامة مائية ديناميكية + تهميشات ─────────────
const PREVIEW_MIME = { pdf: "application/pdf", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", txt: "text/plain", csv: "text/plain", mp4: "video/mp4" };
const previewable = (name) => !!PREVIEW_MIME[(name.split(".").pop() || "").toLowerCase()];

function watermark() {
  const u = state.user, stamp = `${u.full_name} • ${u.username} • ${u.ip || ""} • ${new Date().toISOString().slice(0, 16).replace("T", " ")}`;
  const w = h("div", { class: "wm", "aria-hidden": "true" });
  for (let i = 0; i < 40; i++) w.append(h("span", {}, stamp));
  return w;
}

async function viewerNode(a) {
  const ext = (a.filename.split(".").pop() || "").toLowerCase();
  const res = await api("/attachments/" + a.id, { raw: true });
  const blob = new Blob([await res.arrayBuffer()], { type: PREVIEW_MIME[ext] });
  const url = URL.createObjectURL(blob);
  let el;
  if (ext === "pdf") el = h("iframe", { src: url, title: a.filename, class: "vframe" });
  else if (["png", "jpg", "jpeg", "gif"].includes(ext)) el = h("img", { src: url, alt: a.filename, class: "vimg" });
  else if (ext === "mp4") el = h("video", { src: url, controls: true, class: "vimg" });
  else el = h("pre", { class: "vtext" }, await blob.text());
  setTimeout(() => URL.revokeObjectURL(url), 600000);
  return h("div", { class: "viewer" }, el, watermark());
}

async function previewAttachment(a, c) {
  const body = h("div", { class: "modal-body" }, h("div", { class: "muted" }, "جارٍ التحميل…"));
  const close = () => overlay.remove();
  const mine = (c.notes || []).filter((n) => n.att_id === a.id);
  const noteIn = h("input", { placeholder: "تهميش على هذا الملف…", maxlength: 500, "aria-label": "تهميش" });
  const stampSel = sel("stamp", { "": "— ختم —", "اطّلع": "اطّلع", "معتمد": "معتمد", "للمتابعة": "للمتابعة", "للحفظ": "للحفظ", "مؤجَّل": "مؤجَّل", "يُعاد للدراسة": "يُعاد للدراسة" }, "", { "aria-label": "ختم" });
  const post = async (kind, text) => { try { await api(`/correspondences/${c.id}/notes`, { method: "POST", body: { kind, text, att_id: a.id } }); toast("أُضيف على طبقة التهميش (الأصل لا يتغير)"); close(); render(); } catch (e) { toast(e.message, true); } };
  const overlay = h("div", { class: "modal", role: "dialog", "aria-label": "معاينة " + a.filename },
    h("div", { class: "modal-card" }, h("div", { class: "row" }, h("strong", {}, a.filename), h("span", { class: "small muted" }, "علامة مائية باسمك وعنوانك وتوقيتك لردع التصوير"), h("button", { class: "btn ghost sm", onclick: close }, "إغلاق ✕")),
      body, h("div", { class: "row" }, noteIn, h("button", { class: "btn sm", onclick: () => noteIn.value.trim() && post("note", noteIn.value.trim()) }, "إضافة تهميش"), stampSel,
        h("button", { class: "btn ghost sm", onclick: () => stampSel.value && post("stamp", stampSel.value) }, "وضع ختم")),
      mine.length ? h("ul", { class: "tl" }, mine.map((n) => h("li", {}, (n.kind === "stamp" ? "🔖 " : "📝 ") + n.text, h("span", { class: "small muted" }, ` — ${n.author} ${fmtDT(n.created_at)}`)))) : null));
  document.body.append(overlay);
  try { body.replaceChildren(await viewerNode(a)); } catch (e) { body.replaceChildren(h("div", { class: "alert" }, e.message)); }
}

// ───────────── أدوات صفحة التفاصيل ─────────────
function detailTools(c) {
  const split = (() => { try { return localStorage.getItem("acp_split") === "1"; } catch { return false; } })();
  return [
    h("button", { class: "btn ghost sm", onclick: async () => { try { await api(`/correspondences/${c.id}/pin`, { method: "POST", body: { pinned: !c.pinned } }); render(); } catch (e) { toast(e.message, true); } } }, c.pinned ? "★ مثبّتة" : "☆ تثبيت"),
    h("a", { class: "btn ghost sm", href: "#/dossier/" + c.id }, "📂 الملف الموحّد"),
    h("a", { class: "btn ghost sm", href: "#/label/" + c.id }, "🏷 ملصق QR"),
    h("a", { class: "btn ghost sm", href: "#/label/" + c.id + "?print=1", title: "طباعة باركود المعاملة مباشرة" }, "🖨 طباعة الباركود"),
    c.direction === "outgoing" ? h("a", { class: "btn ghost sm", href: "#/letter/" + c.id }, "📄 الخطاب الرسمي") : null,
    h("button", { class: "btn ghost sm", onclick: () => { try { localStorage.setItem("acp_split", split ? "0" : "1"); } catch { /* */ } render(); } }, split ? "▣ إلغاء العرض المقسّم" : "◫ عرض مقسّم"),
  ];
}

async function detailExtras2(c) {
  const cards = [];
  const split = (() => { try { return localStorage.getItem("acp_split") === "1"; } catch { return false; } })();
  if (split) {
    const first = c.attachments.find((a) => previewable(a.filename));
    cards.push(h("div", { class: "splitflag card" }, h("h3", {}, "معاينة الوثيقة"), first ? await viewerNode(first).catch((e) => h("div", { class: "alert" }, e.message)) : h("div", { class: "muted" }, "لا مرفق قابل للمعاينة")));
  }
  // ملاحظات وأختام (طبقة منفصلة)
  const noteIn = h("input", { placeholder: "أضف ملاحظة على المعاملة…", maxlength: 1000, "aria-label": "ملاحظة" });
  const stampSel = sel("stamp", { "": "— ختم —", "اطّلع": "اطّلع", "معتمد": "معتمد", "للمتابعة": "للمتابعة", "للحفظ": "للحفظ", "مؤجَّل": "مؤجَّل", "يُعاد للدراسة": "يُعاد للدراسة" }, "", { "aria-label": "ختم" });
  const add = guardX(async (kind, text) => { await api(`/correspondences/${c.id}/notes`, { method: "POST", body: { kind, text } }); render(); });
  cards.push(h("div", { class: "card" }, h("h3", {}, `الملاحظات والأختام (${c.notes.length})`),
    c.notes.length ? h("ul", { class: "tl" }, c.notes.map((n) => h("li", {}, h("span", { class: n.kind === "stamp" ? "stampchip" : "" }, (n.kind === "stamp" ? "🔖 " : "📝 ") + n.text),
      h("div", { class: "small muted" }, `${n.author} — ${fmtDT(n.created_at)}`, n.att_id ? " — على مرفق" : "", (n.user_id === state.user.id || state.user.role === "admin") ? h("button", { class: "x", title: "حذف", onclick: guardX(async () => { await api("/notes/" + n.id, { method: "DELETE" }); render(); }) }, " ×") : null)))) : h("div", { class: "muted" }, "لا ملاحظات"),
    h("div", { class: "row" }, noteIn, h("button", { class: "btn sm", onclick: () => noteIn.value.trim() && add("note", noteIn.value.trim()) }, "إضافة"),
      c.can_manage ? [stampSel, h("button", { class: "btn ghost sm", onclick: () => stampSel.value && add("stamp", stampSel.value) }, "ختم")] : null),
    h("div", { class: "small muted" }, "التهميشات والأختام على طبقة منفصلة ولا تُغيّر الوثيقة الأصلية ولا بصمتها.")));
  // مراسلات مرتبطة
  const refIn = h("input", { placeholder: "رقم قيد لربطه", dir: "ltr", "aria-label": "رقم القيد" });
  cards.push(h("div", { class: "card" }, h("h3", {}, "مراسلات مرتبطة"),
    c.related.length ? h("ul", { class: "tl" }, c.related.map((r) => h("li", {}, h("a", { href: "#/c/" + r.id, class: "ltr" }, r.ref_no), " — " + r.subject, " ", statusPill(r.status),
      h("button", { class: "x", title: "فك الربط", onclick: guardX(async () => { await api(`/correspondences/${c.id}/related/${r.id}`, { method: "DELETE" }); render(); }) }, " ×")))) : h("div", { class: "muted" }, "لا روابط"),
    h("div", { class: "row" }, refIn, h("button", { class: "btn sm", onclick: guardX(async () => { await api(`/correspondences/${c.id}/related`, { method: "POST", body: { ref_no: refIn.value.trim() } }); toast("تم الربط"); render(); }) }, "ربط"))));
  // إرسال الصادر بالبريد وتتبع الحالة
  if (c.direction === "outgoing" && c.can_manage && c.conf_level === 0) {
    const deliveries = await api(`/correspondences/${c.id}/deliveries`).catch(() => []);
    const to = h("input", { type: "email", placeholder: "بريد الجهة المرسل إليها", dir: "ltr", "aria-label": "البريد" });
    const msg = h("input", { placeholder: "نص مرافق (اختياري)", maxlength: 1000, "aria-label": "الرسالة" });
    const picks = c.attachments.map((a) => ({ a, cb: h("input", { type: "checkbox", checked: true }) }));
    cards.push(h("div", { class: "card" }, h("h3", {}, "إرسال بالبريد الإلكتروني"),
      deliveries.length ? h("table", {}, h("tbody", {}, deliveries.map((d) => h("tr", {}, h("td", { class: "ltr" }, d.to_addr), h("td", {}, d.status === "sent" ? pill("closed", "✔ قبِله خادم البريد") : d.status === "queued" ? pill("in_progress", "في الانتظار") : pill("urgent", "فشل")),
        h("td", { class: "small muted" }, fmtDT(d.sent_at || d.created_at), d.last_error ? " — " + d.last_error : ""))))) : null,
      h("div", { class: "row" }, to, msg, h("button", { class: "btn sm", onclick: guardX(async () => {
        const r = await api(`/correspondences/${c.id}/send-email`, { method: "POST", body: { to: to.value.trim(), message: msg.value, att_ids: picks.filter((p) => p.cb.checked).map((p) => p.a.id) } });
        toast(r.status === "sent" ? "أُرسل" : "حالة الإرسال: " + r.status, r.status !== "sent"); render();
      }) }, "إرسال")),
      picks.length ? h("div", { class: "row small" }, "المرفقات: ", picks.map((p) => h("label", { class: "opt" }, p.cb, p.a.filename))) : null,
      h("div", { class: "small muted" }, "حالة «قبِله خادم البريد» تعني قبول الرسالة للتسليم، ولا تثبت قراءة المستلم لها. المراسلات السرية لا تُرسل بالبريد.")));
  }
  return cards;
}

// ───────────── ملف المعاملة الموحّد ─────────────
EXT_PREFIX["/dossier/"] = async (id) => {
  const d = await api(`/correspondences/${id}/dossier`);
  return [h("div", { class: "row" }, h("h2", {}, "الملف الموحّد للمعاملة"), h("a", { class: "btn ghost sm", href: "#/c/" + id }, "← المراسلة"), h("button", { class: "btn ghost sm", onclick: () => window.print() }, "طباعة")),
    h("p", { class: "muted small" }, "المعاملة الأصلية مع ردودها والمراسلات المرتبطة ومرفقاتها وإحالاتها وتوقيعاتها وملاحظاتها في خط زمني واحد."),
    h("div", { class: "card" }, h("h3", {}, `مراسلات الملف (${d.letters.length})`), h("table", {}, h("tbody", {}, d.letters.map((l) => h("tr", { class: l.id === d.current ? "cur" : "" },
      h("td", {}, pill(l.relation === "الأصل" ? "new" : l.relation === "مرتبطة" ? "archived" : "in_progress", l.relation)), h("td", { class: "ltr" }, h("a", { href: "#/c/" + l.id }, l.ref_no)),
      h("td", {}, l.subject), h("td", {}, l.direction === "incoming" ? "وارد" : "صادر", " ", L.scope[l.scope]), h("td", {}, statusPill(l.status)), h("td", { class: "small muted" }, l.party)))))),
    h("div", { class: "card" }, h("h3", {}, "الخط الزمني"), h("ul", { class: "tl dossier" }, d.events.map((e) => h("li", {},
      h("span", { class: "di" }, DOSSIER_ICON[e.type] || "•"), h("strong", {}, e.title), " ", h("a", { href: "#/c/" + e.corr_id, class: "ltr small" }, e.ref),
      e.detail ? h("div", { class: "small" }, e.detail) : null, h("div", { class: "small muted" }, fmtDT(e.at))))))];
};

// ───────────── ملصق الباركود وQR ─────────────
EXT_PREFIX["/label/"] = async (id) => {
  const l = await api(`/correspondences/${id}/label`);
  return [h("div", { class: "row noprint" }, h("h2", {}, "ملصق المعاملة"), h("a", { class: "btn ghost sm", href: "#/c/" + id }, "← المراسلة"),
    h("button", { class: "btn", onclick: () => window.print() }, "🖨 طباعة الملصق")),
    h("p", { class: "muted small noprint" }, "مقاس الملصق الحراري 60×40 مم. يُمسح الـQR أو الباركود بأي قارئ لفتح المعاملة من خانة «فتح بالباركود» (أو اضغط /)."),
    h("div", { class: "label-sheet" }, h("div", { class: "lb-top" }, svgNode(l.qr_svg), h("div", { class: "lb-meta" }, h("div", { class: "lb-ref ltr" }, l.ref_no), h("div", {}, l.date), h("div", {}, l.direction === "incoming" ? "وارد" : "صادر", " • مرفقات: " + l.att_count))),
      h("div", { class: "lb-bar" }, svgNode(l.barcode_svg)), h("div", { class: "lb-subj" }, l.subject.slice(0, 60)))];
};

// ───────────── الخطاب الرسمي بالترويسة ─────────────
EXT_PREFIX["/letter/"] = async (id) => {
  const l = await api(`/correspondences/${id}/letter`);
  return [h("div", { class: "row noprint" }, h("h2", {}, "الخطاب الرسمي"), h("a", { class: "btn ghost sm", href: "#/c/" + id }, "← المراسلة"),
    h("button", { class: "btn", onclick: () => window.print() }, "🖨 طباعة / حفظ PDF"), h("span", { class: "small muted" }, "الترويسة والتذييل من «الهوية والترويسة» في إعدادات النظام")),
    h("div", { class: "letter" },
      h("div", { class: "lt-head" }, l.has_logo ? h("img", { class: "lt-logo", src: "/api/public/logo?v=" + encodeURIComponent(l.logo_v || ""), alt: "" }) : null, h("div", {}, h("div", { class: "lt-org" }, l.org_name || "اسم الجهة"), l.org_header ? h("div", { class: "small" }, l.org_header) : null),
        h("div", { class: "lt-meta" }, h("div", {}, "الرقم: ", h("span", { class: "ltr" }, l.ref_no)), h("div", {}, "التاريخ: " + l.doc_date), l.ref_to ? h("div", {}, "إشارة إلى: ", h("span", { class: "ltr" }, l.ref_to.ref_no)) : null)),
      h("div", { class: "lt-to" }, "السيد/ ", h("strong", {}, l.party), " المحترم"),
      h("div", { class: "lt-subj" }, "الموضوع: ", l.subject),
      h("div", { class: "lt-body" }, l.body || "…"),
      h("div", { class: "lt-sign" }, l.signatures.length ? l.signatures.map((s) => h("div", { class: "lt-sig" }, h("div", {}, "✓ اعتُمد إلكترونيًا"), h("strong", {}, s.full_name), s.job_title ? h("div", { class: "small" }, s.job_title) : null,
        s.on_behalf_of ? h("div", { class: "small" }, "بتفويض عن: " + s.on_behalf_of) : null, h("div", { class: "small muted" }, s.signed_at.slice(0, 16)))) : h("div", { class: "muted small" }, "(لم يُعتمد بعد)"),
        l.org_stamp ? h("div", { class: "lt-stamp" }, l.org_stamp) : null, svgNode(l.qr_svg)),
      l.org_footer || l.org_contact ? h("div", { class: "lt-foot" }, l.org_footer, l.org_footer && l.org_contact ? h("br") : null, l.org_contact) : null)];
};

// ───────────── واجهة القيادة: اعتماد سريع + مؤشرات ─────────────
EXT_ROUTES["/executive"] = async () => {
  const [tasks, k, mfa, sla] = await Promise.all([api("/tasks"), api("/kpi"), api("/me/mfa").catch(() => ({})), api("/correspondences?status=awaiting_signature&size=20")]);
  const approvals = tasks.filter((t) => t.kind === "step" && t.step_kind === "approve");
  const quick = guardX(async (t, decision) => {
    const pw = prompt(`تأكيد ${decision === "approve" ? "الاعتماد والتوقيع" : "الرفض"} — أدخل كلمة مرورك:`);
    if (!pw) return;
    let comment = null, otp = null;
    if (decision === "reject") { comment = prompt("سبب الرفض (إلزامي):"); if (!comment) return; }
    if (mfa.enabled) { otp = prompt("رمز المصادقة الثنائية (6 أرقام):"); if (!otp) return; }
    await api(`/wf/steps/${t.step_id}/decide`, { method: "POST", body: { decision, password: pw, comment, otp } });
    toast(decision === "approve" ? "تم الاعتماد والتوقيع" : "تم الرفض"); render();
  });
  const stat = (n, l, href, bad) => h("a", { class: "card stat" + (bad && n ? " warn" : ""), href: href || "#" }, h("div", { class: "n" }, n ?? "—"), h("div", { class: "l" }, l));
  return [h("h2", {}, "واجهة القيادة"), h("p", { class: "muted small" }, "شاشة مبسّطة للاعتماد والتوقيع السريع ومتابعة الالتزام."),
    h("div", { class: "grid g4" }, stat(approvals.length, "بانتظار اعتمادك", "#/tasks"), stat(k.sla_breached_open, "تجاوزت مهلة SLA", "#/list?overdue=1", true), stat(k.overdue, "متأخرة عن موعدها", "#/list?overdue=1", true),
      stat(k.sla_policy_compliance_pct != null ? k.sla_policy_compliance_pct + "٪" : k.sla_compliance_pct != null ? k.sla_compliance_pct + "٪" : "—", "الالتزام بالمهل")),
    h("div", { class: "card" }, h("h3", {}, "اعتماد بنقرة"), approvals.length ? approvals.map((t) => h("div", { class: "exec-row" },
      h("div", {}, h("a", { href: "#/c/" + t.id, class: "ltr" }, t.ref_no), " ", h("strong", {}, t.subject), t.priority !== "normal" ? [" ", prioPill(t.priority)] : null, h("div", { class: "small muted" }, `${t.instruction} — من ${t.from_name}`)),
      h("div", { class: "row" }, h("button", { class: "btn", onclick: () => quick(t, "approve") }, "✔ اعتماد"), h("button", { class: "btn danger", onclick: () => quick(t, "reject") }, "✖ رفض"), h("a", { class: "btn ghost sm", href: "#/c/" + t.id }, "عرض")))) : h("div", { class: "muted" }, "لا اعتمادات معلّقة 🎉")),
    sla.items.length ? h("div", { class: "card" }, h("h3", {}, "بانتظار التوقيع في المؤسسة"), corrTable(sla.items, true)) : null];
};

// ───────────── الأمان والتفضيلات للمستخدم (MFA + قنوات الإشعار) ─────────────
EXT_ROUTES["/security"] = async () => {
  const [mfa, prefs] = await Promise.all([api("/me/mfa"), api("/me/prefs")]);
  const box = h("div");
  const codeIn = h("input", { placeholder: "رمز 6 أرقام", inputmode: "numeric", maxlength: 6, "aria-label": "الرمز" });
  const startSetup = guardX(async () => {
    const s = await api("/me/mfa/setup", { method: "POST", body: {} });
    box.replaceChildren(h("p", {}, "امسح الرمز بتطبيق المصادقة (Google/Microsoft Authenticator…) أو أدخل المفتاح يدويًا ثم اكتب الرمز الظاهر:"), s.qr_svg ? svgNode(s.qr_svg) : null,
      h("div", { class: "secret" }, s.secret), h("div", { class: "row" }, codeIn, h("button", { class: "btn", onclick: guardX(async () => {
        const r = await api("/me/mfa/enable", { method: "POST", body: { code: codeIn.value.trim() } });
        box.replaceChildren(h("div", { class: "alert" }, "فُعّلت المصادقة الثنائية. احفظ رموز الاسترداد هذه الآن (تُعرض مرة واحدة، كل رمز يعمل مرة):", h("div", { class: "secret" }, r.recovery_codes.join("\n"))));
      }) }, "تفعيل")));
  });
  const pwIn = h("input", { type: "password", placeholder: "كلمة المرور", autocomplete: "current-password", "aria-label": "كلمة المرور" });
  const dis = h("input", { placeholder: "رمز التطبيق أو رمز استرداد", "aria-label": "الرمز" });
  const em = h("input", { type: "checkbox", checked: !!prefs.email_on }), sm = h("input", { type: "checkbox", checked: !!prefs.sms_on });
  return [h("h2", {}, "الأمان والتفضيلات"),
    h("div", { class: "card" }, h("h3", {}, "المصادقة الثنائية (TOTP)"),
      h("div", { class: "row" }, mfa.enabled ? pill("closed", "✔ مفعّلة") : pill("urgent", "غير مفعّلة"), mfa.required && !mfa.enabled ? h("span", { class: "alert" }, "الإدارة تُلزم دورك بتفعيلها") : null, mfa.enabled ? h("span", { class: "small muted" }, `رموز استرداد متبقية: ${mfa.recovery_left}`) : null),
      mfa.enabled ? h("div", { class: "row" }, pwIn, dis, h("button", { class: "btn danger sm", onclick: guardX(async () => { await api("/me/mfa/disable", { method: "POST", body: { password: pwIn.value, code: dis.value.trim() } }); toast("أُوقفت"); render(); }) }, "إيقاف"))
        : h("button", { class: "btn", onclick: startSetup }, "إعداد المصادقة الثنائية"), box,
      h("div", { class: "small muted" }, "عند التفعيل يُطلب الرمز عند الدخول وعند التوقيع الإلكتروني والإجراءات الحساسة.")),
    h("div", { class: "card" }, h("h3", {}, "قنوات الإشعار"), h("p", { class: "small muted" }, "الإشعارات داخل المنصة دائمة. اختر ما يصلك خارجها (يتطلب أن يكون بريدك/جوالك مسجَّلًا لدى المدير):"),
      h("div", { class: "row" }, h("label", { class: "opt" }, em, "بريد إلكتروني (الإسناد، الاستحقاق، الاعتماد…)"), h("label", { class: "opt" }, sm, "رسالة نصية (تجاوز المهل والتأخر فقط)"),
        h("button", { class: "btn sm", onclick: guardX(async () => { await api("/me/prefs", { method: "PUT", body: { theme: prefs.theme, page_size: prefs.page_size, email_on: em.checked, sms_on: sm.checked } }); toast("حُفظت التفضيلات"); }) }, "حفظ"))),
    h("div", { class: "card" }, h("h3", {}, "اختصارات لوحة المفاتيح"), h("ul", {}, h("li", {}, h("kbd", {}, "N"), " معاملة جديدة"), h("li", {}, h("kbd", {}, "/"), " الانتقال إلى خانة المسح/رقم القيد (يعمل مع قارئ الباركود)")))];
};

// ───────────── صفحات إدارة 2.1 ─────────────
async function slaView() {
  const [cfg, ] = await Promise.all([api("/admin/sla")]);
  const rows = state.cls.map((c) => ({ c, inp: h("input", { type: "number", min: 0, step: "any", value: cfg.policies[c.key] ?? "", placeholder: "بلا مهلة", class: "narrow", "aria-label": "ساعات " + c.label }) }));
  const days = h("input", { value: cfg.work_days, "aria-label": "أيام العمل", class: "ltr" }), s0 = h("input", { type: "time", value: cfg.work_start }), s1 = h("input", { type: "time", value: cfg.work_end });
  return [adminHead("sla", "اتفاقيات مستوى الخدمة (SLA) ومصفوفة التصعيد", "لكل تصنيف مهلة بـ«ساعات العمل» تبدأ من تسجيل المراسلة. عند 75٪ من المهلة يُنبَّه الموظف المُسنَد إليه، وعند تجاوزها يُصعَّد للمدير المباشر (مديري الوحدات المسؤولة) ومدير النظام ويُلوَّن الصف بالأحمر في القائمة. المهلة الفارغة = بلا SLA."),
    h("div", { class: "card" }, h("h3", {}, "المهلة بالساعات لكل تصنيف"), h("table", {}, h("tbody", {}, rows.map((r) => h("tr", {}, h("td", {}, prioPill(r.c.key)), h("td", {}, r.inp, " ساعة عمل"))))),
      h("h3", {}, "ساعات العمل"), h("div", { class: "row" }, h("label", { class: "inl" }, "أيام العمل (0=الاثنين…6=الأحد)", days), h("label", { class: "inl" }, "من", s0), h("label", { class: "inl" }, "إلى", s1)),
      h("div", { class: "small muted" }, "الافتراضي: الأحد–الخميس (6,0,1,2,3) من 08:00 إلى 16:00. يسري على المراسلات الجديدة."),
      h("button", { class: "btn", onclick: guardX(async () => {
        await api("/admin/sla", { method: "PUT", body: { policies: Object.fromEntries(rows.map((r) => [r.c.key, r.inp.value === "" ? null : Number(r.inp.value)])), work_days: days.value.trim(), work_start: s0.value, work_end: s1.value } });
        toast("حُفظت السياسات"); render();
      }) }, "حفظ")),
    h("div", { class: "card" }, h("button", { class: "btn ghost", onclick: guardX(async () => { const r = await api("/admin/reminders/run", { method: "POST", body: {} }); toast(`تصعيد SLA: ${JSON.stringify(r.extra.sla_escalation || {})}`); }) }, "تشغيل التصعيد الآن (يعمل تلقائيًا كل 10 دقائق)"))];
}

async function channelsView() {
  const [ch, box] = await Promise.all([api("/admin/channels"), api("/admin/outbox")]);
  const dot = (ok) => (ok ? pill("closed", "✔ مُهيّأ") : pill("archived", "غير مُهيّأ"));
  return [adminHead("channels", "قنوات الإشعار والصندوق الصادر", "الإشعارات داخل المنصة ولحظية (SSE). القنوات الخارجية تُهيَّأ بمتغيرات البيئة عند تشغيل الخادم: البريد (ACP_SMTP_*)، الرسائل النصية عبر بوابة HTTP (ACP_SMS_URL / ACP_SMS_TOKEN)، واستقبال البريد الوارد (ACP_IMAP_*)."),
    h("div", { class: "grid g4" }, h("div", { class: "card" }, h("h3", {}, "البريد SMTP"), dot(ch.email)), h("div", { class: "card" }, h("h3", {}, "SMS"), dot(ch.sms)), h("div", { class: "card" }, h("h3", {}, "استقبال IMAP"), dot(ch.imap)),
      h("div", { class: "card" }, h("h3", {}, "الصندوق"), h("div", {}, `بالانتظار ${ch.queued} • فشلت ${ch.failed}`), h("button", { class: "btn sm", onclick: guardX(async () => { const r = await api("/admin/outbox/flush", { method: "POST", body: {} }); toast(`أُرسل ${r.sent} • إعادة ${r.retry} • فشل ${r.failed}`); render(); }) }, "إرسال الآن"))),
    h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["#", "القناة", "إلى", "الموضوع", "الحالة", "المحاولات", "خطأ", "الوقت"].map((t) => h("th", {}, t)))),
      h("tbody", {}, box.map((r) => h("tr", {}, h("td", {}, r.id), h("td", {}, r.kind === "email" ? "بريد" : "SMS"), h("td", { class: "ltr small" }, r.to_addr), h("td", { class: "small" }, r.subject || ""),
        h("td", {}, r.status === "sent" ? pill("closed", "أُرسل") : r.status === "queued" ? pill("in_progress", "انتظار") : pill("urgent", "فشل")), h("td", {}, r.attempts), h("td", { class: "small bad" }, r.last_error || ""), h("td", { class: "small muted ltr" }, fmtDT(r.sent_at || r.created_at)))))))];
}

async function schedulesView() {
  const [list, runs] = await Promise.all([api("/admin/report-schedules"), api("/admin/report-runs")]);
  const people = activeUsers().filter((u) => ["admin", "manager", "clerk"].includes(u.role));
  const nm = h("input", { placeholder: "اسم التقرير", maxlength: 80, "aria-label": "اسم التقرير" });
  const wd = sel("wd", { 6: "الأحد", 0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء", 3: "الخميس", 4: "الجمعة", 5: "السبت" }, 6, { "aria-label": "اليوم" });
  const hr = h("input", { type: "number", min: 0, max: 23, value: 8, class: "narrow", "aria-label": "الساعة" });
  const who = people.map((u) => ({ u, cb: h("input", { type: "checkbox" }) }));
  const dayNo = (n) => ((Number(n) + 1) % 7);  // الأحد=0 في الخادم
  const names = { 0: "الأحد", 1: "الاثنين", 2: "الثلاثاء", 3: "الأربعاء", 4: "الخميس", 5: "الجمعة", 6: "السبت" };
  return [adminHead("schedules", "التقارير المجدولة", "يُنشئ النظام تقرير مؤشرات الأداء (Excel) في اليوم والساعة المحددين، ويُشعر المستلمين داخل المنصة، ويرسله بالبريد إن كان SMTP مُهيّأً وكان للمستلم بريد."),
    h("div", { class: "card" }, h("h3", {}, "جدولة جديدة"), h("div", { class: "row" }, nm, h("label", { class: "inl" }, "كل", wd), h("label", { class: "inl" }, "الساعة", hr)),
      h("div", { class: "row" }, "المستلمون: ", who.map((w) => h("label", { class: "opt" }, w.cb, w.u.full_name))),
      h("button", { class: "btn", onclick: guardX(async () => {
        await api("/admin/report-schedules", { method: "POST", body: { name: nm.value, weekday: ({ 6: 0, 0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6 })[wd.value], hour: Number(hr.value), recipients: who.filter((w) => w.cb.checked).map((w) => w.u.id) } }); toast("أُضيفت"); render();
      }) }, "إضافة")),
    list.length ? h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["التقرير", "الموعد", "المستلمون", "آخر تشغيل", "نشط", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, list.map((s) => h("tr", {}, h("td", {}, s.name), h("td", {}, `${names[s.weekday]} ${String(s.hour).padStart(2, "0")}:00`), h("td", { class: "small" }, s.recipients.map((i) => (state.users.find((u) => u.id === i) || {}).full_name || i).join("، ")),
        h("td", { class: "small muted" }, s.last_run || "—"), h("td", {}, s.active ? "✔" : "—"),
        h("td", { class: "row" }, h("button", { class: "btn sm", onclick: guardX(async () => { await api(`/admin/report-schedules/${s.id}/run`, { method: "POST", body: {} }); toast("أُنشئ التقرير"); render(); }) }, "تشغيل الآن"),
          h("button", { class: "btn ghost sm", onclick: guardX(async () => { await api("/admin/report-schedules/" + s.id, { method: "PUT", body: { active: !s.active } }); render(); }) }, s.active ? "إيقاف" : "تفعيل"),
          h("button", { class: "btn danger sm", onclick: guardX(async () => { if (confirm("حذف الجدولة؟")) { await api("/admin/report-schedules/" + s.id, { method: "DELETE" }); render(); } }) }, "حذف"))))))) : null,
    h("div", { class: "card" }, h("h3", {}, "التقارير المُنشأة"), runs.length ? h("table", {}, h("tbody", {}, runs.slice(0, 20).map((r) => h("tr", {}, h("td", {}, r.name), h("td", { class: "small muted ltr" }, fmtDT(r.created_at)),
      h("td", {}, h("button", { class: "btn ghost sm", onclick: guardX(async () => downloadBlob(await (await api(`/admin/report-runs/${r.id}/download`, { raw: true })).blob(), r.filename)) }, "تنزيل Excel")))))) : h("div", { class: "muted" }, "لا تقارير بعد"))];
}

async function securityAdminView() {
  const [st, sys, users] = await Promise.all([api("/admin/settings"), api("/admin/system"), api("/users")]);
  const f1 = h("input", { type: "checkbox", checked: st.abac_strict_secret === "1" });
  const roles = h("input", { value: st.mfa_required_roles, class: "ltr", "aria-label": "الأدوار" });
  const appr = h("input", { type: "number", min: 0, max: 20, value: st.disposal_approvals, class: "narrow", "aria-label": "عدد الاعتمادات" });
  const cold = h("input", { type: "number", min: 0, max: 3650, value: st.cold_after_days, class: "narrow", "aria-label": "أيام" });
  const sigma = h("input", { type: "number", min: 1, max: 5, step: "any", value: st.anomaly_sigma, class: "narrow", "aria-label": "σ" });
  const mfaUsers = users.filter((u) => u.active);
  return [adminHead("security", "سياسات الأمان والحوكمة", "إعدادات تُفعَّل فورًا. كلها اختيارية ومُعطَّلة افتراضيًا ما لم يُذكر خلاف ذلك."),
    h("div", { class: "card" }, h("h3", {}, "التحكم بالوصول بالسمات (ABAC)"), h("label", { class: "opt" }, f1, "صارم: المراسلات المصنَّفة «سري للغاية» (درجة 2) لا يراها مدير النظام الشامل إلا إن كان طرفًا فيها (مُنشئًا/مُسنَدًا/مُحالًا إليه)")),
    h("div", { class: "card" }, h("h3", {}, "المصادقة الثنائية"), h("div", { class: "row" }, h("label", { class: "inl" }, "أدوار يُلزمها النظام بالتفعيل (تنبيه في صفحة الأمان)", roles), h("span", { class: "small muted" }, `مفعّلة حاليًا لدى ${sys.mfa_users} مستخدم`)),
      h("details", {}, h("summary", {}, "إعادة ضبط مصادقة موظف (فقد جهازه)"), h("div", { class: "row" }, mfaUsers.map((u) => h("button", { class: "btn ghost sm", onclick: guardX(async () => { if (confirm("إعادة ضبط المصادقة الثنائية لـ" + u.full_name + "؟")) { await api(`/admin/users/${u.id}/mfa/reset`, { method: "POST", body: {} }); toast("أُعيد الضبط"); } }) }, u.full_name))))),
    h("div", { class: "card" }, h("h3", {}, "الاحتجاز والأرشيف"), h("div", { class: "row" }, h("label", { class: "inl" }, "اعتمادات لجنة الوثائق المطلوبة قبل الإتلاف (0 = الإتلاف المباشر بكلمة المرور)", appr),
      h("label", { class: "inl" }, "نقل مرفقات المؤرشف للأرشيف الوسيط (البارد) بعد (يوم، 0 = معطّل)", cold),
      h("span", { class: "small muted" }, `ملفات في البارد: ${sys.cold_files}`)), h("button", { class: "btn ghost sm", onclick: guardX(async () => { const r = await api("/admin/cold/run", { method: "POST", body: {} }); toast(`نُقل ${r.moved} ملفًا`); render(); }) }, "نقل الآن")),
    h("div", { class: "card" }, h("h3", {}, "كشف الانحراف الإحصائي"), h("label", { class: "inl" }, "الحساسية: عدد الانحرافات المعيارية فوق المتوسط", sigma)),
    h("button", { class: "btn", onclick: guardX(async () => {
      await api("/admin/settings", { method: "PUT", body: { abac_strict_secret: f1.checked ? "1" : "0", mfa_required_roles: roles.value, disposal_approvals: String(appr.value), cold_after_days: String(cold.value), anomaly_sigma: String(sigma.value) } });
      toast("حُفظت السياسات"); render();
    }) }, "حفظ السياسات")];
}

async function brandingView() {
  const st = await api("/admin/settings");
  const f = (key, label, textarea) => { const el = textarea ? h("textarea", { maxlength: 300, "aria-label": label }) : h("input", { maxlength: 300, "aria-label": label }); el.value = st[key] || ""; return [key, label, el]; };
  const fields = [f("org_name", "اسم الجهة"), f("org_header", "نص الترويسة (تحت الاسم)", true), f("org_footer", "التذييل (عنوان/هاتف/موقع)", true), f("org_stamp", "نص الختم الرسمي", false)];
  return [adminHead("branding", "الهوية والترويسة", "تظهر في «الخطاب الرسمي» المطبوع (ترويسة، ختم، تذييل، رمز QR للتحقق)."),
    h("div", { class: "card" }, h("div", { class: "form-grid" }, fields.map(([k, l, el]) => f_(l, el))),
      h("button", { class: "btn", onclick: guardX(async () => { await api("/admin/settings", { method: "PUT", body: Object.fromEntries(fields.map(([k, , el]) => [k, el.value])) }); toast("حُفظت الهوية"); }) }, "حفظ")),
    h("div", { class: "card" }, h("h3", {}, "توثيق واجهة API"), h("p", { class: "small muted" }, "وثيقة OpenAPI مولَّدة آليًا من المسارات الفعلية، ومصادقة OAuth2 (client-credentials) بمفتاح API لإصدار JWT."),
      h("a", { class: "btn ghost", href: "/apidocs.html", target: "_blank" }, "فتح توثيق API"), " ", h("a", { class: "btn ghost", href: "/api/openapi.json", target: "_blank" }, "openapi.json"))];
}
const f_ = (label, input) => f(label, input);

Object.assign(ADMIN_VIEWS, { sla: slaView, channels: channelsView, schedules: schedulesView, security: securityAdminView, branding: brandingView });
ADMIN_TABS.splice(ADMIN_TABS.findIndex((t) => t[0] === "retention") + 1, 0, ["sla", "SLA"], ["channels", "قنوات الإشعار"], ["schedules", "التقارير المجدولة"], ["security", "سياسات الأمان"], ["branding", "الهوية والترويسة"]);

// ───────────── لجنة الوثائق (تُضاف لصفحة الاحتجاز) ─────────────
async function committeeCards() {
  const [st, batches, cands] = await Promise.all([api("/admin/settings"), api("/admin/disposal-batches").catch(() => []), api("/admin/retention/candidates")]);
  const need = Number(st.disposal_approvals);
  if (!need && !batches.length) return h("div", { class: "card muted small" }, "إتلاف عبر لجنة الوثائق معطّل (اعتمادات = 0). فعّله من «سياسات الأمان» لفرض الاعتماد بفصل المهام.");
  const dispose = cands.filter((c) => c.action === "dispose");
  const checks = new Map();
  const create = guardX(async () => {
    const ids = [...checks].filter(([, el]) => el.checked).map(([id]) => id);
    if (!ids.length) return toast("حدّد مراسلات", true);
    const reason = prompt("سبب الإتلاف (إلزامي):"); if (!reason) return;
    await api("/admin/disposal-batches", { method: "POST", body: { ids, reason } }); toast("أُنشئت الدفعة وأُشعر أعضاء اللجنة"); render();
  });
  const act = (b, kind) => guardX(async () => {
    const pw = prompt("كلمة المرور للتأكيد:"); if (!pw) return;
    const otp = state.user.mfa_enabled ? prompt("رمز المصادقة الثنائية:") : null;
    if (kind === "execute") { const r = await api(`/admin/disposal-batches/${b.id}/execute`, { method: "POST", body: { password: pw, otp } }); toast(`أُتلفت ${r.done} • استُثني ${r.skipped}`); }
    else { const comment = kind === "reject" ? prompt("سبب الرفض:") : null; if (kind === "reject" && !comment) return; await api(`/admin/disposal-batches/${b.id}/vote`, { method: "POST", body: { decision: kind, password: pw, comment, otp } }); toast("سُجّل صوتك"); }
    render();
  });
  return h("div", { class: "card" }, h("h3", {}, `لجنة الوثائق — يلزم ${need} اعتماد (المنشئ لا يصوّت)`),
    state.user.role === "admin" && dispose.length ? [h("div", { class: "small muted" }, "مؤهّلة للإتلاف — حدّد لإنشاء دفعة تُعرض على اللجنة:"), h("table", {}, h("tbody", {}, dispose.map((c) => { const cb = h("input", { type: "checkbox", "aria-label": "تحديد" }); checks.set(c.id, cb); return h("tr", {}, h("td", {}, cb), h("td", { class: "ltr" }, c.ref_no), h("td", {}, c.subject)); }))), h("button", { class: "btn", onclick: create }, "إنشاء دفعة للجنة")] : null,
    batches.length ? h("table", {}, h("thead", {}, h("tr", {}, ["#", "المنشئ", "العدد", "السبب", "الحالة", "الأصوات", ""].map((t) => h("th", {}, t)))), h("tbody", {}, batches.map((b) => h("tr", {}, h("td", {}, b.id), h("td", {}, b.creator), h("td", {}, b.count), h("td", { class: "small" }, b.reason),
      h("td", {}, { pending: pill("in_progress", "بانتظار التصويت"), approved: pill("closed", "معتمدة"), rejected: pill("urgent", "مرفوضة"), executed: pill("archived", "نُفّذت") }[b.status]),
      h("td", { class: "small" }, b.votes.map((v) => `${v.decision === "approve" ? "✔" : "✖"} ${v.full_name}`).join("، ") || "—"),
      h("td", { class: "row" }, b.status === "pending" && !b.i_voted ? [h("button", { class: "btn sm", onclick: act(b, "approve") }, "موافقة"), h("button", { class: "btn danger sm", onclick: act(b, "reject") }, "رفض")] : null,
        b.status === "approved" && state.user.role === "admin" ? h("button", { class: "btn danger sm", onclick: act(b, "execute") }, "تنفيذ الإتلاف") : null)))) ) : h("div", { class: "muted" }, "لا دفعات"));
}
