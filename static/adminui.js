// ميزات مستلهمة من vue-element-admin بالعربية ودون Vue: شريط علوي (زر طيّ القائمة، مسار التنقل، بحث في القوائم، ملء الشاشة،
// كثافة العرض، جرس الإشعارات، سجل أخطاء الواجهة)، وعلامات الصفحات المفتوحة (Tags View)، وزر العودة للأعلى. تُحمَّل بعد account.js ولا تغيّر ما سبق.
(() => {
  const baseShell = shell;
  const store = {
    get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
    set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* */ } },
    sget: (k, d) => { try { return JSON.parse(sessionStorage.getItem(k)) ?? d; } catch { return d; } },
    sset: (k, v) => { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch { /* */ } },
  };
  const stripEmoji = (t) => (t || "").replace(/^[^\p{L}\p{N}]+/u, "").trim();

  // ───── سجل أخطاء الواجهة (للمدير) ─────
  const errors = store.sget("acp_errs", []);
  const logErr = (msg) => { errors.unshift({ at: new Date().toISOString().slice(0, 19).replace("T", " "), msg: String(msg).slice(0, 300), page: location.hash }); errors.length = Math.min(errors.length, 30); store.sset("acp_errs", errors); };
  window.addEventListener("error", (e) => logErr(e.message));
  window.addEventListener("unhandledrejection", (e) => logErr((e.reason && e.reason.message) || e.reason));
  EXT_ROUTES["/errors"] = async () => [h("div", { class: "row" }, h("h2", {}, "سجل أخطاء الواجهة"), h("button", { class: "btn ghost sm", onclick: () => { errors.length = 0; store.sset("acp_errs", errors); render(); } }, "مسح السجل")),
    h("p", { class: "muted small" }, "أخطاء الجافاسكربت التي وقعت في متصفحك خلال هذه الجلسة (للدعم الفني)."),
    errors.length ? h("div", { class: "card tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["الوقت", "الصفحة", "الخطأ"].map((t) => h("th", {}, t)))),
      h("tbody", {}, errors.map((e) => h("tr", {}, h("td", { class: "small ltr" }, e.at), h("td", { class: "small ltr" }, e.page), h("td", {}, e.msg)))))) : h("div", { class: "card empty" }, "لا أخطاء 🎉")];

  // ───── كثافة العرض وملء الشاشة ─────
  const applyDensity = () => document.body.classList.toggle("compact", store.get("acp_compact", false));
  applyDensity();
  const toggleFullscreen = () => { try { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen(); } catch { /* غير مدعوم */ } };

  // ───── علامات الصفحات المفتوحة ─────
  const pageTitle = (el, navLabel) => navLabel || stripEmoji((el.querySelector("main h2") || {}).textContent) || "صفحة";
  function updateTags(hash, title) {
    let tags = store.sget("acp_tags", []);
    if (!tags.some((t) => t.hash === "#/dashboard")) tags.unshift({ hash: "#/dashboard", title: "لوحة التحكم", affix: true });
    const i = tags.findIndex((t) => t.hash === hash);
    if (i >= 0) tags[i].title = title; else tags.push({ hash, title });
    if (tags.length > 12) { const k = tags.findIndex((t) => !t.affix && t.hash !== hash); if (k >= 0) tags.splice(k, 1); }
    store.sset("acp_tags", tags); return tags;
  }
  const closeTag = (hash) => {
    const tags = store.sget("acp_tags", []), i = tags.findIndex((t) => t.hash === hash);
    if (i < 0) return;
    const wasCur = (location.hash || "#/dashboard") === hash;
    tags.splice(i, 1); store.sset("acp_tags", tags);
    if (wasCur) location.hash = (tags[i] || tags[i - 1] || { hash: "#/dashboard" }).hash; else render();
  };

  // ───── بحث القوائم ─────
  function menuIndex(el) {
    const seen = new Map();
    el.querySelectorAll(".navgroup").forEach((g) => { const grp = stripEmoji((g.querySelector("summary span") || {}).textContent);
      g.querySelectorAll(".navitems a").forEach((a) => { const href = a.getAttribute("href"); if (!seen.has(href)) seen.set(href, { href, label: a.firstChild.textContent.trim(), group: grp }); }); });
    if (state.user.role === "admin" && typeof ADMIN_TABS !== "undefined") ADMIN_TABS.forEach(([k, label]) => { const href = "#/admin/" + k; if (!seen.has(href)) seen.set(href, { href, label, group: "إعدادات النظام" }); });
    [["#/about", "من نحن", "حسابي"], ["#/security", "الأمان والتفضيلات", "حسابي"], ["#/password", "تغيير كلمة المرور", "حسابي"]].forEach(([href, label, group]) => { if (!seen.has(href)) seen.set(href, { href, label, group }); });
    return [...seen.values()];
  }
  const norm = (s) => s.replace(/[ً-ْـ]/g, "").replace(/[أإآ]/g, "ا").replace(/ى/g, "ي").replace(/ة/g, "ه").toLowerCase();

  function headerSearch(index) {
    const box = h("div", { class: "hs-list", role: "listbox", hidden: true });
    const input = h("input", { type: "search", class: "hs-in", placeholder: "بحث في القوائم (Ctrl+K)", "aria-label": "بحث في القوائم", autocomplete: "off", id: "hs-input" });
    let cur = -1, hits = [];
    const show = () => {
      const q = norm(input.value.trim()); hits = q ? index.filter((x) => norm(x.label + " " + x.group).includes(q)).slice(0, 8) : [];
      cur = hits.length ? 0 : -1; box.hidden = !hits.length;
      box.replaceChildren(...hits.map((x, i) => h("a", { href: x.href, role: "option", class: "hs-item" + (i === cur ? " on" : ""), onclick: () => { input.value = ""; box.hidden = true; } }, h("strong", {}, x.label), h("span", { class: "small muted" }, x.group))));
    };
    input.addEventListener("input", show);
    input.addEventListener("keydown", (e) => {
      if (!hits.length) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); cur = (cur + (e.key === "ArrowDown" ? 1 : -1) + hits.length) % hits.length; [...box.children].forEach((c, i) => c.classList.toggle("on", i === cur)); }
      else if (e.key === "Enter") { e.preventDefault(); location.hash = hits[Math.max(cur, 0)].href; input.value = ""; box.hidden = true; input.blur(); }
      else if (e.key === "Escape") { input.value = ""; box.hidden = true; }
    });
    input.addEventListener("blur", () => setTimeout(() => (box.hidden = true), 150));
    return h("div", { class: "hs" }, input, box);
  }
  document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && typeof state !== "undefined" && state.user) { const i = document.getElementById("hs-input"); if (i) { e.preventDefault(); i.focus(); } } });

  // ───── العودة للأعلى ─────
  const toTop = h("button", { class: "totop", type: "button", hidden: true, "aria-label": "العودة إلى أعلى الصفحة", title: "العودة للأعلى", onclick: () => window.scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }) }, "↑");
  document.body.append(toTop);
  window.addEventListener("scroll", () => { toTop.hidden = window.scrollY < 400; }, { passive: true });

  // ───── تركيب الشريط العلوي ─────
  shell = function (content, taskCount) {
    const el = baseShell(content, taskCount);
    const main = el.querySelector("main"), side = el.querySelector("nav.side");
    if (!main || !side) return el;
    const hash = location.hash || "#/dashboard", index = menuIndex(el);
    const hit = index.find((x) => x.href === hash.split("?")[0] || x.href === hash);
    const title = pageTitle(el, hit && hit.label);
    const tags = updateTags(hash, title);

    const hidden = store.get("acp_navhidden", false);
    el.classList.toggle("nav-hidden", hidden);
    const burger = h("button", { class: "tb-btn", type: "button", "aria-label": "إظهار/إخفاء القائمة الجانبية", "aria-expanded": String(!hidden), title: "القائمة الجانبية", onclick: (e) => {
      const on = !el.classList.contains("nav-hidden"); el.classList.toggle("nav-hidden", on); store.set("acp_navhidden", on); e.currentTarget.setAttribute("aria-expanded", String(!on)); } }, "☰");

    const crumbs = h("nav", { class: "crumbs", "aria-label": "مسار التنقل" }, h("a", { href: "#/dashboard" }, "الرئيسية"),
      ...(hit && hit.group && hit.group !== "الرئيسية" ? [h("span", { "aria-hidden": "true" }, "‹"), h("span", {}, hit.group)] : []),
      ...(hash.split("?")[0] !== "#/dashboard" ? [h("span", { "aria-hidden": "true" }, "‹"), h("span", { "aria-current": "page", class: "cur" }, title)] : []));

    const bellN = h("span", { class: "badge-n", hidden: !state.unread }, state.unread || "");
    const bell = h("a", { class: "tb-btn", href: "#/notifications", "aria-label": "الإشعارات", title: "الإشعارات" }, "🔔", bellN);
    setTimeout(() => { const t = setInterval(() => { if (!bellN.isConnected) return clearInterval(t); bellN.textContent = state.unread || ""; bellN.hidden = !state.unread; }, 5000); }, 0);
    const dens = h("button", { class: "tb-btn", type: "button", title: "كثافة العرض: مريح / مضغوط", "aria-label": "تبديل كثافة العرض", "aria-pressed": String(store.get("acp_compact", false)),
      onclick: (e) => { const on = !store.get("acp_compact", false); store.set("acp_compact", on); applyDensity(); e.currentTarget.setAttribute("aria-pressed", String(on)); } }, "↕");
    const full = h("button", { class: "tb-btn", type: "button", title: "ملء الشاشة", "aria-label": "تبديل ملء الشاشة", onclick: toggleFullscreen }, "⛶");
    const bug = state.user.role === "admin" ? h("a", { class: "tb-btn", href: "#/errors", title: "سجل أخطاء الواجهة", "aria-label": "سجل أخطاء الواجهة" }, "🐞", errors.length ? h("span", { class: "badge-n" }, errors.length) : null) : null;

    const bar = h("div", { class: "topbar" }, burger, crumbs, h("div", { class: "tb-sp" }), headerSearch(index), dens, full, bug, bell);
    const tagRow = h("div", { class: "tagsview", role: "tablist", "aria-label": "الصفحات المفتوحة" },
      tags.map((t) => h("span", { class: "tag" + (t.hash === hash ? " on" : ""), role: "presentation" },
        h("a", { href: t.hash, role: "tab", "aria-selected": String(t.hash === hash) }, t.title),
        t.affix ? null : h("button", { type: "button", class: "tag-x", "aria-label": "إغلاق " + t.title, onclick: () => closeTag(t.hash) }, "×"))),
      tags.length > 1 ? h("button", { type: "button", class: "tag-more", title: "إغلاق الصفحات الأخرى", onclick: () => { store.sset("acp_tags", tags.filter((t) => t.affix || t.hash === hash)); render(); } }, "إغلاق الأخرى") : null);

    const wrap = h("div", { class: "mainwrap" }, h("div", { class: "topwrap" }, bar, tagRow));
    el.replaceChild(wrap, main);
    wrap.append(main);
    return el;
  };
})();
