// جداول إدارية متقدمة (مستلهمة من Vuestic Admin) بتحسين تدريجي لكل الجداول دون تعديل أكواد الصفحات:
//  • قائمة المعاملات: فرز بالعمود على الخادم (كل النتائج لا الصفحة الحالية فقط)
//  • بقية الجداول: فرز بالنقر على العنوان، تصفية سريعة، إظهار/إخفاء الأعمدة، تصدير CSV، وتحويل إلى بطاقات على الجوال
(() => {
  const SERVER_SORT = { "رقم المعاملة": "ref", "موضوع الرسالة": "subject", "المرسل / الجهة": "party", "تاريخ الاستلام": "date", "التصنيف": "priority", "حالة المعاملة": "status" };
  const norm = (s) => (s || "").replace(/[ً-ْـ]/g, "").replace(/[أإآ]/g, "ا").replace(/ى/g, "ي").replace(/ة/g, "ه").toLowerCase();
  const txt = (c) => (c.textContent || "").replace(/\s+/g, " ").trim();
  const num = (s) => { const t = s.replace(/[٠-٩]/g, (d) => "٠١٢٣٤٥٦٧٨٩".indexOf(d)).replace(/[,٬٪%\s]/g, ""); return /^-?\d+(\.\d+)?$/.test(t) ? parseFloat(t) : null; };
  const cmp = (a, b) => { const x = num(a), y = num(b); if (x !== null && y !== null) return x - y; return a.localeCompare(b, "ar", { numeric: true, sensitivity: "base" }); };

  function setLabels(table) {
    const heads = [...table.tHead.rows[0].cells].map((c) => txt(c));
    table.classList.add("mtbl");
    [...table.tBodies].forEach((tb) => [...tb.rows].forEach((r) => [...r.cells].forEach((c, i) => c.setAttribute("data-label", heads[i] || ""))));
  }

  // ───── قائمة المعاملات: فرز من الخادم ─────
  function serverSort(table) {
    const q = new URLSearchParams((location.hash.split("?")[1] || "")), by = q.get("by"), dir = q.get("dir") || "asc";
    [...table.tHead.rows[0].cells].forEach((th) => {
      const key = SERVER_SORT[txt(th)]; if (!key) return;
      th.classList.add("sortable"); th.tabIndex = 0; 
      th.setAttribute("aria-sort", by === key ? (dir === "desc" ? "descending" : "ascending") : "none");
      const go = () => { const n = new URLSearchParams(location.hash.split("?")[1] || ""); n.set("by", key); n.set("dir", by === key && dir === "asc" ? "desc" : "asc"); n.delete("page"); location.hash = "#/list?" + n.toString(); };
      th.addEventListener("click", go); th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    });
  }

  // ───── بقية الجداول: فرز وتصفية وأعمدة وتصدير ─────
  function clientTable(table) {
    const body = table.tBodies[0];
    if (!body || body.rows.length < 2) return;
    const wrap = table.closest(".tbl-wrap") || table.parentElement, heads = [...table.tHead.rows[0].cells];
    const original = [...body.rows];
    const count = h("span", { class: "small muted", role: "status" }, original.length + " صف");

    heads.forEach((th, i) => {
      if (!txt(th) || th.querySelector("input,button")) return;
      th.classList.add("sortable"); th.tabIndex = 0; th.setAttribute("aria-sort", "none");
      const sort = () => {
        const cur = th.getAttribute("aria-sort"), dir = cur === "ascending" ? "descending" : "ascending";
        heads.forEach((x) => x.setAttribute("aria-sort", "none")); th.setAttribute("aria-sort", dir);
        const rows = [...body.rows].sort((a, b) => cmp(txt(a.cells[i] || a), txt(b.cells[i] || b)) * (dir === "ascending" ? 1 : -1));
        body.append(...rows);
      };
      th.addEventListener("click", sort); th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); } });
    });

    const filter = h("input", { type: "search", class: "tf-in", placeholder: "تصفية سريعة في الجدول…", "aria-label": "تصفية سريعة في الجدول", oninput: () => {
      const q = norm(filter.value.trim()); let n = 0;
      original.forEach((r) => { const show = !q || norm(txt(r)).includes(q); r.hidden = !show; if (show) n++; });
      count.textContent = n + " من " + original.length + " صف";
    } });
    const hiddenCols = new Set();
    const applyCols = () => [...table.rows].forEach((r) => [...r.cells].forEach((c, i) => c.classList.toggle("col-off", hiddenCols.has(i))));
    const colMenu = h("details", { class: "colmenu" }, h("summary", { class: "btn ghost sm" }, "⚙ الأعمدة"),
      h("div", { class: "card colbox" }, heads.map((th, i) => txt(th) ? h("label", { class: "opt" }, h("input", { type: "checkbox", checked: true, onchange: (e) => { e.target.checked ? hiddenCols.delete(i) : hiddenCols.add(i); applyCols(); } }), txt(th)) : null)));
    const exp = h("button", { type: "button", class: "btn ghost sm", onclick: () => {
      const rows = [heads.map(txt).filter((_, i) => !hiddenCols.has(i) && txt(heads[i]))];
      [...body.rows].filter((r) => !r.hidden).forEach((r) => rows.push([...r.cells].map((c, i) => (hiddenCols.has(i) || !txt(heads[i]) ? null : txt(c))).filter((x) => x !== null)));
      const csv = "﻿" + rows.map((r) => r.map((v) => '"' + String(v).replace(/"/g, '""').replace(/^([=+\-@])/, "'$1") + '"').join(",")).join("\r\n");
      downloadBlob(new Blob([csv], { type: "text/csv;charset=utf-8" }), "جدول.csv");
    } }, "⬇ CSV");
    wrap.before(h("div", { class: "tbar noprint" }, filter, colMenu, exp, count));
  }

  // ───── التركيب ─────
  function enhance() {
    document.querySelectorAll("main table:not([data-enh])").forEach((table) => {
      table.setAttribute("data-enh", "1");
      if (!table.tHead || !table.tHead.rows[0] || table.querySelector("[colspan],[rowspan]") || table.closest(".letter,.label-sheet,.colbox")) return;
      try {
        setLabels(table);
        if (table.classList.contains("mail") && location.hash.startsWith("#/list")) serverSort(table); else clientTable(table);
      } catch (e) { /* التحسين اختياري: لا يكسر الصفحة */ }
    });
  }
  let t = null;
  new MutationObserver(() => { clearTimeout(t); t = setTimeout(enhance, 60); }).observe(document.getElementById("app"), { childList: true, subtree: true });
})();

// سباق التحميل: إن اكتمل أول عرض (تسجيل الدخول أو الصفحة) قبل تحميل ملفات التحسين اللاحقة، يُعاد رسمه مرة واحدة بها
(() => {
  const late = document.querySelector("#app > .login") || (document.querySelector("nav.side") && !document.querySelector(".topbar"));
  if (late && typeof render === "function") render();
})();
