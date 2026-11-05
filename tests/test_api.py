"""اختبارات تكاملية للواجهة البرمجية: python -m unittest discover -s tests"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18765
URL = f"http://127.0.0.1:{PORT}"
ADMIN_PW = "Admin12345"


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


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        env = dict(os.environ, ACP_DATA=cls.tmp.name, ACP_PORT=str(PORT), ACP_ADMIN_PASSWORD=ADMIN_PW)
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                call("GET", "/api/health")
                break
            except Exception:
                time.sleep(0.1)
        cls.admin = login("admin", ADMIN_PW)
        mk = lambda u, role, clr, dept: call("POST", "/api/users", {
            "username": u, "full_name": u, "password": "Passw0rd123", "role": role, "clearance": clr,
            "department_id": dept}, cls.admin)
        assert mk("clerk1", "clerk", 1, 1)[0] == 201
        assert mk("emp1", "employee", 0, 2)[0] == 201
        assert mk("emp2", "employee", 0, 2)[0] == 201
        assert mk("mgr1", "manager", 1, 2)[0] == 201
        # المستخدمون الجدد يُجبرون على تغيير كلمة المرور؛ نغيّرها
        cls.tok = {}
        for u in ("clerk1", "emp1", "emp2", "mgr1"):
            t = login(u, "Passw0rd123")
            assert call("POST", "/api/me/password", {"current": "Passw0rd123", "new": "Newpass456"}, t)[0] == 200
            cls.tok[u] = login(u, "Newpass456")
        cls.uid = {u["username"]: u["id"] for u in call("GET", "/api/users", token=cls.admin)[1]}

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.tmp.cleanup()

    def corr(self, token, **kw):
        body = dict(scope="external", direction="incoming", subject="خطاب تجريبي", party="وزارة المالية",
                    doc_date="2026-01-10")
        body.update(kw)
        return call("POST", "/api/correspondences", body, token)

    def test_01_auth_required_and_headers(self):
        s, _, h = call("GET", "/api/correspondences")
        self.assertEqual(s, 401)
        s, _, h = call("GET", "/")
        self.assertEqual(s, 200)
        self.assertIn("frame-ancestors", h["Content-Security-Policy"])
        self.assertEqual(call("GET", "/../server.py")[0], 404)
        self.assertEqual(call("GET", "/%2e%2e/server.py")[0], 404)

    def test_02_bad_login_and_lockout(self):
        for _ in range(5):
            self.assertEqual(call("POST", "/api/login", {"username": "emp2", "password": "wrong"})[0], 401)
        self.assertEqual(call("POST", "/api/login", {"username": "emp2", "password": "Newpass456"})[0], 423)

    def test_03_create_numbering_and_rbac(self):
        s, d, _ = self.corr(self.tok["clerk1"])
        self.assertEqual(s, 201)
        self.assertRegex(d["ref_no"], r"^EXT-IN-\d{4}-00001$")
        s, d2, _ = self.corr(self.tok["clerk1"])
        self.assertTrue(d2["ref_no"].endswith("00002"))
        # الموظف لا يسجّل مراسلة خارجية
        self.assertEqual(self.corr(self.tok["emp1"])[0], 403)
        # لكن يسجّل داخلية
        s, _, _ = call("POST", "/api/correspondences", dict(scope="internal", direction="outgoing", subject="طلب",
                       party="الشؤون المالية", doc_date="2026-01-11"), self.tok["emp1"])
        self.assertEqual(s, 201)

    def test_04_confidentiality_and_visibility(self):
        s, d, _ = self.corr(self.tok["clerk1"], priority="secret", subject="سري")
        self.assertEqual(s, 201)
        self.assertEqual(call("GET", f"/api/correspondences/{d['id']}", token=self.tok["emp1"])[0], 404)
        self.assertEqual(call("GET", f"/api/correspondences/{d['id']}", token=self.tok["mgr1"])[0], 404)  # ليست إدارته
        self.assertEqual(call("GET", f"/api/correspondences/{d['id']}", token=self.tok["clerk1"])[0], 200)
        # لا يمكن تسجيل مراسلة أعلى من الصلاحية
        s, k, _ = call("POST", "/api/classifications", {"label": "سري للغاية", "conf_level": 2, "color": "#000000"}, self.admin)
        self.assertEqual(s, 201)
        self.assertEqual(self.corr(self.tok["clerk1"], priority=k["key"])[0], 403)
        # إحالة مراسلة سرية لموظف بلا صلاحية مرفوضة
        s, e, _ = call("POST", f"/api/correspondences/{d['id']}/transfers", {"to_user": self.uid["emp1"]}, self.tok["clerk1"])
        self.assertEqual(s, 400)

    def test_05_transfer_flow(self):
        s, d, _ = self.corr(self.tok["clerk1"], subject="للإحالة")
        cid = d["id"]
        self.assertEqual(call("GET", f"/api/correspondences/{cid}", token=self.tok["emp1"])[0], 404)
        s, _, _ = call("POST", f"/api/correspondences/{cid}/transfers",
                       {"to_user": self.uid["emp1"], "instruction": "للدراسة", "due_date": "2026-02-01"}, self.tok["clerk1"])
        self.assertEqual(s, 201)
        c = call("GET", f"/api/correspondences/{cid}", token=self.tok["emp1"])[1]
        self.assertEqual(c["status"], "referred")
        tasks = call("GET", "/api/tasks", token=self.tok["emp1"])[1]
        tid = [t for t in tasks if t["id"] == cid][0]["transfer_id"]
        # موظف آخر لا ينفّذ إحالة غيره
        self.assertEqual(call("POST", f"/api/transfers/{tid}/complete", {}, self.tok["mgr1"])[0], 404)
        self.assertEqual(call("POST", f"/api/transfers/{tid}/complete", {"note": "تم"}, self.tok["emp1"])[0], 200)
        c = call("GET", f"/api/correspondences/{cid}", token=self.tok["emp1"])[1]
        self.assertEqual(c["status"], "in_progress")
        self.assertTrue(any(t["action"] == "transfer" for t in c["timeline"]))

    def test_06_attachments(self):
        cid = self.corr(self.tok["clerk1"])[1]["id"]
        hdr = {"X-Filename": "%D8%AE%D8%B7%D8%A7%D8%A8.pdf"}
        s, d, _ = call("POST", f"/api/correspondences/{cid}/attachments", token=self.tok["clerk1"], headers=hdr, raw=b"%PDF-1.4 test")
        self.assertEqual(s, 201)
        s, body, h = call("GET", f"/api/attachments/{d['id']}", token=self.tok["clerk1"])
        self.assertEqual((s, body), (200, b"%PDF-1.4 test"))
        self.assertIn("attachment", h["Content-Disposition"])
        bad = call("POST", f"/api/correspondences/{cid}/attachments", token=self.tok["clerk1"],
                   headers={"X-Filename": "evil.exe"}, raw=b"MZ")
        self.assertEqual(bad[0], 400)
        trav = call("POST", f"/api/correspondences/{cid}/attachments", token=self.tok["clerk1"],
                    headers={"X-Filename": "..%2F..%2Fx.pdf"}, raw=b"x")
        self.assertEqual(trav[0], 201)  # يُنزَّع المسار ويبقى الاسم x.pdf
        self.assertEqual(call("GET", f"/api/correspondences/{cid}", token=self.tok["clerk1"])[1]["attachments"][-1]["filename"], "x.pdf")

    def test_07_search_sql_safety_and_export(self):
        s, d, _ = call("GET", "/api/correspondences?q=%27%20OR%201%3D1--", token=self.tok["clerk1"])
        self.assertEqual((s, d["total"]), (200, 0))
        s, d, _ = call("GET", "/api/correspondences?q=%25", token=self.tok["clerk1"])
        self.assertEqual(d["total"], 0)  # % تُعامل كحرف عادي
        s, csv_, _ = call("GET", "/api/export", token=self.tok["clerk1"])
        self.assertEqual(s, 200)
        self.assertTrue(csv_.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(call("GET", "/api/export", token=self.tok["emp1"])[0], 403)

    def test_08_admin_only_and_api_key(self):
        self.assertEqual(call("GET", "/api/admin/audit", token=self.tok["clerk1"])[0], 403)
        self.assertEqual(call("POST", "/api/users", {}, self.tok["mgr1"])[0], 403)
        s, k, _ = call("POST", "/api/admin/apikeys", {"name": "HR", "user_id": self.uid["clerk1"]}, self.admin)
        self.assertEqual(s, 201)
        s, d, _ = call("POST", "/api/correspondences", dict(scope="external", direction="incoming", subject="من نظام خارجي",
                       party="ERP", doc_date="2026-03-01"), headers={"X-API-Key": k["key"]})
        self.assertEqual(s, 201)
        self.assertEqual(call("GET", "/api/correspondences", headers={"X-API-Key": "acp_bad"})[0], 401)
        log = call("GET", "/api/admin/audit?q=API", token=self.admin)[1]
        self.assertTrue(any("API:HR" in (r["username"] or "") for r in log))
        call("DELETE", f"/api/admin/apikeys/{k['id']}", token=self.admin)
        self.assertEqual(call("GET", "/api/correspondences", headers={"X-API-Key": k["key"]})[0], 401)

    def test_09_validation(self):
        t = self.tok["clerk1"]
        self.assertEqual(call("POST", "/api/correspondences", {"scope": "x"}, t)[0], 400)
        self.assertEqual(self.corr(t, due_date="2026-13-45")[0], 400)
        self.assertEqual(call("POST", "/api/correspondences", {"scope": "external", "direction": "incoming",
                         "subject": "s", "party": "p", "doc_date": "غير"}, t)[0], 400)

    def test_09b_read_state_and_kpi(self):
        cid = self.corr(self.tok["clerk1"], subject="غير مقروءة للمدير")[1]["id"]
        d = call("GET", "/api/correspondences?read=unread&direction=incoming", token=self.tok["mgr1"])[1]
        self.assertEqual(d["kpi"]["unread"], d["total"])  # المدير لا يراها ضمن مؤشراته إن لم تكن إدارته
        a = call("GET", "/api/correspondences?read=unread", token=self.admin)[1]
        self.assertTrue(any(i["id"] == cid and not i["is_read"] for i in a["items"]))
        self.assertEqual(call("GET", f"/api/correspondences/{cid}", token=self.admin)[0], 200)
        a2 = call("GET", "/api/correspondences?read=unread", token=self.admin)[1]
        self.assertFalse(any(i["id"] == cid for i in a2["items"]))
        mine = call("GET", "/api/correspondences?read=read&has_att=1&period=today&sort=old", token=self.tok["clerk1"])
        self.assertEqual(mine[0], 200)
        self.assertEqual(call("POST", "/api/correspondences/read", {"ids": [cid]}, self.tok["clerk1"])[0], 200)
        self.assertEqual(call("POST", "/api/correspondences/read", {"ids": ["x"]}, self.tok["clerk1"])[0], 400)

    def test_09c_delivery_fields_and_priorities(self):
        s, d, _ = self.corr(self.tok["clerk1"], priority="very_urgent", needs_reply=1, deliverer_name="أحمد",
                            deliverer_phone="0500000000", deliverer_email="a@b.sa", delivery_notes="سلّمت باليد")
        self.assertEqual(s, 201)
        c = call("GET", f"/api/correspondences/{d['id']}", token=self.tok["clerk1"])[1]
        self.assertEqual((c["needs_reply"], c["deliverer_name"], c["priority"]), (1, "أحمد", "very_urgent"))
        self.assertEqual(self.corr(self.tok["clerk1"], deliverer_email="not-an-email")[0], 400)
        self.assertEqual(self.corr(self.tok["clerk1"], priority="bogus")[0], 400)
        k = call("GET", "/api/correspondences?priority=urgent_all", token=self.tok["clerk1"])[1]
        self.assertTrue(any(i["id"] == d["id"] for i in k["items"]))

    def test_09d_control_panel(self):
        a = self.admin
        cl = call("GET", "/api/classifications", token=self.tok["emp1"])[1]
        self.assertEqual([c["label"] for c in cl[:7]], ["عاجل جدا", "عاجل", "مهم جدا", "مهم", "سري", "عادي", "غير مهم"])
        self.assertEqual(call("POST", "/api/classifications", {"label": "x"}, self.tok["clerk1"])[0], 403)
        self.assertEqual(call("POST", "/api/classifications", {"label": "x", "color": "red"}, a)[0], 400)
        self.assertEqual(call("PUT", "/api/classifications/normal", {"active": False}, a)[0], 400)
        # وحدات
        s, d, _ = call("POST", "/api/departments", {"name": "وحدة اختبار"}, a)
        self.assertEqual(s, 201)
        self.assertEqual(call("PUT", f"/api/departments/{d['id']}", {"name": "وحدة معدّلة", "active": False}, a)[0], 200)
        self.assertEqual(call("PUT", f"/api/departments/{d['id']}", {"name": "الإدارة العامة"}, a)[0], 409)
        # موظفون
        uid = self.uid["emp1"]
        self.assertEqual(call("PUT", f"/api/users/{uid}", {"job_title": "محاسب", "phone": "0500", "email": "e@x.sa"}, a)[0], 200)
        self.assertEqual(call("PUT", f"/api/users/{uid}", {"email": "bad"}, a)[0], 400)
        u = [x for x in call("GET", "/api/users", token=a)[1] if x["id"] == uid][0]
        self.assertEqual((u["job_title"], u["phone"]), ("محاسب", "0500"))
        self.assertEqual(call("PUT", f"/api/users/{self.uid['mnag'] if 'mnag' in self.uid else self.uid['admin']}", {"role": "employee"}, a)[0], 400)
        # صلاحيات الأدوار
        self.assertEqual(call("PUT", "/api/admin/perms", {"role": "employee", "perm": "create_external", "allowed": True}, a)[0], 200)
        self.assertEqual(self.corr(self.tok["emp1"])[0], 201)  # الآن مسموح
        self.assertEqual(call("PUT", "/api/admin/perms", {"role": "employee", "perm": "create_external", "allowed": False}, a)[0], 200)
        self.assertEqual(self.corr(self.tok["emp1"])[0], 403)
        self.assertIn("export", call("GET", "/api/me", token=self.tok["clerk1"])[1]["perms"])
        self.assertEqual(call("PUT", "/api/admin/perms", {"role": "employee", "perm": "nope", "allowed": 1}, a)[0], 400)

    def test_09e_reports(self):
        r = call("GET", "/api/reports", token=self.tok["clerk1"])
        self.assertEqual(r[0], 200)
        d = r[1]
        self.assertGreater(d["total"], 0)
        self.assertTrue(d["by_class"] and d["by_unit"] and d["by_month"])
        self.assertEqual(call("GET", "/api/reports", token=self.tok["emp1"])[0], 403)
        self.assertEqual(call("GET", "/api/reports?direction=outgoing&from=2026-01-01&to=2026-12-31", token=self.admin)[0], 200)

    def test_09f_folders_types_reply_tracking(self):
        a, c1 = self.admin, self.tok["clerk1"]
        fl = call("GET", "/api/lookups/folders", token=c1)[1]
        ty = call("GET", "/api/lookups/types", token=c1)[1]
        self.assertTrue(fl and ty)
        self.assertEqual(call("POST", "/api/lookups/folders", {"name": "مجلد جديد"}, c1)[0], 403)
        self.assertEqual(call("POST", "/api/lookups/types", {"name": "نوع جديد"}, a)[0], 201)
        self.assertEqual(call("POST", "/api/lookups/types", {"name": "نوع جديد"}, a)[0], 409)
        # وارد يحتاج رد، والمخوّل بالرد emp1 فقط
        s, d, _ = self.corr(c1, needs_reply=1, reply_users=[self.uid["emp1"]], folder_id=fl[0]["id"], type_id=ty[0]["id"], subject="يحتاج رد")
        self.assertEqual(s, 201)
        cid = d["id"]
        det = call("GET", f"/api/correspondences/{cid}", token=self.tok["emp1"])  # صار مرئيًا للمخوّل
        self.assertEqual(det[0], 200)
        self.assertTrue(det[1]["can_reply"])
        self.assertEqual((det[1]["folder_name"], det[1]["type_name"]), (fl[0]["name"], ty[0]["name"]))
        self.assertEqual(call("GET", "/api/tasks", token=self.tok["emp1"])[1][0]["kind"], "reply")
        lst = call("GET", "/api/correspondences?reply=awaiting", token=c1)[1]
        self.assertTrue(any(i["id"] == cid and i["reply_count"] == 0 for i in lst["items"]))
        # غير المخوّل (مدير الإدارة بلا manage_all؟ هو يملكها) → نستخدم موظفًا آخر ليس له صلاحية
        s, _, _ = call("POST", "/api/correspondences", dict(scope="internal", direction="outgoing", subject="رد", party="x",
                       doc_date="2026-11-05", parent_id=cid), self.tok["emp2"])
        self.assertEqual(s, 404)  # لا يراها أصلًا
        # الرد من المخوّل
        s, r, _ = call("POST", "/api/correspondences", dict(scope="internal", direction="outgoing", subject="رد", party="x",
                       doc_date="2026-11-05", parent_id=cid), self.tok["emp1"])
        self.assertEqual(s, 201)
        det = call("GET", f"/api/correspondences/{cid}", token=c1)[1]
        self.assertEqual([r["status"] for r in det["reply_users"]], ["done"])
        self.assertEqual(det["replies"][0]["id"], r["id"])
        self.assertEqual(call("GET", "/api/correspondences?reply=replied", token=c1)[1]["items"][0]["id"], cid)
        self.assertEqual(call("GET", "/api/tasks", token=self.tok["emp1"])[1], [])
        # ربط صادر قائم بوارد آخر
        s, d2, _ = self.corr(c1, subject="وارد ثانٍ")
        s, out, _ = call("POST", "/api/correspondences", dict(scope="external", direction="outgoing", subject="صادر مستقل",
                         party="y", doc_date="2026-11-05"), c1)
        ref = call("GET", f"/api/correspondences/{out['id']}", token=c1)[1]["ref_no"]
        self.assertEqual(call("POST", f"/api/correspondences/{d2['id']}/link", {"ref_no": ref}, c1)[0], 200)
        self.assertEqual(call("POST", f"/api/correspondences/{d2['id']}/link", {"ref_no": ref}, c1)[0], 400)  # مرتبط مسبقًا
        self.assertEqual(call("POST", f"/api/correspondences/{d2['id']}/link", {"ref_no": "NOPE"}, c1)[0], 404)
        self.assertEqual(call("PUT", f"/api/correspondences/{d2['id']}", {"folder_id": fl[1]["id"]}, c1)[0], 200)
        self.assertGreater(len(call("GET", "/api/reports", token=a)[1]["by_ctype"]), 0)

    def test_09g_multi_assign_and_unit(self):
        c1 = self.tok["clerk1"]
        two = [self.uid["emp1"], self.uid["mgr1"]]
        s, d, _ = self.corr(c1, assigned_to=two, department_id=3, subject="إسناد متعدد")
        self.assertEqual(s, 201)
        det = call("GET", f"/api/correspondences/{d['id']}", token=self.tok["emp1"])
        self.assertEqual(det[0], 200)
        self.assertEqual(len(det[1]["assignees"]), 2)
        self.assertEqual(det[1]["department_id"], 3)
        self.assertTrue(det[1]["can_manage"])  # المُسنَد إليه يدير المراسلة
        self.assertEqual(call("GET", f"/api/correspondences/{d['id']}", token=self.tok["mgr1"])[0], 200)
        for t in ("emp1", "mgr1"):
            self.assertTrue(any(x["id"] == d["id"] for x in call("GET", "/api/tasks", token=self.tok[t])[1]))
        # موظف عادي يختار الوحدة المسؤولة بنفسه (داخلي)
        s, d2, _ = call("POST", "/api/correspondences", dict(scope="internal", direction="outgoing", subject="وحدة", party="x",
                        doc_date="2026-11-05", department_id=4), self.tok["emp1"])
        self.assertEqual(s, 201)
        self.assertEqual(call("GET", f"/api/correspondences/{d2['id']}", token=self.tok["emp1"])[1]["department_id"], 4)
        # إسناد لمن لا يملك صلاحية السرية مرفوض، وقيمة مفردة ما زالت مقبولة
        s, d3, _ = self.corr(c1, priority="secret", assigned_to=[self.uid["emp1"]])
        self.assertEqual(s, 400)
        self.assertEqual(self.corr(c1, assigned_to=self.uid["emp1"])[0], 201)

    def test_10_dashboard(self):
        d = call("GET", "/api/dashboard", token=self.tok["clerk1"])[1]
        self.assertGreater(d["total"], 0)
        self.assertEqual(call("GET", "/api/dashboard", token=self.tok["emp2"])[1]["total"], 0)


if __name__ == "__main__":
    unittest.main()
