"use strict";
(async () => {
  const root = document.getElementById("doc");
  const el = (t, c, txt) => { const n = document.createElement(t); if (c) n.className = c; if (txt != null) n.textContent = txt; return n; };
  try {
    const spec = await (await fetch("/api/openapi.json")).json();
    root.replaceChildren(el("h1", "", spec.info.title), el("p", "muted", spec.info.description), el("p", "small", "الإصدار " + spec.info.version + " • OpenAPI " + spec.openapi + " • " + Object.keys(spec.paths).length + " مسارًا"));
    const filter = el("input"); filter.placeholder = "ابحث في المسارات…"; filter.setAttribute("aria-label", "بحث"); root.append(filter);
    const list = el("div"); root.append(list);
    const colors = { get: "#2563eb", post: "#16a34a", put: "#d97706", delete: "#dc2626" };
    const render = () => {
      list.replaceChildren();
      const q = filter.value.trim().toLowerCase();
      for (const [path, ops] of Object.entries(spec.paths)) {
        for (const [m, op] of Object.entries(ops)) {
          if (q && !(path + " " + op.summary).toLowerCase().includes(q)) continue;
          const row = el("div", "card");
          const badge = el("span", "pill", m.toUpperCase()); badge.style.background = colors[m] || "#64748b"; badge.style.color = "#fff";
          const p = el("code", "ltr", path); p.style.marginInline = "8px";
          row.append(badge, p, el("span", "small muted", op.summary));
          const meta = [];
          if (op.security) meta.push("يتطلب مصادقة"); else meta.push("عام");
          if (op["x-roles"]) meta.push("أدوار: " + op["x-roles"].join("، "));
          row.append(el("div", "small muted", meta.join(" • ")));
          list.append(row);
        }
      }
    };
    filter.addEventListener("input", render); render();
  } catch (e) { root.replaceChildren(el("h1", "", "تعذّر تحميل التوثيق"), el("p", "", String(e))); }
})();