"""اختبارات الوحدات الملحقة: الإشعارات، البحث والوسوم، الاقتراحات، مسار العمل والتوقيع، التفويض، الاحتجاز،
سلسلة السجل، النسخ الاحتياطي، الاستيراد، SSO، المؤشرات.  python -m unittest discover -s tests"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18766
URL = f"http://127.0.0.1:{PORT}"
ADMIN_PW = "Admin12345"
SSO_SECRET = "proxy-shared-secret"


def call(method, path, body=None, token=None, headers=None, raw=None):
    h = dict(headers or {})
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(URL + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            payload = r.read()
            return r.status, (json.loads(payload) if "json" in r.headers.get("Content-Type", "") else payload), r.headers
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload), e.headers
        except Exception:
            return e.code, payload, e.headers


def login(u, p):
    s, d, _ = call("POST", "/api/login", {"username": u, "password": p})
    assert s == 200, d
    return d["token"]


class ExtTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "acp.db")
        env = dict(os.environ, ACP_DATA=cls.tmp.name, ACP_PORT=str(PORT), ACP_ADMIN_PASSWORD=ADMIN_PW, ACP_BACKUP_HOURS="0",
                   ACP_SSO_HEADER="X-Remote-User", ACP_SSO_SECRET=SSO_SECRET)
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                call("GET", "/api/health")
                break
            except Exception:
                time.sleep(0.1)
        cls.admin = login("admin", ADMIN_PW)
        cls.tok, cls.uid = {}, {}
        for u, role, clr, dept in (("clerk1", "clerk", 1, 1), ("emp1", "employee", 0, 2), ("emp2", "employee", 0, 2), ("mgr1", "manager", 1, 2)):
            s, d, _ = call("POST", "/api/users", {"username": u, "full_name": u, "password": "Passw0rd123", "role": role, "clearance": clr, "department_id": dept}, cls.admin)
            assert s == 201, d
            t = login(u, "Passw0rd123")
            assert call("POST", "/api/me/password", {"current": "Passw0rd123", "new": "Newpass456"}, t)[0] == 200
            cls.tok[u] = login(u, "Newpass456")
        cls.uid = {x["username"]: x["id"] for x in call("GET", "/api/users", token=cls.admin)[1]}

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.tmp.cleanup()

    def corr(self, token=None, **kw):
        body = dict(scope="external", direction="incoming", subject="خطاب تجريبي", party="وزارة المالية", doc_date="2026-01-10")
        body.update(kw)
        return call("POST", "/api/correspondences", body, token or self.tok["clerk1"])

    def get(self, cid, token=None):
        return call("GET", f"/api/correspondences/{cid}", token=token or self.admin)

    def dbq(self, sql, params=()):
        c = sqlite3.connect(self.db)
        try:
            r = c.execute(sql, params).fetchall()
            c.commit()
            return r
        finally:
            c.close()

    # ───────── 1) الإشعارات ─────────
    def test_01_notifications(self):
        s, d, _ = self.corr(assigned_to=[self.uid["emp1"]], needs_reply=1, reply_users=[self.uid["emp2"]], subject="إشعار")
        self.assertEqual(s, 201)
        n = call("GET", "/api/notifications", token=self.tok["emp1"])[1]
        self.assertGreaterEqual(n["unread"], 1)
        self.assertTrue(any(x["kind"] == "assigned" and x["corr_id"] == d["id"] for x in n["items"]))
        n2 = call("GET", "/api/notifications", token=self.tok["emp2"])[1]
        self.assertTrue(any(x["kind"] == "reply_needed" for x in n2["items"]))
        self.assertEqual(call("POST", "/api/notifications/read", {"all": True}, self.tok["emp1"])[0], 200)
        self.assertEqual(call("GET", "/api/notifications/count", token=self.tok["emp1"])[1]["unread"], 0)
        # إشعار تغيّر الحالة للمنشئ
        call("PUT", f"/api/correspondences/{d['id']}", {"status": "in_progress"}, self.tok["emp1"])
        self.assertTrue(any(x["kind"] == "status" for x in call("GET", "/api/notifications", token=self.tok["clerk1"])[1]["items"]))

    # ───────── 2) الوسوم والبحث النصي ─────────
    def test_02_tags_and_search(self):
        s, d, _ = self.corr(subject="اعتماد الميزانية التشغيلية", summary="خطاب بخصوص المشروعات الاستراتيجية", tags=["ميزانيات", "2026"], party="وزارة الماليّة")
        self.assertEqual(s, 201)
        self.assertEqual(sorted(self.get(d["id"])[1]["tags"]), ["2026", "ميزانيات"])
        # البحث يتجاوز اختلاف الألف/الهاء/التشكيل وأداة التعريف
        for q in ("الميزانية", "ميزانيه", "ميزانيات*", "المشروعات", "الماليه"):
            items = call("GET", "/api/search?q=" + urllib.request.quote(q), token=self.admin)[1]["items"]
            self.assertTrue(any(i["id"] == d["id"] for i in items), q)
        # الفلتر بالوسم في القائمة
        lst = call("GET", "/api/correspondences?tag=" + urllib.request.quote("ميزانيات"), token=self.admin)[1]
        self.assertEqual([i["id"] for i in lst["items"]], [d["id"]])
        # تعديل الوسوم ثم البحث بالجديد
        self.assertEqual(call("PUT", f"/api/correspondences/{d['id']}/tags", {"tags": ["عاجل-مالي"]}, self.tok["clerk1"])[0], 200)
        self.assertTrue(any(i["id"] == d["id"] for i in call("GET", "/api/search?q=" + urllib.request.quote("مالي"), token=self.admin)[1]["items"]))
        # نص مرفق نصي يُفهرس
        up = call("POST", f"/api/correspondences/{d['id']}/attachments", token=self.tok["clerk1"], headers={"X-Filename": "n.txt"}, raw="شروط التوريد الخاصة بالمناقصة".encode())
        self.assertEqual(up[0], 201)
        self.assertTrue(any(i["id"] == d["id"] for i in call("GET", "/api/search?q=" + urllib.request.quote("المناقصة"), token=self.admin)[1]["items"]))
        # الخصوصية: السري لا يظهر في بحث من لا يملك الصلاحية
        s, sec, _ = self.corr(subject="مناقصة سرية للغاية", priority="secret")
        self.assertEqual(s, 201)
        self.assertFalse(any(i["id"] == sec["id"] for i in call("GET", "/api/search?q=" + urllib.request.quote("سرية"), token=self.tok["emp1"])[1]["items"]))
        self.assertEqual(call("GET", "/api/search?q=", token=self.admin)[0], 400)

    # ───────── 3) الاقتراحات الذكية وقواعد التوجيه والقوالب ─────────
    def test_03_suggest_rules_templates(self):
        ty = call("GET", "/api/lookups/types", token=self.admin)[1]
        t_id = [t for t in ty if t["name"] == "شكوى"][0]["id"]
        for i in range(4):
            self.corr(subject=f"شكوى بخصوص تأخر الصرف رقم {i}", party="جمعية الأمل", type_id=t_id)
        sg = call("GET", "/api/suggest?subject=" + urllib.request.quote("شكوى تأخر الصرف") + "&party=" + urllib.request.quote("جمعية الأمل"), token=self.tok["clerk1"])[1]
        self.assertEqual(sg["type"]["name"], "شكوى")
        self.assertEqual(sg["type"]["source"], "ml")
        # قاعدة صريحة بتوجيه تلقائي
        types = {t["name"]: t["id"] for t in ty}
        s, r, _ = call("POST", "/api/admin/rules", {"name": "عقود إلى emp2", "keywords": "عقد، مناقصة", "set_type_id": types["عقد / اتفاقية"],
                                                      "assign_user_id": self.uid["emp2"], "auto_apply": True}, self.admin)
        self.assertEqual(s, 201)
        sg = call("GET", "/api/suggest?subject=" + urllib.request.quote("توقيع عقد الصيانة"), token=self.tok["clerk1"])[1]
        self.assertEqual((sg["type"]["source"], sg["assignee"]["name"]), ("rule", "emp2"))
        s, d, _ = self.corr(subject="توقيع عقد صيانة المبنى")
        det = self.get(d["id"])[1]
        self.assertEqual(det["type_name"], "عقد / اتفاقية")
        self.assertIn("emp2", det["assignees"])
        self.assertTrue(any(t["action"] == "auto_route" for t in det["timeline"]))
        self.assertEqual(call("POST", "/api/admin/rules", {"name": "x"}, self.admin)[0], 400)
        self.assertEqual(call("POST", "/api/admin/rules", {"name": "x", "keywords": "k"}, self.tok["clerk1"])[0], 403)
        tpl = call("GET", "/api/templates", token=self.tok["emp1"])[1]
        self.assertGreaterEqual(len(tpl), 4)
        self.assertIn("{subject_orig}", tpl[0]["subject_tpl"])

    # ───────── 4) التفضيلات والفلاتر المحفوظة ─────────
    def test_04_prefs_and_filters(self):
        t = self.tok["emp1"]
        self.assertEqual(call("PUT", "/api/me/prefs", {"theme": "dark", "page_size": 50}, t)[0], 200)
        prefs = call("GET", "/api/me/prefs", token=t)[1]
        self.assertEqual((prefs["theme"], prefs["page_size"]), ("dark", 50))
        self.assertEqual(call("PUT", "/api/me/prefs", {"theme": "neon"}, t)[0], 400)
        s, f, _ = call("POST", "/api/me/filters", {"name": "عاجل غير مقروء", "kind": "list", "query": "priority=urgent&read=unread"}, t)
        self.assertEqual(s, 201)
        self.assertEqual(call("POST", "/api/me/filters", {"name": "x", "kind": "list", "query": "<script>"}, t)[0], 400)
        self.assertEqual(len(call("GET", "/api/me/filters", token=t)[1]), 1)
        self.assertEqual(call("GET", "/api/me/filters", token=self.tok["emp2"])[1], [])  # خاصة بصاحبها
        self.assertEqual(call("DELETE", f"/api/me/filters/{f['id']}", token=self.tok["emp2"])[0], 200)
        self.assertEqual(len(call("GET", "/api/me/filters", token=t)[1]), 1)  # حذف غير المالك لا يؤثر
        call("DELETE", f"/api/me/filters/{f['id']}", token=t)
        self.assertEqual(call("GET", "/api/me/filters", token=t)[1], [])

    # ───────── 5) مسار العمل + الاعتماد بالتوقيع الإلكتروني ─────────
    def test_05_workflow_and_signature(self):
        wfs = call("GET", "/api/workflows", token=self.tok["clerk1"])[1]
        wf = [w for w in wfs if w["name"] == "اعتماد خطاب صادر"][0]
        s, d, _ = self.corr(direction="outgoing", subject="خطاب للاعتماد")
        cid = d["id"]
        self.assertEqual(call("POST", f"/api/correspondences/{cid}/workflow", {"workflow_id": wf["id"]}, self.tok["emp1"])[0], 404)  # لا يراها
        self.assertEqual(call("POST", f"/api/correspondences/{cid}/workflow", {"workflow_id": wf["id"]}, self.tok["clerk1"])[0], 201)
        self.assertEqual(call("POST", f"/api/correspondences/{cid}/workflow", {"workflow_id": wf["id"]}, self.tok["clerk1"])[0], 400)  # جارٍ
        det = self.get(cid, self.tok["mgr1"])  # مدير الخطوة الأولى صار يراها
        self.assertEqual(det[0], 200)
        steps = det[1]["workflow"]["steps"]
        self.assertEqual([s["status"] for s in steps], ["pending", "waiting"])
        self.assertTrue(steps[0]["can_decide"])
        task = [t for t in call("GET", "/api/tasks", token=self.tok["mgr1"])[1] if t["id"] == cid and t["kind"] == "step"]
        self.assertEqual(len(task), 1)
        # ليس دوره
        self.assertEqual(call("POST", f"/api/wf/steps/{steps[1]['id']}/decide", {"decision": "approve", "password": ADMIN_PW}, self.admin)[0], 404)
        self.assertEqual(call("POST", f"/api/wf/steps/{steps[0]['id']}/decide", {"decision": "done"}, self.tok["emp1"])[0], 404)
        self.assertEqual(call("POST", f"/api/wf/steps/{steps[0]['id']}/decide", {"decision": "approve"}, self.tok["mgr1"])[0], 400)  # قرار غير صالح لمراجعة
        self.assertEqual(call("POST", f"/api/wf/steps/{steps[0]['id']}/decide", {"decision": "done", "comment": "تمت المراجعة"}, self.tok["mgr1"])[0], 200)
        # خطوة الاعتماد: كلمة مرور خاطئة ترفض، الصحيحة توقّع
        self.assertEqual(call("POST", f"/api/wf/steps/{steps[1]['id']}/decide", {"decision": "approve", "password": "wrong"}, self.admin)[0], 400)
        self.assertEqual(call("POST", f"/api/wf/steps/{steps[1]['id']}/decide", {"decision": "approve", "password": ADMIN_PW, "comment": "معتمد"}, self.admin)[0], 200)
        det = self.get(cid)[1]
        self.assertEqual((det["workflow"]["status"], det["status"]), ("done", "closed"))
        sig = det["signatures"][0]
        self.assertTrue(sig["signature_valid"] and sig["document_unchanged"])
        v = call("GET", f"/api/signatures/{sig['id']}/verify", token=self.admin)[1]
        self.assertTrue(v["signature_valid"] and v["document_unchanged"])
        # أي تغيير لاحق في الوثيقة (مرفق جديد) يُكتشف
        det_before = self.get(cid)[1]
        self.assertEqual(det_before["status"], "closed")
        self.assertEqual(call("POST", f"/api/correspondences/{cid}/attachments", token=self.admin, headers={"X-Filename": "late.pdf"}, raw=b"%PDF late")[0], 201)
        self.assertFalse(call("GET", f"/api/signatures/{sig['id']}/verify", token=self.admin)[1]["document_unchanged"])
        # تلاعب بالتوقيع في القاعدة يُكتشف
        self.dbq("UPDATE signatures SET snapshot=replace(snapshot,'approve','reject') WHERE id=?", (sig["id"],))
        self.assertFalse(call("GET", f"/api/signatures/{sig['id']}/verify", token=self.admin)[1]["signature_valid"])

    def test_06_workflow_reject_and_custom(self):
        s, w, _ = call("POST", "/api/workflows", {"name": "مسار مخصص", "close_on_finish": False,
                        "steps": [{"name": "اعتماد المراجع", "kind": "approve", "user_id": self.uid["mgr1"], "days": 1}]}, self.admin)
        self.assertEqual(s, 201)
        self.assertEqual(call("POST", "/api/workflows", {"name": "سيء", "steps": [{"name": "x", "kind": "weird", "role": "admin"}]}, self.admin)[0], 400)
        self.assertEqual(call("POST", "/api/workflows", {"name": "سيء2", "steps": []}, self.admin)[0], 400)
        s, d, _ = self.corr(direction="outgoing", subject="سيُرفض")
        call("POST", f"/api/correspondences/{d['id']}/workflow", {"workflow_id": w["id"]}, self.tok["clerk1"])
        st = self.get(d["id"], self.tok["mgr1"])[1]["workflow"]["steps"][0]
        self.assertEqual(call("POST", f"/api/wf/steps/{st['id']}/decide", {"decision": "reject", "password": "Newpass456"}, self.tok["mgr1"])[0], 400)  # سبب الرفض مطلوب
        self.assertEqual(call("POST", f"/api/wf/steps/{st['id']}/decide", {"decision": "reject", "password": "Newpass456", "comment": "غير مكتمل"}, self.tok["mgr1"])[0], 200)
        det = self.get(d["id"])[1]
        self.assertEqual(det["workflow"]["status"], "rejected")
        self.assertNotEqual(det["status"], "closed")
        self.assertTrue(any(n["kind"] == "wf_rejected" for n in call("GET", "/api/notifications", token=self.tok["clerk1"])[1]["items"]))

    def test_07_direct_approval(self):
        s, d, _ = self.corr(direction="outgoing", subject="اعتماد مباشر")
        self.assertEqual(call("POST", f"/api/correspondences/{d['id']}/approve", {"password": "Newpass456"}, self.tok["emp1"])[0], 404)
        self.assertEqual(call("POST", f"/api/correspondences/{d['id']}/approve", {"password": "bad"}, self.admin)[0], 400)
        self.assertEqual(call("POST", f"/api/correspondences/{d['id']}/approve", {"password": ADMIN_PW}, self.admin)[0], 201)
        self.assertEqual(len(self.get(d["id"])[1]["signatures"]), 1)

    # ───────── 6) التفويض ─────────
    def test_08_delegation(self):
        s, d, _ = self.corr(assigned_to=[self.uid["emp1"]], subject="مهمة مفوَّضة")
        cid = d["id"]
        self.assertEqual(self.get(cid, self.tok["emp2"])[0], 404)
        self.assertFalse(any(t["id"] == cid for t in call("GET", "/api/tasks", token=self.tok["emp2"])[1]))
        today = time.strftime("%Y-%m-%d")
        soon = time.strftime("%Y-%m-%d", time.localtime(time.time() + 30 * 86400))
        self.assertEqual(call("POST", "/api/delegations", {"to_user": self.uid["emp2"], "start_date": today, "end_date": "2020-01-01"}, self.tok["emp1"])[0], 400)
        self.assertEqual(call("POST", "/api/delegations", {"to_user": self.uid["emp1"], "start_date": today, "end_date": today}, self.tok["emp1"])[0], 400)
        s, dl, _ = call("POST", "/api/delegations", {"to_user": self.uid["emp2"], "start_date": today, "end_date": soon, "note": "إجازة"}, self.tok["emp1"])
        self.assertEqual(s, 201)
        self.assertEqual(self.get(cid, self.tok["emp2"])[0], 200)  # يرى ما يخص مُفوِّضه
        tasks = [t for t in call("GET", "/api/tasks", token=self.tok["emp2"])[1] if t["id"] == cid]
        self.assertEqual(len(tasks), 1)
        self.assertTrue(self.get(cid, self.tok["emp2"])[1]["can_manage"])
        self.assertEqual(call("POST", f"/api/transfers/{tasks[0]['transfer_id']}/complete", {"note": "نيابة"}, self.tok["emp2"])[0], 200)
        # لا يتجاوز التفويض درجة السرية للمفوَّض إليه
        s, sec, _ = self.corr(priority="secret", assigned_to=[self.uid["clerk1"]])
        dl2 = call("POST", "/api/delegations", {"to_user": self.uid["emp2"], "start_date": today, "end_date": soon, "from_user": self.uid["clerk1"]}, self.admin)[1]
        self.assertEqual(self.get(sec["id"], self.tok["emp2"])[0], 404)
        call("DELETE", f"/api/delegations/{dl2['id']}", token=self.admin)  # إلغاء تفويض clerk1 حتى لا يؤثر على ما بعده
        # إلغاء التفويض
        s, d2, _ = self.corr(assigned_to=[self.uid["emp1"]], subject="بعد الإلغاء")
        self.assertEqual(self.get(d2["id"], self.tok["emp2"])[0], 200)
        self.assertEqual(call("DELETE", f"/api/delegations/{dl['id']}", token=self.tok["emp2"])[0], 404)  # ليس المالك
        self.assertEqual(call("DELETE", f"/api/delegations/{dl['id']}", token=self.tok["emp1"])[0], 200)
        self.assertEqual(self.get(d2["id"], self.tok["emp2"])[0], 404)

    # ───────── 7) الاحتجاز والتجميد والإتلاف ─────────
    def test_09_retention_and_legal_hold(self):
        s, d, _ = self.corr(subject="للأرشفة والإتلاف")
        cid = d["id"]
        call("POST", f"/api/correspondences/{cid}/attachments", token=self.tok["clerk1"], headers={"X-Filename": "a.pdf"}, raw=b"%PDF-1.4 old")
        call("PUT", f"/api/correspondences/{cid}", {"status": "closed"}, self.tok["clerk1"])
        s, p, _ = call("POST", "/api/admin/retention", {"name": "أرشفة المنجز", "after_days": 30, "action": "archive"}, self.admin)
        self.assertEqual(s, 201)
        self.assertEqual(call("POST", "/api/admin/retention", {"name": "x", "after_days": 0, "action": "archive"}, self.admin)[0], 400)
        self.assertFalse(any(c["id"] == cid for c in call("GET", "/api/admin/retention/candidates", token=self.admin)[1]))  # حديثة
        self.dbq("UPDATE correspondences SET updated_at='2020-01-01 00:00:00' WHERE id=?", (cid,))
        cands = call("GET", "/api/admin/retention/candidates", token=self.admin)[1]
        self.assertTrue(any(c["id"] == cid and c["action"] == "archive" for c in cands))
        # التجميد القانوني يمنع الأهلية
        self.assertEqual(call("POST", f"/api/correspondences/{cid}/legal-hold", {"hold": True}, self.admin)[0], 400)  # السبب مطلوب
        self.assertEqual(call("POST", f"/api/correspondences/{cid}/legal-hold", {"hold": True, "reason": "قضية"}, self.admin)[0], 200)
        self.assertFalse(any(c["id"] == cid for c in call("GET", "/api/admin/retention/candidates", token=self.admin)[1]))
        call("POST", f"/api/correspondences/{cid}/legal-hold", {"hold": False}, self.admin)
        # تنفيذ الأرشفة يتطلب كلمة المرور
        self.assertEqual(call("POST", "/api/admin/retention/apply", {"action": "archive", "ids": [cid], "password": "bad"}, self.admin)[0], 400)
        self.assertEqual(call("POST", "/api/admin/retention/apply", {"action": "archive", "ids": [cid], "password": ADMIN_PW}, self.admin)[1], {"done": 1})
        self.assertEqual(self.get(cid)[1]["status"], "archived")
        # الإتلاف: سياسة + سبب + كلمة مرور؛ يبقى السجل وتُحذف الملفات (تُعاد الأرشفة لتاريخ قديم ليصير مؤهلًا)
        self.dbq("UPDATE correspondences SET updated_at='2020-02-01 00:00:00' WHERE id=?", (cid,))
        call("POST", "/api/admin/retention", {"name": "إتلاف المؤرشف", "after_days": 365, "action": "dispose"}, self.admin)
        self.assertEqual(call("POST", "/api/admin/retention/apply", {"action": "dispose", "ids": [cid], "password": ADMIN_PW}, self.admin)[0], 400)  # بلا سبب
        self.assertEqual(call("POST", "/api/admin/retention/apply", {"action": "dispose", "ids": [999999], "reason": "x", "password": ADMIN_PW}, self.admin)[0], 400)
        files_before = len(os.listdir(os.path.join(self.tmp.name, "uploads")))
        self.assertEqual(call("POST", "/api/admin/retention/apply", {"action": "dispose", "ids": [cid], "reason": "انتهت مدة الحفظ", "password": ADMIN_PW}, self.admin)[1], {"done": 1})
        self.assertEqual(len(os.listdir(os.path.join(self.tmp.name, "uploads"))), files_before - 1)
        att = self.get(cid)[1]["attachments"][0]
        self.assertEqual(call("GET", f"/api/attachments/{att['id']}", token=self.admin)[0], 410)
        log = call("GET", "/api/admin/disposal-log", token=self.admin)[1]
        self.assertEqual(log[0]["corr_id"], cid)
        self.assertEqual(json.loads(log[0]["attachments"])[0]["filename"], "a.pdf")
        self.assertEqual(call("GET", "/api/admin/retention", token=self.tok["clerk1"])[0], 403)

    # ───────── 8) سلسلة السجل والنسخ الاحتياطي والاسترجاع ─────────
    def test_10_backup_restore_and_audit_chain(self):
        v = call("GET", "/api/admin/audit/verify", token=self.admin)[1]
        self.assertTrue(v["ok"], v)
        self.assertGreater(v["checked"], 10)
        s, b, _ = call("POST", "/api/admin/backup", token=self.admin)
        self.assertEqual(s, 201)
        self.assertIn(b["name"], [x["name"] for x in call("GET", "/api/admin/backups", token=self.admin)[1]])
        s, zdata, _ = call("GET", "/api/admin/backups/download?name=" + b["name"], token=self.admin)
        self.assertEqual((s, zdata[:2]), (200, b"PK"))
        self.assertEqual(call("GET", "/api/admin/backups/download?name=../../acp.db", token=self.admin)[0], 404)
        zp = os.path.join(self.tmp.name, "backups", b["name"])
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
        self.assertIn("acp.db", names)
        self.assertIn("manifest.json", names)
        # استرجاع في مجلد جديد عبر سطر الأوامر ثم التحقق من البيانات
        target = tempfile.mkdtemp()
        env = dict(os.environ, ACP_DATA=target)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "server.py"), "restore", zp], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        orig = self.dbq("SELECT COUNT(*) FROM correspondences")[0][0]
        c = sqlite3.connect(os.path.join(target, "acp.db"))
        self.assertGreaterEqual(c.execute("SELECT COUNT(*) FROM correspondences").fetchone()[0], orig - 5)
        c.close()
        r2 = subprocess.run([sys.executable, os.path.join(ROOT, "server.py"), "restore", zp], env=env, capture_output=True, text=True)
        self.assertEqual(r2.returncode, 1)  # لا يكتب فوق قاعدة موجودة دون --force
        # نسخة تالفة تُرفض
        bad = os.path.join(self.tmp.name, "bad.zip")
        with zipfile.ZipFile(zp) as zin, zipfile.ZipFile(bad, "w") as zout:
            for n in zin.namelist():
                zout.writestr(n, zin.read(n) + (b"x" if n == "acp.db" else b""))
        r3 = subprocess.run([sys.executable, os.path.join(ROOT, "server.py"), "restore", bad, "--force"], env=dict(os.environ, ACP_DATA=tempfile.mkdtemp()), capture_output=True, text=True)
        self.assertEqual(r3.returncode, 1)
        # تلاعب بالسجل يُكتشف (حذف صف من المنتصف)
        mid = self.dbq("SELECT id FROM audit_log WHERE hash IS NOT NULL ORDER BY id LIMIT 1 OFFSET 5")[0][0]
        with self.assertRaises(sqlite3.IntegrityError):  # السجل غير قابل للحذف/التعديل حتى بصلاحية الكتابة المباشرة
            self.dbq("DELETE FROM audit_log WHERE id=?", (mid,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.dbq("UPDATE audit_log SET detail='x' WHERE id=?", (mid,))
        self.dbq("DROP TRIGGER audit_no_delete")  # مهاجم بصلاحية مدير القاعدة يعطّل المحفّز: السلسلة تكشف التلاعب مع ذلك
        self.dbq("DELETE FROM audit_log WHERE id=?", (mid,))
        v2 = call("GET", "/api/admin/audit/verify", token=self.admin)[1]
        self.assertFalse(v2["ok"])
        self.assertGreater(v2["first_bad_id"], mid)

    # ───────── 9) الاستيراد ─────────
    def test_11_imports(self):
        users_csv = ("username,full_name,job_title,phone,email,unit,role,clearance,active\n"
                     "hr_ali,علي الشهري,محاسب,0500000001,ali@x.sa,الحسابات,موظف,0,1\n"
                     "emp1,,مدقق,,,,,,\n"
                     "bad user,خطأ,,,,,,,\n").encode("utf-8")
        s, r, _ = call("POST", "/api/admin/import/users?dry_run=1", token=self.admin, raw=users_csv)
        self.assertEqual((s, r["created"], r["updated"], r["errors"]), (200, 1, 1, 1))
        self.assertNotIn("hr_ali", [u["username"] for u in call("GET", "/api/users", token=self.admin)[1]])  # التجريبي لا يكتب
        s, r, _ = call("POST", "/api/admin/import/users", token=self.admin, raw=users_csv)
        self.assertEqual((r["created"], r["updated"], r["errors"]), (1, 1, 1))
        temp = [x for x in r["rows"] if x["result"] == "created"][0]["temp_password"]
        self.assertEqual(call("POST", "/api/login", {"username": "hr_ali", "password": temp})[0], 200)
        users = {u["username"]: u for u in call("GET", "/api/users", token=self.admin)[1]}
        self.assertEqual(users["emp1"]["job_title"], "مدقق")
        self.assertEqual(call("POST", "/api/admin/import/users", token=self.tok["clerk1"], raw=users_csv)[0], 403)
        corr_csv = ("legacy_ref,direction,scope,subject,party,party_ref,doc_date,classification,type,folder,unit,status,summary\n"
                    "OLD-1,وارد,خارجي,خطاب قديم عن التوريد,شركة النور,ن/55,2019-05-01,عاجل,طلب,أرشيف 2019,الحسابات,منجز,ملخص\n"
                    "OLD-2,صادر,داخلي,رد قديم,قسم الصيانة,,2019-06-01,عادي,,,,,\n"
                    "OLD-3,sideways,خارجي,خطأ,x,,2019-06-01,,,,,,\n").encode("utf-8")
        s, r, _ = call("POST", "/api/admin/import/correspondences?dry_run=1", token=self.admin, raw=corr_csv)
        self.assertEqual((r["created"], r["errors"]), (2, 1))
        self.assertEqual(self.dbq("SELECT COUNT(*) FROM correspondences WHERE legacy_ref LIKE 'OLD-%'")[0][0], 0)
        s, r, _ = call("POST", "/api/admin/import/correspondences", token=self.admin, raw=corr_csv)
        self.assertEqual((r["created"], r["errors"]), (2, 1))
        s, r2, _ = call("POST", "/api/admin/import/correspondences", token=self.admin, raw=corr_csv)
        self.assertEqual((r2["created"], r2["skipped"]), (0, 2))  # لا تكرار عند إعادة الاستيراد
        found = call("GET", "/api/search?q=" + urllib.request.quote("OLD-1"), token=self.admin)[1]["items"]  # فُهرس الرقم القديم
        self.assertTrue(found)
        det = self.get(found[0]["id"])[1]
        self.assertEqual((det["status"], det["type_name"], det["folder_name"], det["priority"]), ("closed", "طلب", "أرشيف 2019", "urgent"))

    # ───────── 10) SSO والمؤشرات وحالة النظام ─────────
    def test_12_sso(self):
        h = {"X-Remote-User": "emp1", "X-SSO-Secret": SSO_SECRET}
        s, me, _ = call("GET", "/api/me", headers=h)
        self.assertEqual((s, me["username"]), (200, "emp1"))
        self.assertEqual(call("GET", "/api/me", headers={"X-Remote-User": "emp1", "X-SSO-Secret": "wrong"})[0], 401)
        self.assertEqual(call("GET", "/api/me", headers={"X-Remote-User": "emp1"})[0], 401)  # بلا السر لا يُقبل أبدًا
        self.assertEqual(call("GET", "/api/me", headers={"X-Remote-User": "nobody", "X-SSO-Secret": SSO_SECRET})[0], 401)
        self.assertEqual(call("GET", "/api/admin/system", headers=h)[0], 403)  # لا يرفع الصلاحيات

    def test_13_kpi_and_system(self):
        k = call("GET", "/api/kpi", token=self.tok["clerk1"])[1]
        for key in ("total", "open", "overdue_pct", "sla_compliance_pct", "avg_close_days", "backlog_age", "monthly", "units", "workload", "reply_rate_pct"):
            self.assertIn(key, k)
        self.assertEqual(set(k["backlog_age"]), {"0-7", "8-30", "31-90", "90+"})
        self.assertEqual(call("GET", "/api/kpi", token=self.tok["emp1"])[0], 403)
        self.assertEqual(call("GET", "/api/kpi?from=2099-01-01", token=self.admin)[1]["total"], 0)
        sysd = call("GET", "/api/admin/system", token=self.admin)[1]
        self.assertTrue(sysd["sso"])
        self.assertFalse(sysd["encryption_at_rest"])
        self.assertGreater(sysd["correspondences"], 5)

    def test_13b_hierarchical_folders(self):
        a = self.admin
        s, root, _ = call("POST", "/api/lookups/folders", {"name": "الأرشيف العام"}, a)
        s, kid, _ = call("POST", "/api/lookups/folders", {"name": "تعاميم 2026", "parent_id": root["id"]}, a)
        self.assertEqual(s, 201)
        s, grand, _ = call("POST", "/api/lookups/folders", {"name": "تعاميم الربع الأول", "parent_id": kid["id"]}, a)
        self.assertEqual(s, 201)
        rows = call("GET", "/api/lookups/folders", token=self.tok["clerk1"])[1]
        by = {r["id"]: r for r in rows}
        self.assertEqual(by[grand["id"]]["path"], "الأرشيف العام ‹ تعاميم 2026 ‹ تعاميم الربع الأول")
        self.assertEqual(by[grand["id"]]["depth"], 2)
        ids = [r["id"] for r in rows]
        self.assertLess(ids.index(root["id"]), ids.index(kid["id"]))  # ترتيب شجري: الأب قبل الابن
        # منع الحلقات والعمق الزائد والأب غير الموجود
        self.assertEqual(call("PUT", f"/api/lookups/folders/{root['id']}", {"parent_id": grand["id"]}, a)[0], 400)
        self.assertEqual(call("PUT", f"/api/lookups/folders/{root['id']}", {"parent_id": root["id"]}, a)[0], 400)
        self.assertEqual(call("POST", "/api/lookups/folders", {"name": "س", "parent_id": 99999}, a)[0], 400)
        s, l4, _ = call("POST", "/api/lookups/folders", {"name": "مستوى 4", "parent_id": grand["id"]}, a)
        self.assertEqual(s, 201)
        self.assertEqual(call("POST", "/api/lookups/folders", {"name": "مستوى 5", "parent_id": l4["id"]}, a)[0], 400)
        # الحفظ في مجلد فرعي والتقرير حسب المجلد
        s, d, _ = self.corr(folder_id=grand["id"], subject="في مجلد فرعي")
        self.assertEqual(self.get(d["id"])[1]["folder_name"], "تعاميم الربع الأول")
        # نقل المجلد لأب آخر
        self.assertEqual(call("PUT", f"/api/lookups/folders/{grand['id']}", {"parent_id": None}, a)[0], 200)
        self.assertEqual({r["id"]: r for r in call("GET", "/api/lookups/folders", token=a)[1]}[grand["id"]]["depth"], 0)

    def test_14_reminders_and_settings(self):
        s, d, _ = self.corr(assigned_to=[self.uid["emp2"]], due_date="2020-01-01", subject="متأخرة")
        r = call("POST", "/api/admin/reminders/run", token=self.admin)[1]
        self.assertGreaterEqual(r["reminders"], 1)
        n = call("GET", "/api/notifications", token=self.tok["emp2"])[1]["items"]
        self.assertTrue(any(x["kind"] == "overdue" and x["corr_id"] == d["id"] for x in n))
        r2 = call("POST", "/api/admin/reminders/run", token=self.admin)[1]
        self.assertEqual(r2["reminders"], 0)  # لا تكرار في اليوم نفسه
        self.assertEqual(call("PUT", "/api/admin/settings", {"alert_overdue_pct": "1"}, self.admin)[0], 200)
        self.assertEqual(call("PUT", "/api/admin/settings", {"evil": "1"}, self.admin)[0], 400)
        self.assertEqual(call("PUT", "/api/admin/settings", {"alert_overdue_pct": "abc"}, self.admin)[0], 400)
        r3 = call("POST", "/api/admin/reminders/run", token=self.admin)[1]
        self.assertTrue(r3["alerts"])
        self.assertTrue(any(x["kind"] == "kpi_alert" for x in call("GET", "/api/notifications", token=self.admin)[1]["items"]))


if __name__ == "__main__":
    unittest.main()
