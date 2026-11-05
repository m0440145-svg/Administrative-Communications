// صفحة الدخول — «وصال»: قسم تعريفي كحلي + بطاقة دخول على خلفية عاجية. تُحمَّل بعد app.js.
// منطق الدخول كما كان تمامًا (api("/login") ثم api("/login/mfa") عند الطلب ثم boot)؛ الجديد عرضيّ فقط، والنسخة السابقة في docs/backup-login-v1/.
(() => {
  const icon = (d, size = 22) => svgNode(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${d}</svg>`);
  const ICONS = {
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    route: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.5 6H15a3 3 0 0 1 0 6H9a3 3 0 0 0 0 6h6.5"/>',
    shield: '<path d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6z"/><path d="m9 12 2 2 4-4"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    eyeoff: '<path d="M3 3l18 18"/><path d="M10.6 6.1A9.8 9.8 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4"/><path d="M6.6 6.6A17 17 0 0 0 2 12s3.6 7 10 7a9.6 9.6 0 0 0 4-.9"/>',
    alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16.5h.01"/>',
    lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  };
  const FEATURES = [["mail", "قيد وتتبّع الوارد والصادر، الداخلي والخارجي"], ["route", "مسارات اعتماد وإسناد بمهل وتصعيد تلقائي"],
    ["shield", "صلاحيات ومستويات سرّية وسجل تدقيق محكم"], ["clock", "إشعارات لحظية وتقارير ومؤشرات أداء"]];

  // علامة «وصال»: ظرف تنساب منه خطوط مسار — مجرّدة ومتناسقة مع الرسم
  const MARK = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="48" height="48" aria-hidden="true" focusable="false">
    <rect width="48" height="48" rx="13" fill="#f28c28"/>
    <rect x="9" y="14" width="30" height="21" rx="4" fill="none" stroke="#fff" stroke-width="2.6"/>
    <path d="m10 17 14 10 14-10" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="38" cy="14" r="4.2" fill="#0b2545" stroke="#fff" stroke-width="2"/></svg>`;

  // رسم تجريدي لتدفق المراسلات: عقد (جهات/وحدات) مترابطة بمسارات، ورسالة تنساب نحو الاعتماد
  const FLOW = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 560 300" class="lg-flow" role="img" aria-label="رسم تجريدي لمسار مراسلة من الاستلام حتى الاعتماد" focusable="false">
    <defs><linearGradient id="lgf" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#f28c28" stop-opacity=".15"/><stop offset=".5" stop-color="#f28c28"/><stop offset="1" stop-color="#ffd7a8"/></linearGradient></defs>
    <g fill="none" stroke="#ffffff" stroke-opacity=".16" stroke-width="1.5"><circle cx="440" cy="60" r="120"/><circle cx="440" cy="60" r="80"/><circle cx="90" cy="260" r="90"/></g>
    <g fill="none" stroke="#ffffff" stroke-opacity=".28" stroke-width="2" stroke-linecap="round">
      <path d="M92 150 C170 150 170 70 250 70"/><path d="M92 150 C170 150 170 230 250 230"/><path d="M250 70 C330 70 330 150 410 150"/><path d="M250 230 C330 230 330 150 410 150"/></g>
    <path class="lg-flow-path" d="M92 150 C170 150 170 70 250 70 C330 70 330 150 410 150 L470 150" fill="none" stroke="url(#lgf)" stroke-width="4" stroke-linecap="round" stroke-dasharray="14 12"/>
    <g>
      <circle cx="70" cy="150" r="24" fill="#16365f" stroke="#fff" stroke-opacity=".5" stroke-width="2"/><path d="M58 144h24v14H58zm0 0 12 8 12-8" fill="none" stroke="#fff" stroke-width="2.2" stroke-linejoin="round"/>
      <rect x="226" y="48" width="48" height="44" rx="12" fill="#16365f" stroke="#fff" stroke-opacity=".5" stroke-width="2"/><path d="M240 70h20M240 78h12" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/>
      <rect x="226" y="208" width="48" height="44" rx="12" fill="#16365f" stroke="#fff" stroke-opacity=".5" stroke-width="2"/><path d="M240 230h20M240 238h12" stroke="#fff" stroke-width="2.4" stroke-linecap="round" opacity=".7"/>
      <circle cx="432" cy="150" r="30" fill="#f28c28"/><path d="m420 150 8 9 16-18" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
    </g></svg>`;

  loginView = function () {
    let ticket = null;  // تذكرة الخطوة الثانية (MFA) كما يُعيدها الخادم
    const err = h("div", { class: "lg-err", id: "lg-err", role: "alert", "aria-live": "assertive", hidden: true });
    const showErr = (m) => { err.replaceChildren(icon(ICONS.alert, 18), h("span", {}, m)); err.hidden = false; };
    const clearErr = () => { err.hidden = true; err.replaceChildren(); u.removeAttribute("aria-invalid"); p.removeAttribute("aria-invalid"); code.removeAttribute("aria-invalid"); };
    const caps = h("div", { class: "lg-hint", hidden: true }, "⚠ مفتاح Caps Lock مفعّل");
    const u = h("input", { id: "u", name: "u", autocomplete: "username", required: true, autofocus: true, autocapitalize: "none", spellcheck: "false", "aria-describedby": "lg-err" });
    const p = h("input", { id: "p", name: "p", type: "password", autocomplete: "current-password", required: true, "aria-describedby": "lg-err" });
    const code = h("input", { id: "otp", name: "otp", inputmode: "text", autocomplete: "one-time-code", autocapitalize: "none", spellcheck: "false", maxlength: 32, class: "lg-otp ltr", "aria-describedby": "lg-err", placeholder: "••••••" });
    const eye = h("button", { type: "button", class: "lg-eye", "aria-label": "إظهار كلمة المرور", "aria-pressed": "false", onclick: () => {
      const show = p.type === "password"; p.type = show ? "text" : "password";
      eye.setAttribute("aria-pressed", String(show)); eye.setAttribute("aria-label", show ? "إخفاء كلمة المرور" : "إظهار كلمة المرور"); eye.replaceChildren(icon(ICONS[show ? "eyeoff" : "eye"]));
    } }, icon(ICONS.eye));
    p.addEventListener("keyup", (e) => { caps.hidden = !(e.getModifierState && e.getModifierState("CapsLock")); });
    [u, p, code].forEach((el) => el.addEventListener("input", clearErr));

    const btn = h("button", { class: "btn lg-submit", type: "submit" }, "دخول");
    const back = h("button", { type: "button", class: "lg-back", hidden: true, onclick: () => { ticket = null; step(); u.focus(); } }, "← رجوع");
    const credBox = h("div", { class: "lg-step" }, h("div", { class: "field" }, h("label", { for: "u" }, "اسم المستخدم"), u),
      h("div", { class: "field" }, h("label", { for: "p" }, "كلمة المرور"), h("div", { class: "lg-pw" }, p, eye), caps));
    const mfaBox = h("div", { class: "lg-step", hidden: true }, h("div", { class: "lg-mfa-ic" }, icon(ICONS.lock, 26)),
      h("p", { class: "lg-mfa-t" }, "التحقق بخطوتين"), h("p", { class: "muted small" }, "أدخل الرمز المكوّن من 6 أرقام من تطبيق المصادقة، أو أحد رموز الاسترداد."),
      h("div", { class: "field" }, h("label", { for: "otp" }, "رمز التحقق"), code));
    const head = h("div", {}, h("h2", { id: "lg-h" }, "تسجيل الدخول"), h("p", { class: "muted small", id: "lg-sub" }, "أدخل بيانات حسابك للمتابعة"));
    function step() {
      const mfa = !!ticket;
      credBox.hidden = mfa; mfaBox.hidden = !mfa; back.hidden = !mfa; clearErr();
      u.required = !mfa; p.required = !mfa; code.required = mfa;
      head.querySelector("#lg-h").textContent = mfa ? "رمز التحقق" : "تسجيل الدخول"; head.querySelector("#lg-sub").hidden = mfa;
      btn.textContent = mfa ? "تأكيد الدخول" : "دخول"; if (mfa) { code.value = ""; code.focus(); }
    }

    const form = h("form", { class: "lg-form", onsubmit: async (e) => {
      e.preventDefault(); clearErr(); btn.disabled = true; const label = btn.textContent; btn.textContent = "جارٍ التحقق…"; btn.setAttribute("aria-busy", "true");
      try {
        let r;
        if (ticket) r = await api("/login/mfa", { method: "POST", body: { ticket, code: code.value.trim() } });
        else {
          r = await api("/login", { method: "POST", body: { username: u.value.trim(), password: p.value } });
          if (r.mfa_required) { ticket = r.ticket; step(); return; }
        }
        state.token = r.token; sessionStorage.setItem("acp_token", r.token); state.user = r.user;
        await boot();
      } catch (ex) {
        showErr(ex.message);
        if (ticket) { code.setAttribute("aria-invalid", "true"); code.select(); }
        else { u.setAttribute("aria-invalid", "true"); p.setAttribute("aria-invalid", "true"); p.value = ""; p.focus(); }
      } finally { btn.disabled = false; btn.removeAttribute("aria-busy"); btn.textContent = ticket ? "تأكيد الدخول" : "دخول"; }
    } }, head, credBox, mfaBox, err, btn, back,
      h("p", { class: "small muted lg-help" }, "نسيت كلمة المرور؟ تواصل مع مسؤول النظام لإعادة تعيينها."));

    const org = BRAND.name;
    const page = h("div", { class: "lg-page" },
      h("section", { class: "lg-brand", "aria-label": "تعريف بالمنصة" },
        h("div", { class: "lg-brand-in" },
          h("div", { class: "lg-id" }, BRAND.logo ? h("div", { class: "lg-logo" }, brandLogo("lg-logo-img")) : svgNode(MARK),
            h("div", {}, h("div", { class: "lg-word" }, "وصال"), h("div", { class: "lg-word-sub" }, "للاتصالات الإدارية"))),
          h("h1", { class: "lg-slogan" }, "كل مراسلة في مسار واضح"),
          h("p", { class: "lg-tag" }, "من الاستلام حتى الاعتماد والأرشفة، تتبّع كامل وصلاحيات دقيقة."),
          org ? h("p", { class: "lg-org" }, org) : null,
          BRAND.login_message ? h("p", { class: "lg-msg" }, BRAND.login_message) : null,
          svgNode(FLOW),
          h("ul", { class: "lg-feats" }, FEATURES.map(([ic, t]) => h("li", {}, icon(ICONS[ic], 20), h("span", {}, t)))))),
      h("div", { class: "lg-main" }, h("div", { class: "lg-card" }, form,
        h("p", { class: "small muted lg-note" }, "الدخول مقصور على المصرّح لهم. نشاطك على المنصة مسجَّل لأغراض التدقيق والأمن."))));
    // ألوان الهوية التي يضبطها المدير (إن وُجدت) تنعكس على الصفحة؛ وإلا تبقى الألوان المؤسسية الافتراضية
    const c = BRAND.colors || {};
    if (c.primary) { page.style.setProperty("--lg-orange", c.primary); page.style.setProperty("--lg-orange-d", c.primary); }
    if (c.side) { page.style.setProperty("--lg-navy", c.side); page.style.setProperty("--lg-navy-2", c.side); }
    return page;
  };
})();
