// إصدارات المرفقات + الحقول المخصصة (مستلهمة من Mayan EDMS) بالعربية. إضافة فوق صفحة المعاملة دون تغيير سلوكها؛
// الواجهة الخلفية: ext_edms.py. (تُحمَّل بعد browse.js)
(() => {
  const fmtSize = (n) => sizeStr(n);
  const lbl = (label, input, req) => f(label, input, req);  // f = دالة الحقل العامة (تُحجب بالاسم داخل خرائط المتغيّرات)

  // ───── نافذة إصدارات المرفق ─────
  function versionsDialog(att, meta, reload) {
    const box = h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": "إصدارات المرفق", onclick: (e) => e.target === box && close() });
    const close = () => { box.remove(); document.removeEventListener("keydown", esc); };
    const esc = (e) => e.key === "Escape" && close();
    const body = h("div", { class: "card tdlg vdlg" });
    box.append(body);
    const dlOld = async (v) => { try { const r = await api("/attachment-versions/" + v.id, { raw: true }); downloadBlob(await r.blob(), v.filename); } catch (e) { toast(e.message, true); } };
    const dlCur = async () => { try { const r = await api("/attachments/" + att.id, { raw: true }); downloadBlob(await r.blob(), att.filename); } catch (e) { toast(e.message, true); } };
    async function draw() {
      const vs = await api(`/attachments/${meta.id}/versions`);
      const file = h("input", { type: "file", "aria-label": "الإصدار الجديد" }), note = h("input", { maxlength: 300, placeholder: "سبب الاستبدال (اختياري)", "aria-label": "سبب الاستبدال" });
      const up = h("button", { class: "btn", type: "button", onclick: async () => {
        const f = file.files[0]; if (!f) return toast("اختر الملف الجديد أولًا", true);
        up.disabled = true;
        try {
          const headers = { "X-Filename": encodeURIComponent(f.name) }; if (note.value.trim()) headers["X-Note"] = encodeURIComponent(note.value.trim());
          await api(`/attachments/${meta.id}/versions`, { method: "POST", body: f, headers }); toast("حُفظ الإصدار الجديد وأُبقي السابق"); await draw(); reload();
        } catch (e) { toast(e.message, true); up.disabled = false; }
      } }, "رفع كإصدار جديد");
      body.replaceChildren(h("h3", {}, "إصدارات المرفق: " + att.filename),
        h("p", { class: "small muted" }, "كل رفع جديد يحفظ الإصدار السابق بفحصه وبصمته. لا يُسمح بذلك على المعاملات الموقَّعة أو المؤرشفة."),
        h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["الإصدار", "الملف", "الحجم", "رفعه", "البصمة SHA-256", "سبب الاستبدال", ""].map((t) => h("th", {}, t)))),
          h("tbody", {}, vs.map((v) => h("tr", {}, h("td", {}, v.current ? h("strong", {}, v.ver_no + " (الحالي)") : v.ver_no), h("td", {}, v.filename), h("td", {}, fmtSize(v.size)),
            h("td", { class: "small" }, (v.uploader || "—") + " — " + fmtDT(v.created_at)), h("td", { class: "small muted ltr", title: v.sha256 }, v.sha256.slice(0, 16) + "…"), h("td", { class: "small" }, v.note || "—"),
            h("td", {}, h("button", { class: "btn ghost sm", onclick: () => (v.current ? dlCur() : dlOld(v)) }, "⬇ تنزيل"))))))),
        meta.can_version ? h("div", { class: "row vup" }, file, note, up) : h("p", { class: "small muted" }, "لا يتاح لك رفع إصدار جديد لهذا المرفق."),
        h("div", { class: "row" }, h("button", { class: "btn ghost", type: "button", onclick: close }, "إغلاق")));
    }
    draw().then(() => { document.addEventListener("keydown", esc); document.body.append(box); }).catch((e) => toast(e.message, true));
  }

  // ───── بطاقة «بيانات إضافية» (الحقول المخصصة) ─────
  function fieldsCard(cid, data) {
    if (!data.fields.length) return null;
    const inputs = data.fields.map((f) => {
      let el;
      if (f.ftype === "choice") el = sel("cf" + f.id, { "": "— اختر —", ...Object.fromEntries(f.choices.map((c) => [c, c])) }, f.value || "", { "aria-label": f.name });
      else el = h("input", { type: f.ftype === "number" ? "text" : f.ftype === "date" ? "date" : "text", inputmode: f.ftype === "number" ? "decimal" : null, maxlength: 500, value: f.value || "", "aria-label": f.name, dir: f.ftype === "text" ? null : "ltr" });
      if (!data.can_edit) { el.disabled = true; }
      return [f, el];
    });
    const err = h("div", { class: "err", role: "alert" });
    return h("div", { class: "card" }, h("h3", {}, "بيانات إضافية"), h("div", { class: "form-grid" }, inputs.map(([fl, el]) => lbl(fl.name, el, !!fl.required))),
      err, data.can_edit ? h("button", { class: "btn", type: "button", onclick: async (e) => {
        const btn = e.currentTarget; err.textContent = ""; btn.disabled = true;
        try { await api(`/correspondences/${cid}/fields`, { method: "PUT", body: { values: Object.fromEntries(inputs.map(([fl, el]) => [String(fl.id), el.value])) } }); toast("حُفظت البيانات الإضافية"); render(); }
        catch (ex) { err.textContent = ex.message; btn.disabled = false; }
      } }, "حفظ البيانات") : h("p", { class: "small muted" }, "للاطلاع فقط."));
  }

  // ───── تغليف صفحة المعاملة ─────
  const baseDetail = detailView;
  detailView = async (id) => {
    const base = await baseDetail(id);
    let meta = null, fields = null;
    [meta, fields] = await Promise.all([api(`/correspondences/${id}/att-meta`).catch(() => null), api(`/correspondences/${id}/fields`).catch(() => null)]);
    const nodes = Array.isArray(base) ? [...base] : [base];
    if (meta) {
      const card = nodes.find((n) => n && n.querySelectorAll && [...n.querySelectorAll("h3")].some((x) => x.textContent.startsWith("المرفقات (")));
      if (card) {
        const queue = meta.items.slice();
        card.querySelectorAll("table tbody tr").forEach((tr) => {
          const first = tr.querySelector("td");
          const name = first && first.querySelector("a") ? first.querySelector("a").textContent : null;
          const m = queue.find((x) => x.filename === name); if (!m) return; queue.splice(queue.indexOf(m), 1);
          const label = m.old_versions ? `🕘 الإصدارات (${m.old_versions + 1})` : "🕘 الإصدارات";
          first.append(" ", h("button", { class: "btn ghost sm", type: "button", title: "إصدارات المرفق", onclick: () => versionsDialog({ id: m.id, filename: name }, m, render) }, label));
        });
      }
    }
    const fc = fields && fieldsCard(id, fields);
    if (fc) nodes.splice(Math.min(1, nodes.length), 0, fc);
    return nodes;
  };

  // ───── لوحة الإدارة: الحقول المخصصة ─────
  ADMIN_VIEWS.fields = async () => {
    const list = await api("/custom-fields?all=1");
    const types = Object.fromEntries(state.types.map((t) => [t.id, t.name]));
    const name = h("input", { maxlength: 80, "aria-label": "اسم الحقل", placeholder: "مثال: قيمة العقد" });
    const ftype = sel("ftype", { text: "نص", number: "رقم", date: "تاريخ", choice: "اختيار من قائمة" }, "text", { "aria-label": "نوع الحقل" });
    const choices = h("textarea", { rows: 2, placeholder: "الاختيارات مفصولة بفاصلة أو سطر جديد", "aria-label": "الاختيارات", hidden: true });
    const req = h("input", { type: "checkbox" });
    const typ = sel("ftyp", { "": "كل الأنواع", ...types }, "", { "aria-label": "ينطبق على" });
    ftype.addEventListener("change", () => (choices.hidden = ftype.value !== "choice"));
    const upd = (fl, body) => guardX(async () => { await api("/custom-fields/" + fl.id, { method: "PUT", body }); toast("تم الحفظ"); render(); });
    return [adminHead("fields", "الحقول المخصصة", "حقول بيانات إضافية تظهر في صفحة المعاملة (بطاقة «بيانات إضافية»): لكل الأنواع أو لنوع معاملة معيّن، اختيارية أو إلزامية."),
      list.length ? h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["الحقل", "النوع", "ينطبق على", "إلزامي", "نشط", ""].map((t) => h("th", {}, t)))),
        h("tbody", {}, list.map((fl) => h("tr", {}, h("td", {}, h("strong", {}, fl.name), fl.ftype === "choice" ? h("div", { class: "small muted" }, fl.choices.join("، ")) : null), h("td", {}, fl.ftype_label), h("td", {}, fl.type_id ? types[fl.type_id] || "—" : "كل الأنواع"),
          h("td", {}, h("input", { type: "checkbox", class: "chk", checked: !!fl.required, "aria-label": "إلزامي", onchange: (e) => upd(fl, { required: e.target.checked })() })),
          h("td", {}, h("input", { type: "checkbox", class: "chk", checked: !!fl.active, "aria-label": "نشط", onchange: (e) => upd(fl, { active: e.target.checked })() })),
          h("td", {}, h("button", { class: "btn ghost sm", onclick: guardX(async () => { const n = prompt("الاسم الجديد للحقل:", fl.name); if (n && n.trim() && n !== fl.name) { await api("/custom-fields/" + fl.id, { method: "PUT", body: { name: n.trim() } }); render(); } }) }, "تعديل الاسم"))))))) : h("div", { class: "card empty" }, "لا حقول مخصصة بعد"),
      h("div", { class: "card" }, h("h3", {}, "إضافة حقل"), h("div", { class: "form-grid" }, f("اسم الحقل", name, true), f("النوع", ftype), f("ينطبق على", typ), f("الاختيارات", choices), h("label", { class: "opt" }, req, "إلزامي")),
        h("button", { class: "btn", onclick: guardX(async () => {
          await api("/custom-fields", { method: "POST", body: { name: name.value, ftype: ftype.value, choices: choices.value, required: req.checked, type_id: typ.value ? Number(typ.value) : null } });
          toast("أُضيف الحقل"); render();
        }) }, "إضافة"))];
  };
  ADMIN_TABS.push(["fields", "الحقول المخصصة"]);
})();
