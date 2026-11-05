// المهام (To-Do): إضافة مهمة، تحويل معاملة (وخصوصًا العاجلة) إلى مهمة، ومتابعة المهام في «مهامي» وفي صفحة المعاملة.
// إضافة فوق ما سبق: تغلّف tasksView وdetailView وdetailTools دون تغيير سلوكها الأصلي. الواجهة الخلفية: ext_tasks.py (/api/work-tasks).
(() => {
  const PRIO = { normal: "عادية", high: "مهمة", urgent: "عاجلة" };
  const staff = () => ["admin", "manager", "clerk"].includes(state.user.role);
  const tomorrow = (n = 1) => { const d = new Date(Date.now() + n * 86400000); return d.toISOString().slice(0, 10); };

  // ───── نافذة إنشاء مهمة (مستقلة أو من معاملة) ─────
  function taskDialog({ corr, onDone }) {
    const urgent = corr && state.clsMap[corr.priority] && state.clsMap[corr.priority].is_urgent;
    const title = h("input", { maxlength: 200, required: true, value: corr ? (corr.conf_level ? "متابعة المعاملة " + corr.ref_no : "متابعة: " + corr.subject).slice(0, 200) : "", "aria-label": "عنوان المهمة" });
    const desc = h("textarea", { maxlength: 2000, rows: 3, "aria-label": "تفاصيل" });
    const due = h("input", { type: "date", value: tomorrow(corr ? (urgent ? 1 : 3) : 3), "aria-label": "الاستحقاق" });
    const prio = sel("prio", PRIO, corr ? (urgent ? "urgent" : "normal") : "normal", { "aria-label": "الأولوية" });
    const users = state.users.filter((u) => u.active !== false);
    const who = staff() ? sel("who", Object.fromEntries(users.map((u) => [u.id, u.full_name + (u.id === state.user.id ? " (أنا)" : "")])), state.user.id, { "aria-label": "المسؤول عن التنفيذ" }) : null;
    const ref = corr ? null : h("input", { class: "ltr", placeholder: "اختياري: رقم معاملة لربطها", "aria-label": "رقم المعاملة", maxlength: 40 });
    const err = h("div", { class: "err", role: "alert" });
    const close = () => { box.remove(); document.removeEventListener("keydown", esc); };
    const esc = (e) => e.key === "Escape" && close();
    const save = h("button", { class: "btn", type: "submit" }, corr ? "تحويل إلى مهمة" : "إضافة المهمة");
    const form = h("form", { class: "card tdlg", onsubmit: async (e) => {
      e.preventDefault(); err.textContent = ""; save.disabled = true;
      const body = { title: title.value, description: desc.value, due_date: due.value, priority: prio.value };
      if (who) body.assignee_id = Number(who.value);
      if (ref && ref.value.trim()) body.ref_no = ref.value.trim();
      try {
        await api(corr ? `/correspondences/${corr.id}/to-task` : "/work-tasks", { method: "POST", body });
        toast(corr ? "حُوِّلت المعاملة إلى مهمة" : "أُضيفت المهمة"); close(); onDone && onDone();
      } catch (ex) { err.textContent = ex.message; save.disabled = false; }
    } },
      h("h3", {}, corr ? "تحويل المعاملة إلى مهمة" : "مهمة جديدة"),
      corr ? h("p", { class: "small muted" }, "المعاملة: ", h("span", { class: "ltr" }, corr.ref_no), corr.conf_level ? " (سرية: لا يظهر موضوعها في المهمة)" : " — " + corr.subject) : null,
      f("العنوان", title, true), f("التفاصيل (اختياري)", desc),
      h("div", { class: "form-grid" }, f("الاستحقاق", due), f("الأولوية", prio), who ? f("المسؤول عن التنفيذ", who) : null, ref ? f("ربط بمعاملة", ref) : null),
      err, h("div", { class: "row" }, save, h("button", { type: "button", class: "btn ghost", onclick: close }, "إلغاء")));
    const box = h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": "مهمة", onclick: (e) => e.target === box && close() }, form);
    document.addEventListener("keydown", esc); document.body.append(box); title.focus();
  }

  // ───── بطاقة مهمة ─────
  function taskItem(t, reload) {
    const act = (label, fn, cls = "ghost") => h("button", { class: "btn sm " + cls, onclick: async () => { try { await fn(); reload(); } catch (e) { if (e.message) toast(e.message, true); } } }, label);
    return h("div", { class: "wtask" + (t.status === "done" ? " done" : "") + (t.overdue ? " late" : "") },
      h("div", { class: "wt-main" },
        h("strong", {}, t.title), " ", t.priority !== "normal" ? h("span", { class: "pill " + (t.priority === "urgent" ? "urgent" : "") }, PRIO[t.priority]) : null, t.overdue ? [" ", h("span", { class: "pill urgent" }, "متأخرة")] : null,
        t.description ? h("div", { class: "small" }, t.description) : null,
        h("div", { class: "small muted" },
          t.due_date ? "الاستحقاق: " + t.due_date + " • " : "", "المسؤول: ", t.assignee_name, t.created_by !== t.assignee_id ? " • من: " + t.creator_name : "",
          t.ref_no ? [" • ", t.corr_id ? h("a", { href: "#/c/" + t.corr_id, class: "ltr" }, t.ref_no) : h("span", { class: "ltr" }, t.ref_no), t.corr_subject ? " — " + t.corr_subject : ""] : null),
        t.status === "done" && t.done_note ? h("div", { class: "small muted" }, "ملاحظة الإنجاز: " + t.done_note) : null),
      h("div", { class: "wt-act" }, t.status === "open" && t.can_complete ? act("✔ إنجاز", async () => { const note = prompt("ملاحظة الإنجاز (اختياري):"); if (note === null) throw new Error(""); await api(`/work-tasks/${t.id}/complete`, { method: "POST", body: { note } }); toast("أُنجزت المهمة"); }, "") : null,
        t.status === "done" && t.can_complete ? act("↺ إعادة فتح", () => api(`/work-tasks/${t.id}/reopen`, { method: "POST", body: {} })) : null,
        t.can_edit ? act("🗑", async () => { if (!confirm("حذف المهمة؟")) throw new Error(""); await api("/work-tasks/" + t.id, { method: "DELETE" }); toast("حُذفت المهمة"); }) : null));
  }

  // ───── قسم المهام داخل «مهامي» ─────
  const baseTasksView = tasksView;
  tasksView = async () => {
    const base = await baseTasksView();
    const box = h("div", { class: "card", id: "wtasks" });
    let scope = "mine", status = "open";
    const load = async () => {
      const list = await api(`/work-tasks?scope=${scope}&status=${status}`);
      const tab = (label, val, cur, set) => h("button", { class: "tab" + (cur === val ? " on" : ""), type: "button", onclick: () => { set(val); load(); } }, label);
      box.replaceChildren(
        h("div", { class: "row between" }, h("h3", {}, "المهام والمتابعات"), h("button", { class: "btn sm", onclick: () => taskDialog({ onDone: load }) }, "+ مهمة جديدة")),
        h("div", { class: "row" }, tab("مهامي", "mine", scope, (v) => (scope = v)), staff() ? tab("كلّفتُ بها غيري", "created", scope, (v) => (scope = v)) : null, state.user.role === "admin" ? tab("الكل", "all", scope, (v) => (scope = v)) : null,
          h("span", { class: "tb-sp" }), tab("مفتوحة", "open", status, (v) => (status = v)), tab("مُنجزة", "done", status, (v) => (status = v))),
        ...(list.length ? list.map((t) => taskItem(t, load)) : [h("div", { class: "empty" }, status === "open" ? "لا مهام مفتوحة" : "لا مهام مُنجزة")]));
    };
    try { await load(); } catch (e) { box.replaceChildren(h("div", { class: "muted" }, "تعذّر تحميل المهام: " + e.message)); }
    const arr = Array.isArray(base) ? base : [base];
    return [arr[0], box, ...arr.slice(1)];
  };

  // ───── زر «تحويل إلى مهمة» في أدوات المعاملة + مهامها في صفحة التفاصيل ─────
  const baseTools = detailTools;
  detailTools = (c) => {
    const isUrgent = state.clsMap[c.priority] && state.clsMap[c.priority].is_urgent;
    const btn = h("button", { class: "btn sm " + (isUrgent ? "" : "ghost"), type: "button", title: "تحويل هذه المعاملة إلى مهمة متابعة", onclick: () => taskDialog({ corr: c, onDone: render }) }, "✔ تحويل إلى مهمة");
    return [...baseTools(c), btn];
  };
  const baseDetail = detailView;
  detailView = async (id) => {
    const base = await baseDetail(id);
    let tasks = [];
    try { tasks = await api("/work-tasks?corr_id=" + id); } catch { /* لا يمنع عرض المعاملة */ }
    if (!tasks.length) return base;
    const card = h("div", { class: "card" }, h("h3", {}, `مهام المتابعة (${tasks.filter((t) => t.status === "open").length} مفتوحة)`), tasks.map((t) => taskItem(t, render)));
    return Array.isArray(base) ? [...base, card] : [base, card];
  };
})();
