// الدردشة الفورية بين الموظفين داخل كل معاملة (وارد/صادر). إضافة فوق صفحة المعاملة دون تغيير سلوكها؛ الخلفية: ext_chat.py.
// الفورية عبر قناة SSE القائمة (حدث chat) مع استطلاع احتياطي كل 10 ثوانٍ. (تُحمَّل بعد edms.js)
(() => {
  const handlers = new Map();  // corr_id → دالة تتلقى حدث رسالة جديدة
  window.onChatEvent = (ev) => {
    const hnd = handlers.get(ev.corr_id);
    if (hnd && hnd.alive()) return hnd.fire();
    toast(`💬 ${ev.name || "زميل"}: رسالة جديدة في دردشة ${ev.ref_no || "معاملة"}`);
  };
  const hhmm = (s) => (s || "").slice(11, 16);
  const MENTION = /(@[A-Za-z0-9_.\-]{2,40})/g;
  const richBody = (txt) => txt.split(MENTION).map((p, i) => (i % 2 ? h("span", { class: "mention" }, p) : p));

  function chatCard(cid) {
    const ids = new Set();
    let lastId = 0, canPost = true, polling = null;
    const list = h("div", { class: "chat-list", role: "log", "aria-live": "polite", "aria-label": "رسائل الدردشة" });
    const live = h("span", { class: "chat-live" + (startRealtime.running ? " on" : ""), title: "الاتصال الفوري" }, "فوري");
    const jump = h("button", { type: "button", class: "chat-new", hidden: true, onclick: () => { list.scrollTop = list.scrollHeight; jump.hidden = true; } }, "↓ رسائل جديدة");
    const box = h("textarea", { rows: 2, maxlength: 2000, placeholder: "اكتب رسالتك… (Enter للإرسال، Shift+Enter لسطر جديد)", "aria-label": "رسالة الدردشة" });
    const send = h("button", { class: "btn", type: "button" }, "إرسال");
    const err = h("div", { class: "err", role: "alert" });
    const card = h("div", { class: "card chat" }, h("div", { class: "row between" }, h("h3", {}, "💬 دردشة المعاملة"), live),
      h("p", { class: "small muted" }, "يراها كل من يحق له الاطلاع على هذه المعاملة. اذكر زميلًا بـ @اسم_المستخدم لتنبيهه."), list, jump, err);

    const bubble = (m) => {
      const del = h("button", { class: "cm-x", type: "button", title: "حذف الرسالة", "aria-label": "حذف الرسالة", onclick: async () => {
        if (!confirm("حذف هذه الرسالة؟")) return;
        try { await api("/chat/" + m.id, { method: "DELETE" }); el.classList.add("del"); el.querySelector(".cm-b").textContent = "(حُذفت هذه الرسالة)"; del.remove(); } catch (e) { toast(e.message, true); }
      } }, "🗑");
      const el = h("div", { class: "cm" + (m.mine ? " mine" : "") + (m.deleted ? " del" : ""), "data-id": m.id },
        h("div", { class: "cm-h" }, h("strong", {}, m.mine ? "أنا" : m.name), h("span", {}, hhmm(m.at)), (m.mine || state.user.role === "admin") && !m.deleted ? del : null),
        h("div", { class: "cm-b" }, m.deleted ? "(حُذفت هذه الرسالة)" : richBody(m.body)));
      return el;
    };
    const append = (msgs) => {
      const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
      let added = 0;
      msgs.forEach((m) => { if (ids.has(m.id)) return; ids.add(m.id); lastId = Math.max(lastId, m.id); list.append(bubble(m)); added++; });
      if (!added) return;
      if (nearBottom || msgs.every((m) => m.mine)) list.scrollTop = list.scrollHeight; else jump.hidden = false;
    };
    const fetchNew = async () => {
      try { const r = await api(`/correspondences/${cid}/chat?after=${lastId}`); append(r.messages); } catch { /* مؤقت */ }
    };
    (async () => {
      try {
        const r = await api(`/correspondences/${cid}/chat`); canPost = r.can_post; append(r.messages);
        if (!r.messages.length) list.append(h("div", { class: "empty", id: "chat-empty" }, "لا رسائل بعد. ابدأ المحادثة 👋"));
        list.scrollTop = list.scrollHeight;
        if (!canPost) { box.disabled = true; send.disabled = true; box.placeholder = "المعاملة مؤرشفة: الدردشة للقراءة فقط"; }
      } catch (e) { err.textContent = e.message; }
    })();

    const doSend = async () => {
      const text = box.value.trim(); if (!text || !canPost) return;
      send.disabled = true; err.textContent = "";
      try { const m = await api(`/correspondences/${cid}/chat`, { method: "POST", body: { body: text } }); box.value = ""; const e0 = list.querySelector("#chat-empty"); if (e0) e0.remove(); append([m]); list.scrollTop = list.scrollHeight; }
      catch (e) { err.textContent = e.message; } finally { send.disabled = !canPost; box.focus(); }
    };
    send.onclick = doSend;
    box.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); doSend(); } });
    card.append(h("div", { class: "chat-form" }, box, send));

    // فورية: حدث SSE ← جلب الجديد؛ واستطلاع احتياطي إن لم تصل الأحداث
    const alive = () => card.isConnected;
    handlers.set(cid, { alive, fire: () => { live.classList.add("on"); const e0 = list.querySelector("#chat-empty"); if (e0) e0.remove(); fetchNew(); } });
    polling = setInterval(() => {
      if (!alive()) { clearInterval(polling); if (handlers.get(cid) && !handlers.get(cid).alive()) handlers.delete(cid); return; }
      live.classList.toggle("on", !!startRealtime.running);
      if (document.visibilityState === "visible") fetchNew();
    }, 10000);
    return card;
  }

  const baseDetail = detailView;
  detailView = async (id) => {
    const base = await baseDetail(id);
    const nodes = Array.isArray(base) ? [...base] : [base];
    nodes.splice(Math.min(2, nodes.length), 0, chatCard(id));
    return nodes;
  };
})();
