// أيقونات Tabler (محلية) ومكونات بصرية مضافة: تُحوَّل رموز emoji الشائعة إلى أيقونات SVG تلقائيًا دون تعديل أكواد الصفحات،
// مع إبقاء emoji بديلًا إن غابت الأيقونة. + دالة sparkline وحالات الفراغ. (تُحمَّل بعد icons.js)
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const ic = (name, size = 18) => {
    const inner = TABLER_ICONS[name]; if (!inner) return null;
    const s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("width", size); s.setAttribute("height", size); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "2"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round"); s.setAttribute("aria-hidden", "true"); s.setAttribute("focusable", "false"); s.setAttribute("class", "ti");
    s.innerHTML = inner; return s;  // innerHTML من ثوابت icons.js المولَّدة فقط (لا مدخلات مستخدم)
  };
  window.ic = ic;

  const MAP = { "🏠": "home", "✉️": "mail", "✉": "mail", "📊": "chart-bar", "🏢": "building", "🗂": "folders", "⚙️": "settings", "⚙": "settings", "👤": "user", "☰": "menu-2", "🔔": "bell", "⛶": "maximize",
    "↕": "arrows-vertical", "🐞": "bug", "🔐": "lock", "🔑": "key", "ℹ️": "info-circle", "🙈": "eye-off", "⌨": "keyboard", "⏻": "power", "📥": "inbox", "📤": "send", "🔎": "search", "✍️": "signature",
    "📷": "camera", "🖨": "printer", "🏷": "tag", "📄": "file-text", "📂": "folder", "↩": "arrow-back-up", "↪": "arrow-forward-up", "⬇": "download", "🕓": "clock", "⏰": "alarm", "📎": "paperclip",
    "💾": "device-floppy", "🌐": "world", "⏱": "clock", "👁": "eye", "🛡": "shield-check" };
  const KEYS = Object.keys(MAP).sort((a, b) => b.length - a.length);
  // أين نحوّل: عناصر التنقل والأزرار والقوائم فقط (لا نصوص المحتوى)
  const SEL = ".navgroup summary > span:first-child, .tb-btn, .quick a, .acct-item, .home-head .btn, .detail-tools .btn, .row > .btn.ghost.sm, .iconbtn, .colmenu summary, .tbar .btn";

  function iconify(root) {
    (root.querySelectorAll ? [...root.querySelectorAll(SEL), ...(root.matches && root.matches(SEL) ? [root] : [])] : []).forEach((el) => {
      if (el.dataset.ti) return;
      const t = el.firstChild;
      if (!t || t.nodeType !== 3) return;
      const k = KEYS.find((x) => t.nodeValue.trimStart().startsWith(x));
      if (!k) return;
      const svg = ic(MAP[k], el.classList.contains("tb-btn") || el.classList.contains("iconbtn") ? 20 : 17);
      if (!svg) return;
      el.dataset.ti = "1";
      t.nodeValue = t.nodeValue.trimStart().slice(k.length).replace(/^️/, "").trimStart();
      svg.classList.add("ti-lead"); el.insertBefore(svg, el.firstChild);
    });
    root.querySelectorAll && root.querySelectorAll(".empty:not([data-ti])").forEach((e) => {
      e.dataset.ti = "1"; const i = ic("inbox", 38); if (i) { i.classList.add("ti-empty"); e.prepend(i); }
    });
  }
  window.iconify = iconify;

  let t = null; const pending = new Set();
  new MutationObserver((muts) => {
    muts.forEach((m) => m.addedNodes.forEach((n) => { if (n.nodeType === 1) pending.add(n); }));
    clearTimeout(t); t = setTimeout(() => { pending.forEach((n) => n.isConnected && iconify(n)); pending.clear(); }, 30);
  }).observe(document.body, { childList: true, subtree: true });
  iconify(document.body);

  // خط اتجاه مصغّر (Sparkline) بـSVG: قيم رقمية → مسار + نقطة أخيرة
  window.sparkline = (vals, w = 110, hgt = 34) => {
    const n = vals.length; if (n < 2) return null;
    const max = Math.max(...vals), min = Math.min(...vals), span = max - min || 1, pad = 3;
    const pts = vals.map((v, i) => [pad + (i * (w - 2 * pad)) / (n - 1), hgt - pad - ((v - min) / span) * (hgt - 2 * pad)]);
    const s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", `0 0 ${w} ${hgt}`); s.setAttribute("width", w); s.setAttribute("height", hgt); s.setAttribute("class", "spark"); s.setAttribute("aria-hidden", "true");
    s.innerHTML = `<polyline fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" points="${pts.map((p) => p.map((x) => x.toFixed(1)).join(",")).join(" ")}"/>` +
      `<circle cx="${pts[n - 1][0].toFixed(1)}" cy="${pts[n - 1][1].toFixed(1)}" r="3" fill="currentColor"/>`;
    return s;
  };
})();
