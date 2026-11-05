"""اختبارات المسح والاستلام وأنواع الملفات.   python -m unittest tests.test_scan"""
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
PORT = 18773
URL = f"http://127.0.0.1:{PORT}"


def call(method, path, body=None, token=None, raw=None, name=None):
    h, data = {}, raw
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    if name:
        h["X-Filename"] = urllib.parse.quote(name)
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


class ScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.scan = tempfile.mkdtemp()
        env = dict(os.environ, ACP_DATA=cls.tmp.name, ACP_PORT=str(PORT), ACP_ADMIN_PASSWORD="Admin12345", ACP_BACKUP_HOURS="0", ACP_SCAN_DIR=cls.scan, ACP_SCAN_SECONDS="1")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                call("GET", "/api/health")
                break
            except Exception:
                time.sleep(0.1)
        cls.admin = call("POST", "/api/login", {"username": "admin", "password": "Admin12345"})[1]["token"]
        call("POST", "/api/users", {"username": "emp8", "full_name": "موظف", "password": "Passw0rd123", "role": "employee", "clearance": 0, "department_id": 2}, cls.admin)
        t = call("POST", "/api/login", {"username": "emp8", "password": "Passw0rd123"})[1]["token"]
        call("POST", "/api/me/password", {"current": "Passw0rd123", "new": "Newpass456"}, t)
        cls.emp = call("POST", "/api/login", {"username": "emp8", "password": "Newpass456"})[1]["token"]
        s, c = call("POST", "/api/correspondences", {"scope": "external", "direction": "incoming", "subject": "وارد", "party": "جهة", "doc_date": "2026-01-10"}, cls.admin)
        cls.cid, cls.ref = c["id"], c["ref_no"]

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.tmp.cleanup()

    def attachments(self):
        return [a["filename"] for a in call("GET", f"/api/correspondences/{self.cid}", token=self.admin)[1]["attachments"]]

    def test_1_file_types(self):
        rules = call("GET", "/api/scan/rules", token=self.admin)[1]
        for e in (".pdf", ".odt", ".webp", ".7z", ".mp3", ".dwg"):
            self.assertIn(e, rules["exts"])
        for name, ok in (("a.odt", 201), ("a.exe", 400), ("a.svg", 400), ("a.html", 400), ("a.docm", 400), ("a.kml", 400)):
            self.assertEqual(call("POST", f"/api/correspondences/{self.cid}/attachments", token=self.admin, raw=b"x" * 10, name=name)[0], ok if ok == 400 else 201, name)

    def test_2_admin_extra_types_and_limits(self):
        self.assertEqual(call("PUT", "/api/admin/settings", {"ext_extra": ".kml, .geojson"}, self.admin)[0], 200)
        self.assertEqual(call("POST", f"/api/correspondences/{self.cid}/attachments", token=self.admin, raw=b"<kml/>", name="map.kml")[0], 201)
        for bad in (".exe", ".svg", "..x", ".toolongextension"):
            self.assertEqual(call("PUT", "/api/admin/settings", {"ext_extra": bad}, self.admin)[0], 400, bad)
        self.assertEqual(call("PUT", "/api/admin/settings", {"max_upload_mb": "1"}, self.admin)[0], 200)
        self.assertEqual(call("POST", f"/api/correspondences/{self.cid}/attachments", token=self.admin, raw=b"x" * (1024 * 1024 + 1), name="big.pdf")[0], 413)
        self.assertEqual(call("PUT", "/api/admin/settings", {"max_upload_mb": "999"}, self.admin)[0], 400)
        self.assertEqual(call("PUT", "/api/admin/settings", {"max_upload_mb": "20"}, self.admin)[0], 200)

    def test_3_inbox_flow_and_permissions(self):
        self.assertEqual(call("GET", "/api/scan/inbox", token=self.emp)[0], 403)
        self.assertEqual(call("POST", "/api/scan/inbox", token=self.emp, raw=b"x", name="a.pdf")[0], 403)
        self.assertEqual(call("POST", "/api/scan/inbox", token=self.admin, raw=b"x", name="a.exe")[0], 400)
        s, d = call("POST", "/api/scan/inbox", token=self.admin, raw=b"%PDF-1.4 hello", name="مسح.pdf")
        self.assertEqual(s, 201, d)
        self.assertEqual(call("GET", f"/api/scan/inbox/{d['id']}/file", token=self.admin)[1], b"%PDF-1.4 hello")
        self.assertEqual(call("POST", f"/api/scan/inbox/{d['id']}/attach", {"ref_no": "NOPE-1"}, self.admin)[0], 404)
        s, r = call("POST", f"/api/scan/inbox/{d['id']}/attach", {"ref_no": self.ref.lower()}, self.admin)
        self.assertEqual((s, r["corr_id"]), (200, self.cid))
        self.assertIn("مسح.pdf", self.attachments())
        self.assertNotIn(d["id"], [x["id"] for x in call("GET", "/api/scan/inbox", token=self.admin)[1]])
        s, d2 = call("POST", "/api/scan/inbox", token=self.admin, raw=b"zz", name="b.png")
        self.assertEqual(call("DELETE", f"/api/scan/inbox/{d2['id']}", token=self.admin)[0], 200)

    def drop(self, name, data=b"%PDF-1.4 scan"):
        p = os.path.join(self.scan, name)
        with open(p, "wb") as f:
            f.write(data)
        os.utime(p, (time.time() - 10, time.time() - 10))
        return p

    def wait(self, cond, secs=15):
        for _ in range(secs * 2):
            if cond():
                return True
            time.sleep(0.5)
        return False

    def test_4_hot_folder(self):
        named = self.drop(f"{self.ref}__عقد.pdf")
        loose = self.drop("بدون-رقم.pdf")
        bad = self.drop("run.exe", b"MZ")
        self.assertTrue(self.wait(lambda: not os.path.exists(named) and not os.path.exists(loose) and not os.path.exists(bad)))
        self.assertIn(f"{self.ref}__عقد.pdf", self.attachments())                        # أُرفق مباشرة برقم القيد
        names = [x["filename"] for x in call("GET", "/api/scan/inbox", token=self.admin)[1]]
        self.assertIn("بدون-رقم.pdf", names)                                              # دخل الصندوق
        self.assertTrue(os.listdir(os.path.join(self.scan, "_failed")))                  # المحظور رُفض
        self.assertTrue(any(x.endswith("عقد.pdf") for x in os.listdir(os.path.join(self.scan, "_processed"))))


if __name__ == "__main__":
    unittest.main()
