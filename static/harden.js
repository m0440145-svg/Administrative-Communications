// تحصين المرحلة 0: بطاقة إضافية في «سياسات الأمان» (تُضاف تحت البطاقات القائمة دون المساس بها)
(() => {
  const orig = ADMIN_VIEWS.security;
  ADMIN_VIEWS.security = async () => {
    const [base, st] = await Promise.all([orig(), api("/admin/settings")]);
    const num = (k, label, min, max) => { const el = h("input", { type: "number", min, max, value: st[k], class: "narrow", "aria-label": label }); return [k, h("label", { class: "inl" }, label, el), el]; };
    const chk = (k, label) => { const el = h("input", { type: "checkbox", checked: st[k] === "1" }); return [k, h("label", { class: "opt" }, el, label), el, true]; };
    const rows = [num("pw_min_length", "الحد الأدنى لطول كلمة المرور (8–64)", 8, 64), chk("pw_require_case", "إلزام حروف كبيرة وصغيرة"), chk("pw_block_common", "حظر كلمات المرور الشائعة"),
      num("rate_login_per_min", "حدّ محاولات الدخول لكل عنوان IP في الدقيقة (0 = بلا حد)", 0, 10000), num("rate_heavy_per_min", "حدّ البحث والتصدير لكل IP في الدقيقة (0 = بلا حد)", 0, 100000),
      num("idle_minutes", "إنهاء الجلسة عند الخمول بعد (دقيقة، 0 = معطّل)", 0, 1440), num("chain_check_hours", "فحص سلامة سلسلة سجل الإجراءات كل (ساعة، 0 = معطّل)", 0, 720)];
    const card = h("div", { class: "card" }, h("h3", {}, "تحصين الحسابات والجلسات"),
      h("p", { class: "small muted" }, "تسري فورًا. إبطال الجلسات الأخرى عند تغيير كلمة المرور مفعّل دائمًا. أُضيف تنبيه للمديرين إن اختلّت سلسلة سجل الإجراءات."),
      h("div", { class: "form-grid" }, rows.map((r) => r[1])),
      h("button", { class: "btn", onclick: guardX(async () => {
        await api("/admin/settings", { method: "PUT", body: Object.fromEntries(rows.map(([k, , el, isChk]) => [k, isChk ? (el.checked ? "1" : "0") : String(el.value)])) });
        toast("حُفظت إعدادات التحصين");
      }) }, "حفظ التحصين"));
    return [...base, card];
  };
})();
