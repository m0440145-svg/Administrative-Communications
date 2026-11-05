"""إصدارات المرفقات والحقول المخصصة.   python -m unittest tests.test_edms"""
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
PORT = 18777
URL = f"http://127.0.0.1:{PORT}"


def call(method, path, body=None, token=None, raw=None, headers=None):
    h, data = dict(headers or {}), raw
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(URL + path, data=data, method=method, headers=h)) as r:
            p = r.read()
            return r.status, (json.loads(p) if "json" in r.headers.get("Content-Type", "") else p)
    except urllib.error.HTTPError as e:
        p = e.read()
        try:
            return e.code, json.loads(p)
        except Exception:
            return e.code, p


class EdmsTest(unittest.TestCase):
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
        assert call("POST", "/api/users", {"username": "emp_d", "full_name": "موظف", "password": "Passw0rd123", "role": "employee", "clearance": 0, "department_id": 2}, cls.admin)[0] == 201
        t = call("POST", "/api/login", {"username": "emp_d", "password": "Passw0rd123"})[1]["token"]
        call("POST", "/api/me/password", {"current": "Passw0rd123", "new": "Newpass456"}, t)
        cls.emp = call("POST", "/api/login", {"username": "emp_d", "password": "Newpass456"})[1]["token"]

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.tmp.cleanup()

    def corr(self, **kw):
        b = dict(scope="external", direction="incoming", subject="معاملة", party="جهة", doc_date="2026-01-10")
        b.update(kw)
        s, c = call("POST", "/api/correspondences", b, self.admin)
        self.assertEqual(s, 201, c)
        return c

    def attach(self, cid, data, name="عقد.pdf"):
        s, a = call("POST", f"/api/correspondences/{cid}/attachments", token=self.admin, raw=data, headers={"X-Filename": urllib.parse.quote(name)})
        self.assertEqual(s, 201, a)
        return a["id"]

    def test_1_versions_flow(self):
        c = self.corr()
        aid = self.attach(c["id"], b"%PDF v1")
        meta = call("GET", f"/api/correspondences/{c['id']}/att-meta", token=self.admin)[1]
        self.assertEqual((meta["items"][0]["id"], meta["items"][0]["old_versions"], meta["items"][0]["can_version"]), (aid, 0, True))
        s, r = call("POST", f"/api/attachments/{aid}/versions", token=self.admin, raw=b"%PDF v2 changed", headers={"X-Note": urllib.parse.quote("تعديل البند 3")})
        self.assertEqual((s, r["ver_no"]), (201, 2), r)
        self.assertEqual(call("POST", f"/api/attachments/{aid}/versions", token=self.admin, raw=b"%PDF v2 changed")[0], 400)  # البصمة نفسها
        call("POST", f"/api/attachments/{aid}/versions", token=self.admin, raw=b"%PDF v3")
        vs = call("GET", f"/api/attachments/{aid}/versions", token=self.admin)[1]
        self.assertEqual([(v["ver_no"], v["current"]) for v in vs], [(3, True), (2, False), (1, False)])
        self.assertEqual(vs[2]["sha256"], __import__("hashlib").sha256(b"%PDF v1").hexdigest())
        self.assertEqual(call("GET", f"/api/attachments/{aid}", token=self.admin)[1], b"%PDF v3")                   # التنزيل العادي = الحالي
        self.assertEqual(call("GET", f"/api/attachment-versions/{vs[2]['id']}", token=self.admin)[1], b"%PDF v1")  # القديم متاح
        self.assertEqual(vs[2]["note"], "تعديل البند 3")  # الملاحظة = سبب استبدال ذلك الإصدار
        self.assertEqual(call("GET", f"/api/attachment-versions/{vs[2]['id']}", token=self.emp)[0], 404)            # موظف لا يرى المعاملة

    def test_2_version_rules(self):
        c = self.corr()
        aid = self.attach(c["id"], b"A")
        self.assertEqual(call("POST", f"/api/attachments/{aid}/versions", token=self.emp, raw=b"B")[0], 404)
        self.assertEqual(call("POST", f"/api/attachments/{aid}/versions", token=self.admin, raw=b"B", headers={"X-Filename": "x.exe"})[0], 400)
        # معاملة موقّعة: يُمنع الاستبدال
        con = __import__("sqlite3").connect(os.path.join(self.tmp.name, "acp.db"), timeout=30)
        con.execute("INSERT INTO signatures(corr_id,step_id,user_id,decision,comment,doc_hash,signature,snapshot,signed_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (c["id"], None, 1, "approve", None, "h", "s", "{}", "2026-01-01 00:00:00"))
        con.commit(); con.close()
        s, r = call("POST", f"/api/attachments/{aid}/versions", token=self.admin, raw=b"C")
        self.assertEqual(s, 400)
        self.assertIn("موقَّعة", r["error"])
        self.assertFalse(call("GET", f"/api/correspondences/{c['id']}/att-meta", token=self.admin)[1]["items"][0]["can_version"])

    def test_3_custom_fields(self):
        ty = call("GET", "/api/lookups/types", token=self.admin)[1][0]["id"]
        s, f1 = call("POST", "/api/custom-fields", {"name": "قيمة العقد", "ftype": "number", "required": True}, self.admin)
        self.assertEqual(s, 201, f1)
        s, f2 = call("POST", "/api/custom-fields", {"name": "الجهة المنفذة", "ftype": "choice", "choices": "الرياض، جدة، الدمام", "type_id": ty}, self.admin)
        self.assertEqual(s, 201, f2)
        self.assertEqual(call("POST", "/api/custom-fields", {"name": "قيمة العقد", "ftype": "text"}, self.admin)[0], 409)
        self.assertEqual(call("POST", "/api/custom-fields", {"name": "س", "ftype": "choice"}, self.admin)[0], 400)
        self.assertEqual(call("POST", "/api/custom-fields", {"name": "ص", "ftype": "text"}, self.emp)[0], 403)
        c1, c2 = self.corr(), self.corr(type_id=ty)
        g1 = call("GET", f"/api/correspondences/{c1['id']}/fields", token=self.admin)[1]
        self.assertEqual([f["name"] for f in g1["fields"]], ["قيمة العقد"])                       # حقل النوع لا يظهر لغير نوعه
        g2 = call("GET", f"/api/correspondences/{c2['id']}/fields", token=self.admin)[1]
        self.assertEqual([f["name"] for f in g2["fields"]], ["قيمة العقد", "الجهة المنفذة"])
        put = lambda cid, vals, tok=None: call("PUT", f"/api/correspondences/{cid}/fields", {"values": vals}, tok or self.admin)
        self.assertEqual(put(c2["id"], {str(f1["id"]): "abc"})[0], 400)                          # رقم غير صالح
        self.assertEqual(put(c2["id"], {str(f1["id"]): ""})[0], 400)                             # إلزامي
        self.assertEqual(put(c2["id"], {str(f2["id"]): "مكة"})[0], 400)                          # خارج القائمة
        self.assertEqual(put(c2["id"], {str(f1["id"]): "125000.50", str(f2["id"]): "جدة"})[0], 200)
        vals = {f["name"]: f["value"] for f in call("GET", f"/api/correspondences/{c2['id']}/fields", token=self.admin)[1]["fields"]}
        self.assertEqual(vals, {"قيمة العقد": "125000.50", "الجهة المنفذة": "جدة"})
        self.assertEqual(put(c2["id"], {str(f2["id"]): ""})[0], 200)                             # مسح قيمة غير إلزامية
        self.assertEqual(put(c2["id"], {str(f1["id"]): "1"}, self.emp)[0], 404)                  # لا يرى المعاملة
        self.assertEqual(call("PUT", f"/api/custom-fields/{f1['id']}", {"ftype": "text"}, self.admin)[0], 400)
        self.assertEqual(call("PUT", f"/api/custom-fields/{f2['id']}", {"active": False}, self.admin)[0], 200)
        self.assertEqual([f["name"] for f in call("GET", f"/api/correspondences/{c2['id']}/fields", token=self.admin)[1]["fields"]], ["قيمة العقد"])


if __name__ == "__main__":
    unittest.main()
