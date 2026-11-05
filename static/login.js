// صفحة الدخول (واجهة الاستقبال): لوحة هوية + نموذج واضح. منطق الدخول وMFA وboot كما كان تمامًا (تُحمَّل بعد app.js)
(() => {
  const icon = (d) => svgNode(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${d}</svg>`);
  const ICONS = {
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    route: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.5 6H15a3 3 0 0 1 0 6H9a3 3 0 0 0 0 6h6.5"/>',
    shield: '<path d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6z"/><path d="m9 12 2 2 4-4"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    eyeoff: '<path d="M3 3l18 18"/><path d="M10.6 6.1A9.8 9.8 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4"/><path d="M6.6 6.6A17 17 0 0 0 2 12s3.6 7 10 7a9.6 9.6 0 0 0 4-.9"/>',
  };
  const FEATURES = [["mail", "قيد وتتبّع المراسلات الواردة والصادرة، الداخلية والخارجية"], ["route", "مسارات اعتماد وإسناد مع مهل وتصعيد تلقائي"],
    ["shield", "صلاحيات ومستويات سرّية وسجل تدقيق غير قابل للتعديل"], ["clock", "إشعارات لحظية وتقارير ومؤشرات أداء"]];

  loginView = function () {
    const err = h("div", { class: "lg-err", id: "lg-err", role: "alert", "aria-live": "assertive" });
    const caps = h("div", { class: "lg-hint", hidden: true }, "⚠ مفتاح Caps Lock مفعّل");
    const u = h("input", { id: "u", name: "u", autocomplete: "username", required: true, autofocus: true, autocapitalize: "none", spellcheck: "false", "aria-describedby": "lg-err" });
    const p = h("input", { id: "p", name: "p", type: "password", autocomplete: "current-password", required: true, "aria-describedby": "lg-err" });
    const eye = h("button", { type: "button", class: "lg-eye", "aria-label": "إظهار كلمة المرور", "aria-pressed": "false", onclick: () => {
      const show = p.type === "password"; p.type = show ? "text" : "password";
      eye.setAttribute("aria-pressed", String(show)); eye.setAttribute("aria-label", show ? "إخفاء كلمة المرور" : "إظهار كلمة المرور"); eye.replaceChildren(icon(ICONS[show ? "eyeoff" : "eye"]));
    } }, icon(ICONS.eye));
    p.addEventListener("keyup", (e) => { caps.hidden = !(e.getModifierState && e.getModifierState("CapsLock")); });
    const btn = h("button", { class: "btn lg-submit", type: "submit" }, "دخول");
    const form = h("form", { class: "lg-form", onsubmit: async (e) => {
      e.preventDefault(); err.textContent = ""; btn.disabled = true; btn.textContent = "جارٍ التحقق…";
      try {
        let r = await api("/login", { method: "POST", body: { username: u.value.trim(), password: p.value } });
        if (r.mfa_required) {
          const code = prompt("أدخل رمز المصادقة الثنائية (6 أرقام من تطبيق المصادقة) أو أحد رموز الاسترداد:");
          if (!code) throw new Error("لم يُدخَل رمز التحقق");
          r = await api("/login/mfa", { method: "POST", body: { ticket: r.ticket, code: code.trim() } });
        }
        state.token = r.token; sessionStorage.setItem("acp_token", r.token); state.user = r.user;
        await boot();
      } catch (ex) { err.textContent = ex.message; p.value = ""; p.focus(); }
      finally { btn.disabled = false; btn.textContent = "دخول"; }
    } },
      h("h2", {}, "تسجيل الدخول"), h("p", { class: "muted small" }, "أدخل بيانات حسابك للمتابعة"),
      h("div", { class: "field" }, h("label", { for: "u" }, "اسم المستخدم"), u),
      h("div", { class: "field" }, h("label", { for: "p" }, "كلمة المرور"), h("div", { class: "lg-pw" }, p, eye), caps),
      err, btn,
      h("p", { class: "small muted lg-help" }, "نسيت كلمة المرور؟ تواصل مع مسؤول النظام لإعادة تعيينها."));
    const name = BRAND.name || "منصة الاتصالات الإدارية";
    return h("div", { class: "lg-page" },
      h("section", { class: "lg-brand", "aria-label": "تعريف بالمنصة" },
        h("div", { class: "lg-brand-in" }, BRAND.logo ? h("div", { class: "lg-logo" }, brandLogo("lg-logo-img")) : null,
          h("h1", {}, name), h("p", { class: "lg-tag" }, BRAND.name ? "منصة الاتصالات الإدارية" : "إدارة المراسلات والمعاملات بكفاءة وأمان"),
          BRAND.login_message ? h("p", { class: "lg-msg" }, BRAND.login_message) : null,
          h("ul", { class: "lg-feats" }, FEATURES.map(([ic, t]) => h("li", {}, icon(ICONS[ic]), h("span", {}, t)))))),
      h("div", { class: "lg-main" }, h("div", { class: "lg-card" }, form,
        h("p", { class: "small muted lg-note" }, "الدخول مقصور على المصرّح لهم. نشاطك على المنصة مسجَّل لأغراض التدقيق والأمن."))));
  };
})();
