// الصفحة الرئيسية المحسّنة (تُحمَّل بعد app.js): تضيف طبقة «ما يهمّني الآن» فوق لوحة التحكم القائمة دون حذف أي منها،
// مع وضع الخصوصية، واختصارات لوحة المفاتيح، ورابط تخطي المحتوى. راجع docs/UX_DESIGN.md.
(() => {
  const baseDashboard = dashboardView;
  const baseCorrTable = corrTable;

  // ───── وضع الخصوصية: يطمس موضوع المراسلات المصنَّفة (سري/سري للغاية) حتى المرور عليها ─────
  const privacyOn = () => { try { return localStorage.getItem("acp_privacy") === "1"; } catch { return false; } };
  const setPrivacy = (on) => { document.body.classList.toggle("privacy", on); try { localStorage.setItem("acp_privacy", on ? "1" : "0"); } catch { /* */ } };
  setPrivacy(privacyOn());
  corrTable = (items, compact) => {
    const el = baseCorrTable(items, compact);
    el.querySelectorAll?.("tbody tr").forEach((tr, i) => { if (items[i] && items[i].conf_level > 0) tr.classList.add("sens"); });
    return el;
  };

  // ───── اختصارات لوحة المفاتيح وتخطي المحتوى ─────
  const skip = h("a", { class: "skip", href: "#", onclick: (e) => { e.preventDefault(); const m = document.querySelector("main"); if (m) { m.tabIndex = -1; m.focus(); } } }, "تخطي إلى المحتوى");
  document.body.prepend(skip);
  const SHORT = [["/", "البحث بالباركود/الرقم"], ["N", "تسجيل مراسلة جديدة"], ["D", "لوحة التحكم"], ["T", "مهامي"], ["L", "المعاملات"], ["B", "الإشعارات"], ["P", "تبديل وضع الخصوصية"], ["?", "عرض هذه القائمة"], ["Alt+Shift+Q", "خروج سريع"]];
  const showHelp = () => {
    if (document.getElementById("kbhelp")) return document.getElementById("kbhelp").remove();
    const close = () => { box.remove(); };
    const box = h("div", { id: "kbhelp", class: "kbhelp", role: "dialog", "aria-modal": "true", "aria-label": "اختصارات لوحة المفاتيح", onclick: (e) => e.target === box && close() },
      h("div", { class: "card" }, h("h3", {}, "اختصارات لوحة المفاتيح"), h("table", {}, h("tbody", {}, SHORT.map(([k, d]) => h("tr", {}, h("td", {}, h("kbd", {}, k)), h("td", {}, d))))),
        h("button", { class: "btn ghost", onclick: close }, "إغلاق (Esc)")));
    document.body.append(box); box.querySelector("button").focus();
  };
  window.__acp = { showHelp, setPrivacy };
  document.addEventListener("keydown", (e) => {
    if (typeof state === "undefined" || !state.user) return;
    if (e.key === "Escape") { const k = document.getElementById("kbhelp"); if (k) k.remove(); return; }
    if (e.altKey && e.shiftKey && e.code === "KeyQ") { e.preventDefault(); doLogout(); return; }
    const t = e.target;
    if (e.ctrlKey || e.metaKey || e.altKey || (t && (["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.isContentEditable))) return;
    const go = { d: "#/dashboard", t: "#/tasks", l: "#/list", b: "#/notifications" }[e.key.toLowerCase()];
    if (go) { location.hash = go; return; }
    if (e.key.toLowerCase() === "p") { setPrivacy(!document.body.classList.contains("privacy")); toast(document.body.classList.contains("privacy") ? "وضع الخصوصية: مفعّل" : "وضع الخصوصية: معطّل"); }
    else if (e.key === "?") showHelp();
  });

  // ───── الصفحة الرئيسية ─────
  const greeting = () => { const hr = new Date().getHours(); return hr < 12 ? "صباح الخير" : hr < 18 ? "مساء الخير" : "مساء النور"; };
  const skeleton = () => h("div", { class: "skel", "aria-hidden": "true" });
  const section = (title, body, action) => h("section", { class: "card hsec", "aria-label": title }, h("div", { class: "row between" }, h("h3", {}, title), action || null), body);
  const tile = (n, l, href, tone) => h("a", { class: "card stat" + (tone && n ? " " + tone : ""), href }, h("div", { class: "n" }, n ?? "—"), h("div", { class: "l" }, l));

  dashboardView = async () => {
    const role = state.user.role, leader = ["admin", "manager"].includes(role);
    const [tasksR, notifR, mfaR, kpiR, orgR] = await Promise.allSettled([api("/tasks"), api("/notifications?limit=6"), api("/me/mfa"),
      isStaff() || can("export") ? api("/kpi") : Promise.reject(), api("/org/profile")]);
    const val = (r, d) => (r.status === "fulfilled" ? r.value : d);
    const tasks = val(tasksR, []), notif = val(notifR, { items: [], unread: 0 }), mfa = val(mfaR, null), kpi = val(kpiR, null), org = val(orgR, {});

    const quick = [["✉️ تسجيل مراسلة", "#/new", "N"], ["📥 الوارد", "#/list?direction=incoming"], ["📤 الصادر", "#/list?direction=outgoing"], ["🔎 بحث متقدم", "#/search"],
      ...(leader ? [["✍️ واجهة القيادة", "#/executive"]] : []), ...(isStaff() || can("export") ? [["📊 التقارير", "#/reports"]] : [])];

    const head = h("div", { class: "home-head" }, h("div", {}, h("h2", {}, `${greeting()}، ${state.user.full_name}`),
      h("div", { class: "muted small" }, new Date().toLocaleDateString("ar-SA", { weekday: "long", year: "numeric", month: "long", day: "numeric" }), org.org_name ? " — " + org.org_name : "")),
      h("div", { class: "row" }, h("button", { class: "btn ghost sm", "aria-pressed": String(document.body.classList.contains("privacy")), title: "طمس المراسلات المصنَّفة (P)",
        onclick: (e) => { const on = !document.body.classList.contains("privacy"); setPrivacy(on); e.currentTarget.setAttribute("aria-pressed", String(on)); render(); } }, "🙈 وضع الخصوصية"),
      h("button", { class: "btn ghost sm", title: "اختصارات لوحة المفاتيح (؟)", onclick: showHelp }, "⌨ الاختصارات")));

    const quickRow = h("nav", { class: "quick", "aria-label": "اختصارات سريعة" }, quick.map(([l, href, k]) => h("a", { class: "btn ghost", href, title: k ? "اختصار: " + k : null }, l)));

    const urgent = tasks.filter((t) => t.priority !== "normal" || (t.transfer_due && t.transfer_due < today()));
    const taskList = tasks.length ? h("ul", { class: "mini" }, tasks.slice(0, 5).map((t) => h("li", { class: t.conf_level > 0 ? "sens" : "" },
      h("a", { href: "#/c/" + t.id, class: "ltr" }, t.ref_no), " ", h("strong", {}, t.subject), t.priority !== "normal" ? [" ", prioPill(t.priority)] : null,
      h("div", { class: "small muted" }, (t.instruction ? t.instruction + " — " : "") + "من " + t.from_name + (t.transfer_due ? " — الاستحقاق " + t.transfer_due : ""))))) : h("div", { class: "empty" }, "لا مهام معلّقة 🎉");

    const alerts = [];
    if (urgent.length) alerts.push(["warn", `${urgent.length} من مهامك عاجلة أو متأخرة`, "#/tasks"]);
    if (kpi && kpi.sla_breached_open) alerts.push(["bad", `${kpi.sla_breached_open} معاملة تجاوزت مهلة SLA`, "#/list?overdue=1"]);
    if (kpi && kpi.overdue) alerts.push(["warn", `${kpi.overdue} معاملة متأخرة عن موعدها`, "#/list?overdue=1"]);
    if (notif.unread) alerts.push(["info", `${notif.unread} إشعار غير مقروء`, "#/notifications"]);
    if (mfa && mfa.required && !mfa.enabled) alerts.push(["bad", "حسابك يتطلب تفعيل المصادقة الثنائية", "#/security"]);
    const alertBox = alerts.length ? h("div", { class: "alerts", role: "status" }, alerts.map(([tone, txt, href]) => h("a", { class: "alert " + tone, href }, txt, " ←"))) : h("div", { class: "muted small" }, "✔ لا تنبيهات حاليًا");

    const kpiRow = kpi ? h("div", { class: "grid g4" }, tile(kpi.overdue, "متأخرة عن موعدها", "#/list?overdue=1", "warn"), tile(kpi.sla_breached_open, "تجاوزت مهلة SLA", "#/list?overdue=1", "warn"),
      tile(kpi.sla_policy_compliance_pct != null ? kpi.sla_policy_compliance_pct + "٪" : kpi.sla_compliance_pct != null ? kpi.sla_compliance_pct + "٪" : "—", "الالتزام بالمهل", "#/reports"),
      tile(kpi.avg_close_days != null ? kpi.avg_close_days : "—", "متوسط أيام الإغلاق", "#/reports")) : null;

    const activity = notif.items.length ? h("ul", { class: "mini" }, notif.items.map((n) => h("li", { class: n.read_at ? "" : "unread" },
      n.corr_id ? h("a", { href: "#/c/" + n.corr_id }, n.title) : h("span", {}, n.title), h("div", { class: "small muted" }, fmtDT(n.created_at))))) : h("div", { class: "empty" }, "لا نشاط حديث");

    const secState = h("ul", { class: "secstate" },
      h("li", { class: mfa && mfa.enabled ? "ok" : "bad" }, mfa ? (mfa.enabled ? "✔ المصادقة الثنائية مفعّلة" : "⚠ المصادقة الثنائية غير مفعّلة") : "— المصادقة الثنائية"),
      h("li", { class: "ok" }, "🌐 عنوان اتصالك: ", h("span", { class: "ltr" }, state.user.ip || "—")),
      h("li", { class: "ok" }, "⏱ تنتهي الجلسة تلقائيًا بعد 8 ساعات (أو الخمول إن فُعِّل)"),
      h("li", {}, "🙈 وضع الخصوصية: ", document.body.classList.contains("privacy") ? "مفعّل" : "معطّل"));
    const securityCard = section("حالة أمان حسابك", [secState, h("div", { class: "row" }, h("a", { class: "btn ghost sm", href: "#/security" }, "الأمان والتفضيلات"),
      h("button", { class: "btn danger sm", title: "Alt+Shift+Q", onclick: doLogout }, "⏻ خروج سريع"))]);

    let details;
    try { details = await baseDashboard(); } catch (e) { details = [h("div", { class: "card muted" }, "تعذّر تحميل الإحصاءات التفصيلية")]; }
    // لوحة التحكم القائمة كما هي (دون عنوانها المكرر) تحت قسم «تفاصيل الإحصاءات»
    const rest = Array.isArray(details) ? details.slice(1) : [details];

    return [head, quickRow, alertBox,
      h("div", { class: "grid g2" }, section("بانتظار إجرائي", taskList, h("a", { href: "#/tasks", class: "small" }, "عرض الكل")), section("آخر النشاط", activity, h("a", { href: "#/notifications", class: "small" }, "كل الإشعارات"))),
      kpiRow, securityCard, h("h3", { class: "home-sub" }, "تفاصيل الإحصاءات"), ...rest];
  };
})();
