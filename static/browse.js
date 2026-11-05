// استعراض الفهارس (مستلهم من Paperless-ngx) + تقويم المواعيد (مستلهم من AdminLTE) + التحرير الجماعي + العروض المحفوظة في الرئيسية.
// كله بالعربية وإضافة فوق ما سبق؛ الواجهة الخلفية: ext_browse.py. (تُحمَّل بعد todo.js)
(() => {
  const arNum = (n) => String(n);
  // ───────────── الفهارس ─────────────
  const KINDS = [["party", "الجهات المراسِلة", "تجميع كل المراسلات حسب الجهة"], ["tag", "الوسوم", "التصنيف الحر بالوسوم"], ["type", "أنواع المعاملات", "طلب، شكوى، تعميم…"], ["folder", "المجلدات", "التنظيم حسب المجلد"]];
  const hrefFor = (kind, r) => {
    if (kind === "party") return "#/list?q=" + encodeURIComponent(r.key);
    if (kind === "tag") return "#/list?tag=" + encodeURIComponent(r.key);
    if (r.key === "" || r.key === null) return null;
    return `#/list?${kind === "type" ? "type" : "folder"}=${r.key}`;
  };
  EXT_ROUTES["/browse"] = async (q) => {
    const kind = KINDS.some((k) => k[0] === q.get("kind")) ? q.get("kind") : "party";
    const term = q.get("q") || "";
    const rows = await api(`/facets?kind=${kind}&q=${encodeURIComponent(term)}`);
    const tabs = h("div", { class: "row", role: "tablist" }, KINDS.map(([k, label]) => h("a", { class: "tab" + (k === kind ? " on" : ""), role: "tab", "aria-selected": String(k === kind), href: "#/browse?kind=" + k }, label)));
    const search = h("input", { type: "search", class: "tf-in", value: term, placeholder: "بحث في هذا الفهرس…", "aria-label": "بحث في الفهرس", onkeydown: (e) => { if (e.key === "Enter") location.hash = `#/browse?kind=${kind}&q=${encodeURIComponent(e.target.value.trim())}`; } });
    const max = Math.max(1, ...rows.map((r) => r.n));
    const meta = KINDS.find((k) => k[0] === kind);
    return [h("h2", {}, "الفهارس"), h("p", { class: "muted small" }, "استعرض مراسلاتك مصنّفةً: " + meta[2] + ". الأرقام ضمن ما يحق لك الاطلاع عليه."), tabs,
      h("div", { class: "card" }, h("div", { class: "tbar" }, search, h("span", { class: "small muted" }, rows.length + " عنصر")),
        rows.length ? h("div", { class: "facets" }, rows.map((r) => {
          const href = hrefFor(kind, r), bar = h("div", { class: "fbar" }, h("i")); bar.firstChild.style.width = Math.round((r.n / max) * 100) + "%";
          return h(href ? "a" : "div", { class: "facet", href }, h("div", { class: "fname" }, kind === "tag" ? "# " + r.name : r.name), bar,
            h("div", { class: "fmeta small muted" }, `${arNum(r.n)} معاملة`, r.open_n ? ` • ${arNum(r.open_n)} مفتوحة` : "", r.last ? " • آخرها " + fmtDate(r.last) : ""));
        })) : h("div", { class: "empty" }, "لا نتائج"))];
  };

  // ───────────── التقويم ─────────────
  const DAYS = ["الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"];
  const hijriDay = (d) => { try { return new Intl.DateTimeFormat("ar-SA-u-ca-islamic-umalqura-nu-latn", { day: "numeric" }).format(d); } catch { return ""; } };
  const monthName = (y, m) => new Intl.DateTimeFormat("ar-u-ca-gregory-nu-latn", { month: "long", year: "numeric" }).format(new Date(y, m - 1, 1));
  const pad = (n) => String(n).padStart(2, "0");
  EXT_ROUTES["/calendar"] = async (q) => {
    const now = new Date(), cur = /^\d{4}-(0[1-9]|1[0-2])$/.test(q.get("m") || "") ? q.get("m") : `${now.getFullYear()}-${pad(now.getMonth() + 1)}`;
    const [Y, M] = cur.split("-").map(Number), data = await api("/calendar?month=" + cur);
    const first = new Date(Y, M - 1, 1), days = new Date(Y, M, 0).getDate(), lead = first.getDay();
    const shift = (n) => { const d = new Date(Y, M - 1 + n, 1); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`; };
    const chip = (it) => h("a", { class: "cal-it " + (it.kind === "task" ? "task" : it.urgent ? "urg" : "corr") + (it.done ? " done" : ""), title: (it.ref_no ? it.ref_no + " — " : "") + it.title,
      href: it.kind === "corr" ? "#/c/" + it.id : it.corr_id ? "#/c/" + it.corr_id : "#/tasks" }, it.kind === "task" ? "✔ " : "", it.title);
    const cells = [];
    for (let i = 0; i < lead; i++) cells.push(h("div", { class: "cal-cell off", "aria-hidden": "true" }));
    for (let d = 1; d <= days; d++) {
      const iso = `${cur}-${pad(d)}`, items = data.days[iso] || [], dt = new Date(Y, M - 1, d);
      cells.push(h("div", { class: "cal-cell" + (iso === data.today ? " today" : "") + (items.length ? " has" : "") },
        h("div", { class: "cal-d" }, h("span", {}, d), h("span", { class: "cal-h" }, hijriDay(dt)), h("span", { class: "cal-wd" }, DAYS[dt.getDay()])),
        ...items.slice(0, 3).map(chip), items.length > 3 ? h("details", { class: "cal-more" }, h("summary", {}, `+${items.length - 3} أخرى`), ...items.slice(3).map(chip)) : null));
    }
    const total = Object.values(data.days).reduce((s, a) => s + a.length, 0);
    return [h("div", { class: "row between" }, h("h2", {}, "التقويم"), h("div", { class: "row" },
      h("a", { class: "btn ghost sm", href: "#/calendar?m=" + shift(-1), "aria-label": "الشهر السابق" }, "→ السابق"), h("strong", { class: "cal-title", "aria-live": "polite" }, monthName(Y, M)),
      h("a", { class: "btn ghost sm", href: "#/calendar?m=" + shift(1), "aria-label": "الشهر التالي" }, "التالي ←"), h("a", { class: "btn sm", href: "#/calendar" }, "اليوم"))),
      h("p", { class: "muted small" }, `مواعيد استحقاق المعاملات ومهامك في هذا الشهر (${total}).`, " ",
        h("span", { class: "cal-key corr" }, "معاملة"), " ", h("span", { class: "cal-key urg" }, "عاجلة"), " ", h("span", { class: "cal-key task" }, "مهمة"), " ", h("span", { class: "cal-key done" }, "منتهية")),
      h("div", { class: "cal", role: "grid", "aria-label": monthName(Y, M) }, DAYS.map((d) => h("div", { class: "cal-h-d", role: "columnheader" }, d)), ...cells)];
  };

  // ───────────── التحرير الجماعي (يُستدعى من شريط التحديد في قائمة المعاملات) ─────────────
  function bulkDialog(kind, ids, done) {
    const isFolder = kind === "folder", isType = kind === "type";
    const input = kind === "tags" ? tagsInput([]) : null;
    const opts = isFolder ? { "": "— بدون مجلد —", ...folderOptions() } : isType ? { "": "— بدون نوع —", ...Object.fromEntries(state.types.map((t) => [t.id, t.name])) } : null;
    const pick = opts ? sel("bulkv", opts, "", { "aria-label": isFolder ? "المجلد" : "النوع" }) : null;
    const err = h("div", { class: "err", role: "alert" });
    const close = () => { box.remove(); document.removeEventListener("keydown", esc); };
    const esc = (e) => e.key === "Escape" && close();
    const go = h("button", { class: "btn", type: "submit" }, "تطبيق على " + ids.length + " معاملة");
    const form = h("form", { class: "card tdlg", onsubmit: async (e) => {
      e.preventDefault(); err.textContent = ""; go.disabled = true;
      try {
        const body = { ids, action: isFolder ? "set_folder" : isType ? "set_type" : "add_tags", value: pick ? (pick.value ? Number(pick.value) : null) : input.values() };
        const r = await api("/correspondences/bulk", { method: "POST", body });
        toast(`تم تطبيق التعديل على ${r.done} معاملة` + (r.denied ? ` (تعذّر ${r.denied}: لا تملك صلاحية تعديلها)` : ""), r.done === 0); close(); done();
      } catch (ex) { err.textContent = ex.message; go.disabled = false; }
    } }, h("h3", {}, isFolder ? "نقل إلى مجلد" : isType ? "تغيير نوع المعاملة" : "إضافة وسوم"),
      h("p", { class: "small muted" }, "يشمل المعاملات المحدّدة التي تملك صلاحية تعديلها فقط."), pick ? f(isFolder ? "المجلد" : "النوع", pick) : f("الوسوم (إدخال أو فاصلة)", input.node),
      err, h("div", { class: "row" }, go, h("button", { type: "button", class: "btn ghost", onclick: close }, "إلغاء")));
    const box = h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": "تحرير جماعي", onclick: (e) => e.target === box && close() }, form);
    document.addEventListener("keydown", esc); document.body.append(box); (pick || form.querySelector("input")).focus();
  }
  window.bulkExtra = (ids, reload) => !["admin", "manager", "clerk"].includes(state.user.role) ? [] : [
    h("button", { type: "button", class: "btn ghost sm", onclick: () => bulkDialog("folder", ids, reload) }, "📂 نقل إلى مجلد"),
    h("button", { type: "button", class: "btn ghost sm", onclick: () => bulkDialog("type", ids, reload) }, "🏷 تغيير النوع"),
    h("button", { type: "button", class: "btn ghost sm", onclick: () => bulkDialog("tags", ids, reload) }, "# إضافة وسوم")];

  // ───────────── العروض المحفوظة في الرئيسية ─────────────
  window.savedViewsCard = async () => {
    const views = (state.filters || []).filter((x) => x.kind !== "report").slice(0, 8);
    if (!views.length) return null;
    const counts = await Promise.allSettled(views.map((v) => api("/correspondences?size=1&" + v.query.replace(/(^|&)page=\d+/g, ""))));
    return h("section", { class: "card hsec", "aria-label": "عروضي المحفوظة" }, h("div", { class: "row between" }, h("h3", {}, "عروضي المحفوظة"), h("span", { class: "small muted" }, "الفلاتر التي حفظتها (من قائمة المعاملات)")),
      h("div", { class: "views" }, views.map((v, i) => h("a", { class: "view", href: "#/list?" + v.query }, h("span", { class: "vn" }, counts[i].status === "fulfilled" ? counts[i].value.total : "—"), h("span", { class: "vl" }, v.name)))));
  };
})();
