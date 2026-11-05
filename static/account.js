// قائمة الحساب: زر «مدير النظام» (واسم أي مستخدم) صار فعّالًا في القائمة الجانبية، مع زر عائم أسفل الشاشة يعمل في كل الصفحات.
// يفتح قائمة: الأمان والتفضيلات، تغيير كلمة المرور، من نحن، وضع الخصوصية، الاختصارات، تسجيل الخروج. (تُحمَّل بعد home.js)
(() => {
  const baseShell = shell;
  const initial = () => (state.user.full_name || "؟").trim().charAt(0);
  let menu = null, opener = null;

  const closeMenu = (focusBack) => {
    if (!menu) return;
    menu.remove(); menu = null;
    document.querySelectorAll("[data-acct]").forEach((b) => b.setAttribute("aria-expanded", "false"));
    if (focusBack && opener) opener.focus();
  };

  const openMenu = (btn) => {
    if (menu) return closeMenu(true);
    opener = btn;
    const item = (label, fn, cls) => h("button", { class: "acct-item " + (cls || ""), role: "menuitem", type: "button", onclick: () => { closeMenu(); fn(); } }, label);
    const go = (hash) => () => { location.hash = hash; };
    const priv = document.body.classList.contains("privacy");
    menu = h("div", { class: "acct-menu", role: "menu", "aria-label": "قائمة الحساب" },
      h("div", { class: "acct-head" }, h("div", { class: "acct-av big", "aria-hidden": "true" }, initial()),
        h("div", {}, h("strong", {}, state.user.full_name), h("div", { class: "small muted" }, L.role[state.user.role]), h("div", { class: "small muted ltr" }, state.user.username || ""))),
      item("🔐 الأمان والتفضيلات", go("#/security")), item("🔑 تغيير كلمة المرور", go("#/password")), item("ℹ️ من نحن", go("#/about")),
      state.user.role === "admin" ? item("⚙️ الهوية والترويسة", go("#/admin/branding")) : null,
      item(priv ? "🙈 إيقاف وضع الخصوصية" : "🙈 تفعيل وضع الخصوصية", () => { window.__acp.setPrivacy(!priv); render(); }),
      item("⌨ اختصارات لوحة المفاتيح", () => window.__acp.showHelp()),
      item("⏻ تسجيل الخروج", doLogout, "danger"));
    document.body.append(menu);
    // تموضع: بجوار الزر ضمن حدود الشاشة
    const r = btn.getBoundingClientRect(), mw = menu.offsetWidth, mh = menu.offsetHeight;
    menu.style.top = Math.max(8, Math.min(innerHeight - mh - 8, r.top - (r.top > innerHeight / 2 ? mh + 6 : -r.height - 6))) + "px";
    menu.style.left = Math.max(8, Math.min(innerWidth - mw - 8, r.left + r.width / 2 - mw / 2)) + "px";
    document.querySelectorAll("[data-acct]").forEach((b) => b.setAttribute("aria-expanded", String(b === btn)));
    const items = [...menu.querySelectorAll("[role=menuitem]")];
    items[0].focus();
    menu.addEventListener("keydown", (e) => {
      const i = items.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
      else if (e.key === "Tab") closeMenu();
    });
  };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && menu) closeMenu(true); });
  document.addEventListener("click", (e) => { if (menu && !menu.contains(e.target) && !e.target.closest("[data-acct]")) closeMenu(); });
  window.addEventListener("hashchange", () => closeMenu());

  const trigger = (cls, children, label) => h("button", { class: cls, type: "button", "data-acct": "1", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": label, title: label,
    onclick: (e) => openMenu(e.currentTarget) }, children);

  shell = function (content, taskCount) {
    closeMenu();
    const el = baseShell(content, taskCount);
    const who = el.querySelector(".who");
    if (who && who.firstElementChild) {  // اسم المستخدم + دوره يصيران زرًا واحدًا فعّالًا
      const name = who.children[0], role = who.children[1];
      const btn = trigger("acct-btn", [h("span", { class: "acct-av", "aria-hidden": "true" }, initial()), h("span", { class: "acct-txt" }, h("strong", {}, state.user.full_name), h("span", { class: "small" }, L.role[state.user.role])), h("span", { "aria-hidden": "true" }, "▾")], "قائمة الحساب: " + state.user.full_name);
      who.replaceChild(btn, name); role.remove();
    }
    el.append(trigger("acct-fab", h("span", { class: "acct-av", "aria-hidden": "true" }, initial()), "قائمة الحساب"));
    return el;
  };
})();
