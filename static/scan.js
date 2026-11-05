// المسح والاستلام + طباعة الباركود بأحجام ونسخ + أنواع الملفات (تُحمَّل بعد app.js)
const SCAN_HELP = "للماسحات الشبكية/المكتبية: اضبط «المسح إلى مجلد» على المجلد الذي يحدده مسؤول النظام (ACP_SCAN_DIR). سمِّ الملف برقم القيد أولًا (مثل IN-00012__عقد.pdf) ليُرفق مباشرةً بالمراسلة، وإلا دخل صندوق المسح لتنسبه يدويًا.";

// ───── دمج صور ممسوحة في ملف PDF واحد (JPEG داخل PDF، بلا مكتبات) ─────
async function imagesToPdf(files) {
  const pages = [];
  for (const f of files) {
    const bmp = await createImageBitmap(f);
    const cv = document.createElement("canvas"); cv.width = bmp.width; cv.height = bmp.height;
    const cx = cv.getContext("2d"); cx.fillStyle = "#fff"; cx.fillRect(0, 0, cv.width, cv.height); cx.drawImage(bmp, 0, 0);
    const blob = await new Promise((r) => cv.toBlob(r, "image/jpeg", 0.88));
    pages.push({ w: bmp.width, h: bmp.height, jpg: new Uint8Array(await blob.arrayBuffer()) });
  }
  const enc = new TextEncoder(), chunks = [], offs = [];
  let len = 0;
  const put = (x) => { const b = typeof x === "string" ? enc.encode(x) : x; chunks.push(b); len += b.length; };
  const obj = (n, body) => { offs[n] = len; put(`${n} 0 obj\n`); body(); put("\nendobj\n"); };
  const N = pages.length, total = 2 + N * 3;
  put("%PDF-1.4\n");
  obj(1, () => put("<< /Type /Catalog /Pages 2 0 R >>"));
  obj(2, () => put(`<< /Type /Pages /Count ${N} /Kids [${pages.map((_, i) => `${3 + i * 3} 0 R`).join(" ")}] >>`));
  pages.forEach((p, i) => {
    const pg = 3 + i * 3, ct = pg + 1, im = pg + 2, PW = 595, PH = 842, s = Math.min(PW / p.w, PH / p.h), w = (p.w * s).toFixed(2), hh = (p.h * s).toFixed(2);
    const x = ((PW - p.w * s) / 2).toFixed(2), y = ((PH - p.h * s) / 2).toFixed(2), content = `q ${w} 0 0 ${hh} ${x} ${y} cm /Im0 Do Q`;
    obj(pg, () => put(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PW} ${PH}] /Resources << /XObject << /Im0 ${im} 0 R >> >> /Contents ${ct} 0 R >>`));
    obj(ct, () => put(`<< /Length ${content.length} >>\nstream\n${content}\nendstream`));
    obj(im, () => { put(`<< /Type /XObject /Subtype /Image /Width ${p.w} /Height ${p.h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${p.jpg.length} >>\nstream\n`); put(p.jpg); put("\nendstream"); });
  });
  const xref = len;
  put(`xref\n0 ${total + 1}\n0000000000 65535 f \n` + offs.slice(1).map((o) => String(o).padStart(10, "0") + " 00000 n \n").join(""));
  put(`trailer\n<< /Size ${total + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`);
  return new Blob(chunks, { type: "application/pdf" });
}

const sendToInbox = (blob, name) => api("/scan/inbox", { method: "POST", body: blob, headers: { "X-Filename": encodeURIComponent(name) } });

// ───── صفحة المسح والاستلام ─────
EXT_ROUTES["/scan"] = async () => {
  const [rules, items] = await Promise.all([api("/scan/rules"), api("/scan/inbox")]);
  const status = h("div", { class: "small muted", role: "status" });
  const merge = h("input", { type: "checkbox", id: "scan-merge", checked: true });
  const run = guardX(async (files) => {
    files = [...files]; if (!files.length) return;
    const imgs = files.filter((f) => f.type.startsWith("image/") && /jpe?g|png|webp|bmp/.test(f.type));
    status.textContent = "جارٍ الرفع…";
    if (merge.checked && imgs.length > 1) {
      const pdf = await imagesToPdf(imgs);
      await sendToInbox(pdf, "مسح-" + new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-") + ".pdf");
      for (const f of files.filter((x) => !imgs.includes(x))) await sendToInbox(f, f.name);
    } else for (const f of files) await sendToInbox(f, f.name);
    toast("أُضيف إلى صندوق المسح"); render();
  });
  const pick = h("input", { type: "file", multiple: true, class: "sr-only", id: "scan-file", onchange: (e) => run(e.target.files) });
  const cam = h("input", { type: "file", accept: "image/*", capture: "environment", multiple: true, class: "sr-only", id: "scan-cam", onchange: (e) => run(e.target.files) });
  const drop = h("div", { class: "dropzone", tabindex: 0, role: "button", "aria-label": "اسحب الملفات هنا أو اضغط للاختيار",
    onclick: () => pick.click(), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick.click(); } },
    ondragover: (e) => { e.preventDefault(); drop.classList.add("over"); }, ondragleave: () => drop.classList.remove("over"),
    ondrop: (e) => { e.preventDefault(); drop.classList.remove("over"); run(e.dataTransfer.files); } },
    h("div", { class: "dz-t" }, "اسحب الملفات الممسوحة هنا أو اضغط للاختيار"), h("div", { class: "small muted" }, `حتى ${rules.max_mb} ميغابايت للملف • ${rules.exts.length} نوع مدعوم`));

  const attachRef = (it) => { const inp = h("input", { placeholder: "رقم القيد", class: "ltr", "aria-label": "رقم القيد", size: 14 });
    return h("span", { class: "row" }, inp, h("button", { class: "btn ghost sm", onclick: guardX(async () => { if (!inp.value.trim()) throw new Error("اكتب رقم القيد"); const r = await api(`/scan/inbox/${it.id}/attach`, { method: "POST", body: { ref_no: inp.value.trim() } }); toast("أُرفق بالمراسلة " + r.ref_no); render(); }) }, "إرفاق")); };
  const preview = guardX(async (it) => {
    const res = await api(`/scan/inbox/${it.id}/file`, { raw: true }), ext = it.filename.split(".").pop().toLowerCase();
    const blob = new Blob([await res.arrayBuffer()], { type: PREVIEW_MIME[ext] || "application/octet-stream" });
    window.open(URL.createObjectURL(blob), "_blank");
  });
  return [h("h2", {}, "المسح والاستلام"), h("p", { class: "muted small" }, "ارفع المستندات الممسوحة (أو صوّرها بالجوال) ثم انسبها لمراسلة جديدة أو قائمة."),
    h("div", { class: "card" }, drop, pick, cam,
      h("div", { class: "row" }, h("label", { class: "btn ghost sm", for: "scan-cam" }, "📷 تصوير بالكاميرا"), h("label", { class: "opt" }, merge, "دمج عدة صور في ملف PDF واحد"), status),
      rules.scan_dir ? h("p", { class: "small" }, "✔ مجلد المسح التلقائي مفعّل. ", SCAN_HELP) : h("p", { class: "small muted" }, "مجلد المسح التلقائي غير مفعّل. ", SCAN_HELP)),
    h("div", { class: "card" }, h("h3", {}, `صندوق المسح (${items.length})`), items.length ? h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["الملف", "الحجم", "المصدر", "الوقت", "إجراءات"].map((t) => h("th", {}, t)))),
      h("tbody", {}, items.map((it) => h("tr", {}, h("td", {}, it.filename), h("td", {}, sizeStr(it.size)), h("td", {}, it.source === "folder" ? "مجلد المسح" : "رفع — " + (it.uploader || "")), h("td", { class: "small" }, fmtDT(it.created_at)),
        h("td", {}, h("div", { class: "row" }, previewable(it.filename) ? h("button", { class: "btn ghost sm", onclick: () => preview(it) }, "👁 معاينة") : null,
          h("a", { class: "btn sm", href: `#/new?scan=${it.id}&direction=incoming` }, "مراسلة واردة جديدة"), h("a", { class: "btn ghost sm", href: `#/new?scan=${it.id}&direction=outgoing` }, "صادرة جديدة"), attachRef(it),
          h("button", { class: "btn danger sm", onclick: guardX(async () => { if (confirm("حذف الملف من الصندوق؟")) { await api("/scan/inbox/" + it.id, { method: "DELETE" }); render(); } }) }, "حذف")))))))) : h("div", { class: "empty" }, "الصندوق فارغ"))];
};

// ───── طباعة الباركود: أحجام ونسخ وختم استلام ─────
const LABEL_SIZES = { s: ["صغير 60×40 مم (طابعة حرارية)", "lb-s"], m: ["متوسط 90×55 مم", "lb-m"], l: ["ختم استلام 100×70 مم (باسم الجهة والتاريخ)", "lb-l"] };
EXT_PREFIX["/label/"] = async (id, q) => {
  const [l, org] = await Promise.all([api(`/correspondences/${id}/label`), api("/org/profile").catch(() => ({}))]);
  const size = h("select", { "aria-label": "الحجم", onchange: draw }, Object.entries(LABEL_SIZES).map(([k, [t]]) => h("option", { value: k, selected: k === (state.labelSize || "s") }, t)));
  const copies = h("input", { type: "number", min: 1, max: 50, value: state.labelCopies || 1, class: "narrow", "aria-label": "عدد النسخ", onchange: draw });
  const withSubj = h("input", { type: "checkbox", checked: state.labelSubj !== false, onchange: draw });
  const sheets = h("div", { class: "lb-grid" });
  function one() {
    const k = size.value, cls = LABEL_SIZES[k][1], dir = l.direction === "incoming" ? "وارد" : "صادر";
    return h("div", { class: "label-sheet " + cls },
      k === "l" ? h("div", { class: "lb-org" }, org.org_name || "ختم الاستلام", " — ", h("span", {}, l.direction === "incoming" ? "استلام" : "إصدار")) : null,
      h("div", { class: "lb-top" }, svgNode(l.qr_svg), h("div", { class: "lb-meta" }, h("div", { class: "lb-ref ltr" }, l.ref_no), h("div", {}, l.date), h("div", {}, dir + " • مرفقات: " + l.att_count),
        k === "l" ? h("div", {}, "تاريخ الطباعة: " + new Date().toISOString().slice(0, 16).replace("T", " ")) : null)),
      h("div", { class: "lb-bar" }, svgNode(l.barcode_svg)), withSubj.checked ? h("div", { class: "lb-subj" }, l.subject.slice(0, k === "s" ? 60 : 120)) : null);
  }
  function draw() {
    state.labelSize = size.value; state.labelCopies = Math.max(1, Math.min(50, Number(copies.value) || 1)); state.labelSubj = withSubj.checked;
    sheets.className = "lb-grid pg-" + size.value; sheets.replaceChildren(...Array.from({ length: state.labelCopies }, one));
  }
  draw();
  if (q && q.get && q.get("print") === "1") setTimeout(() => window.print(), 400);
  return [h("div", { class: "row noprint" }, h("h2", {}, "طباعة الباركود"), h("a", { class: "btn ghost sm", href: "#/c/" + id }, "← المراسلة"), h("button", { class: "btn", onclick: () => window.print() }, "🖨 طباعة")),
    h("div", { class: "card noprint" }, h("div", { class: "row" }, h("label", { class: "inl" }, "الحجم ", size), h("label", { class: "inl" }, "عدد النسخ ", copies), h("label", { class: "opt" }, withSubj, "طباعة الموضوع (يُحجب للمراسلات السرية)")),
      h("p", { class: "small muted" }, "يُمسح الـQR أو الباركود بأي قارئ لفتح المعاملة (خانة «فتح بالباركود» أو مفتاح /). اختر حجم الورق نفسه في إعدادات الطابعة.")),
    sheets];
};

// ───── أنواع الملفات (لوحة الإدارة) ─────
ADMIN_VIEWS.files = async () => {
  const [st, rules] = await Promise.all([api("/admin/settings"), api("/scan/rules")]);
  const extra = h("input", { class: "ltr", value: st.ext_extra || "", placeholder: ".dwg, .kml", "aria-label": "أنواع إضافية" });
  const mb = h("input", { type: "number", min: 1, max: 200, value: st.max_upload_mb, class: "narrow", "aria-label": "الحد الأقصى" });
  return [adminHead("files", "الملفات والمسح", "أنواع الملفات المسموح رفعها وحجمها الأقصى، وحالة مجلد المسح."),
    h("div", { class: "card" }, h("div", { class: "form-grid" }, f("أنواع إضافية مسموحة (امتدادات مفصولة بفواصل)", extra), f("الحد الأقصى لحجم الملف (ميغابايت، 1–200)", mb)),
      h("button", { class: "btn", onclick: guardX(async () => { await api("/admin/settings", { method: "PUT", body: { ext_extra: extra.value, max_upload_mb: String(mb.value) } }); toast("حُفظت إعدادات الملفات"); render(); }) }, "حفظ")),
    h("div", { class: "card" }, h("h3", {}, `الأنواع المدعومة حاليًا (${rules.exts.length})`), h("p", { class: "ltr small" }, rules.exts.join("  ")),
      h("h3", {}, "محظورة دائمًا (تنفيذية/سكربتات/ويب/ماكرو)"), h("p", { class: "ltr small muted" }, rules.blocked.join("  ")),
      h("p", { class: "small" }, rules.scan_dir ? "✔ مجلد المسح التلقائي مفعّل." : "مجلد المسح التلقائي غير مفعّل — عيّن المتغير ACP_SCAN_DIR ثم أعد تشغيل الخادم.", " ", h("a", { href: "#/scan" }, "صندوق المسح ←")))];
};
ADMIN_TABS.push(["files", "الملفات والمسح"]);
