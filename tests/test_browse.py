"""الفهارس والتقويم والتحرير الجماعي.   python -m unittest tests.test_browse"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18776
URL = f"http://127.0.0.1:{PORT}"


def call(method, path, body=None, token=None):
    h, data = {}, None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(URL + path, data=data, method=method, headers=h)) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class BrowseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        env = dict(os.environ, ACP_DATA=cls.tmp.name, ACP_PORT=str(PORT), ACP_ADMIN_PASSWORD="Admin12345", ACP_BACKUP_HOURS="0")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                call("GET", "/api/health")
                break
            except Exception:
                time.sleep(0.1)
        cls.admin = call("POST", "/api/login", {"username": "admin", "password": "Admin12345"})[1]["token"]
        assert call("POST", "/api/users", {"username": "emp_c", "full_name": "موظف", "password": "Passw0rd123", "role": "employee", "clearance": 0, "department_id": 2}, cls.admin)[0] == 201
        t = call("POST", "/api/login", {"username": "emp_c", "password": "Passw0rd123"})[1]["token"]
        call("POST", "/api/me/password", {"current": "Passw0rd123", "new": "Newpass456"}, t)
        cls.emp = call("POST", "/api/login", {"username": "emp_c", "password": "Newpass456"})[1]["token"]
        cls.ids = []
        for subj, party, due in (("أ", "وزارة المالية", "2026-03-10"), ("ب", "وزارة المالية", "2026-03-20"), ("ج", "هيئة الاتصالات", "2026-04-02")):
            s, c = call("POST", "/api/correspondences", {"scope": "external", "direction": "incoming", "subject": subj, "party": party, "doc_date": "2026-01-10", "due_date": due}, cls.admin)
            assert s == 201, c
            cls.ids.append(c["id"])

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.tmp.cleanup()

    def test_1_facets(self):
        s, rows = call("GET", "/api/facets?kind=party", token=self.admin)
        self.assertEqual(s, 200)
        top = {r["name"]: r["n"] for r in rows}
        self.assertEqual(top["وزارة المالية"], 2)
        self.assertEqual(rows[0]["name"], "وزارة المالية")   # الأكثر مراسلة أولًا
        self.assertEqual(call("GET", "/api/facets?kind=party&q=" + urllib.parse.quote("هيئة"), token=self.admin)[1][0]["name"], "هيئة الاتصالات")
        self.assertEqual(call("GET", "/api/facets?kind=party&q=" + urllib.parse.quote("100%"), token=self.admin)[1], [])  # محارف LIKE تُهرَّب
        for k in ("tag", "type", "folder"):
            self.assertEqual(call("GET", f"/api/facets?kind={k}", token=self.admin)[0], 200)
        self.assertEqual(call("GET", "/api/facets?kind=users", token=self.admin)[0], 400)
        # الموظف لا يرى جهات مراسلات لا تخصه
        self.assertEqual(call("GET", "/api/facets?kind=party", token=self.emp)[1], [])

    def test_2_calendar(self):
        s, d = call("GET", "/api/calendar?month=2026-03", token=self.admin)
        self.assertEqual(s, 200)
        self.assertEqual(sorted(d["days"]), ["2026-03-10", "2026-03-20"])
        self.assertEqual(d["days"]["2026-03-10"][0]["kind"], "corr")
        call("POST", "/api/work-tasks", {"title": "مهمة المارس", "due_date": "2026-03-15"}, self.admin)
        d = call("GET", "/api/calendar?month=2026-03", token=self.admin)[1]
        self.assertIn("2026-03-15", d["days"])
        self.assertEqual(call("GET", "/api/calendar?month=2026-3", token=self.admin)[0], 400)
        self.assertEqual(call("GET", "/api/calendar?month=2026-03", token=self.emp)[1]["days"], {})

    def test_3_bulk_edit(self):
        f = call("GET", "/api/lookups/folders", token=self.admin)[1][0]["id"]
        b = lambda body, tok=None: call("POST", "/api/correspondences/bulk", body, tok or self.admin)
        s, r = b({"ids": self.ids, "action": "set_folder", "value": f})
        self.assertEqual((s, r["done"], r["denied"]), (200, 3, 0))
        for i in self.ids:
            self.assertEqual(call("GET", f"/api/correspondences/{i}", token=self.admin)[1]["folder_id"], f)
        s, r = b({"ids": self.ids[:2], "action": "add_tags", "value": ["عاجل-مالي", "#2026"]})
        self.assertEqual((s, r["done"]), (200, 2))
        self.assertEqual(sorted(call("GET", f"/api/correspondences/{self.ids[0]}", token=self.admin)[1]["tags"]), ["2026", "عاجل-مالي"])
        self.assertEqual(call("GET", "/api/correspondences?tag=" + urllib.parse.quote("عاجل-مالي"), token=self.admin)[1]["total"], 2)  # الفهرس النصي والفلتر محدَّثان
        self.assertEqual(b({"ids": self.ids, "action": "set_folder", "value": 99999})[0], 400)
        self.assertEqual(b({"ids": [], "action": "set_folder", "value": f})[0], 400)
        self.assertEqual(b({"ids": self.ids, "action": "drop_table"})[0], 400)
        self.assertEqual(b({"ids": list(range(1, 300)), "action": "mark_read"})[0], 400)
        # الموظف العادي: لا يرى ولا يعدّل → كلها مرفوضة
        s, r = b({"ids": self.ids, "action": "set_folder", "value": f}, self.emp)
        self.assertEqual((s, r["done"], r["denied"]), (200, 0, 3))
        self.assertEqual(call("GET", "/api/facets?kind=tag", token=self.admin)[1][0]["n"], 2)


if __name__ == "__main__":
    unittest.main()
