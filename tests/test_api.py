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
        s, d, _ = self.corr(self.tok["clerk1"], conf_level=1, subject="سري")
        self.assertEqual(s, 201)
        self.assertEqual(call("GET", f"/api/correspondences/{d['id']}", token=self.tok["emp1"])[0], 404)
        self.assertEqual(call("GET", f"/api/correspondences/{d['id']}", token=self.tok["mgr1"])[0], 404)  # ليست إدارته
        self.assertEqual(call("GET", f"/api/correspondences/{d['id']}", token=self.tok["clerk1"])[0], 200)
        # لا يمكن تسجيل مراسلة أعلى من الصلاحية
        self.assertEqual(self.corr(self.tok["clerk1"], conf_level=2)[0], 403)
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

    def test_10_dashboard(self):
        d = call("GET", "/api/dashboard", token=self.tok["clerk1"])[1]
        self.assertGreater(d["total"], 0)
        self.assertEqual(call("GET", "/api/dashboard", token=self.tok["emp2"])[1]["total"], 0)


if __name__ == "__main__":
    unittest.main()
