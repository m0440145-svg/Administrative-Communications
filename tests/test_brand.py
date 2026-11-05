"""اختبارات هوية الجهة: الألوان، الشعار، بيانات الجهة، الصلاحيات، والتوافق مع الخطاب الرسمي والنسخ الاحتياطي.   python -m unittest tests.test_brand"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18771
URL = f"http://127.0.0.1:{PORT}"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def call(method, path, body=None, token=None, raw=None):
    h, data = {}, raw
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(URL + path, data=data, method=method, headers=h)) as r:
            p = r.read()
            return r.status, (json.loads(p) if "json" in r.headers.get("Content-Type", "") else p), r.headers
    except urllib.error.HTTPError as e:
        p = e.read()
        try:
            return e.code, json.loads(p), e.headers
        except Exception:
            return e.code, p, e.headers


class BrandTest(unittest.TestCase):
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
        call("POST", "/api/users", {"username": "emp9", "full_name": "موظف", "password": "Passw0rd123", "role": "employee", "clearance": 0, "department_id": 2}, cls.admin)
        t = call("POST", "/api/login", {"username": "emp9", "password": "Passw0rd123"})[1]["token"]
        call("POST", "/api/me/password", {"current": "Passw0rd123", "new": "Newpass456"}, t)
        cls.emp = call("POST", "/api/login", {"username": "emp9", "password": "Newpass456"})[1]["token"]

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.tmp.cleanup()

    def test_1_public_defaults_and_no_secrets(self):
        s, d, _ = call("GET", "/api/public/branding")
        self.assertEqual(s, 200)
        self.assertEqual(d["colors"], {"primary": "", "side": ""})
        self.assertFalse(d["logo"])
        self.assertNotIn("org_about", d)
        self.assertEqual(call("GET", "/api/public/logo")[0], 404)

    def test_2_only_admin_writes_any_user_reads_profile(self):
        self.assertEqual(call("PUT", "/api/admin/org", {"org_name": "x"}, self.emp)[0], 403)
        self.assertEqual(call("GET", "/api/admin/org", token=self.emp)[0], 403)
        self.assertEqual(call("POST", "/api/admin/org/logo", raw=PNG, token=self.emp)[0], 403)
        self.assertEqual(call("GET", "/api/org/profile")[0], 401)
        self.assertEqual(call("GET", "/api/org/profile", token=self.emp)[0], 200)

    def test_3_profile_and_validation(self):
        body = {"org_name": "هيئة اختبار", "org_vision": "رؤية", "org_mission": "رسالة", "org_goals": "هدف 1\nهدف 2", "org_about": "نبذة",
                "org_email": "info@test.sa", "org_website": "https://test.sa", "org_phone": "+966 11 000 0000", "org_header": "ترويسة", "org_footer": "تذييل"}
        s, d, _ = call("PUT", "/api/admin/org", body, self.admin)
        self.assertEqual(s, 200, d)
        self.assertEqual(call("GET", "/api/org/profile", token=self.emp)[1]["org_goals"], "هدف 1\nهدف 2")
        for bad in ({"org_email": "x"}, {"org_website": "javascript:alert(1)"}, {"org_phone": "abc"}, {"org_about": "ا" * 3001}, {"nope": "1"}, {"org_name": 5}):
            self.assertEqual(call("PUT", "/api/admin/org", bad, self.admin)[0], 400, bad)

    def test_4_colors_validation_and_public(self):
        for bad in ({"color_primary": "red"}, {"color_primary": "#12345"}, {"color_primary": "url(x)"}, {"color_side": "#ffffff"}):
            self.assertEqual(call("PUT", "/api/admin/org", bad, self.admin)[0], 400, bad)
        self.assertEqual(call("PUT", "/api/admin/org", {"color_primary": "#0F766E", "color_side": "#0b3b3a"}, self.admin)[0], 200)
        self.assertEqual(call("GET", "/api/public/branding")[1]["colors"], {"primary": "#0f766e", "side": "#0b3b3a"})
        self.assertEqual(call("PUT", "/api/admin/org", {"color_primary": "", "color_side": ""}, self.admin)[0], 200)
        self.assertEqual(call("GET", "/api/public/branding")[1]["colors"], {"primary": "", "side": ""})

    def test_5_logo_rules(self):
        self.assertEqual(call("POST", "/api/admin/org/logo", raw=b"<svg onload=alert(1)/>", token=self.admin)[0], 400)
        self.assertEqual(call("POST", "/api/admin/org/logo", raw=b"GIF89a....", token=self.admin)[0], 400)
        self.assertEqual(call("POST", "/api/admin/org/logo", raw=PNG + b"0" * (1024 * 1024), token=self.admin)[0], 413)
        self.assertEqual(call("POST", "/api/admin/org/logo", raw=PNG, token=self.admin)[0], 200)
        s, d, h = call("GET", "/api/public/logo")
        self.assertEqual((s, d, h["Content-Type"]), (200, PNG, "image/png"))
        self.assertTrue(call("GET", "/api/public/branding")[1]["logo"])

    def test_6_letter_uses_profile_and_old_settings_still_work(self):
        s, c, _ = call("POST", "/api/correspondences", {"scope": "external", "direction": "outgoing", "subject": "خطاب", "party": "جهة", "doc_date": "2026-01-10", "body": "نص"}, self.admin)
        self.assertEqual(s, 201, c)
        l = call("GET", f"/api/correspondences/{c['id']}/letter", token=self.admin)[1]
        self.assertEqual((l["org_name"], l["org_footer"], l["has_logo"]), ("هيئة اختبار", "تذييل", True))
        self.assertIn("info@test.sa", l["org_contact"])
        # الواجهة القديمة للإعدادات ما زالت تعمل
        self.assertEqual(call("PUT", "/api/admin/settings", {"org_stamp": "ختم"}, self.admin)[0], 200)
        self.assertEqual(call("GET", "/api/admin/settings", token=self.admin)[1]["org_stamp"], "ختم")

    def test_7_logo_in_backup_and_delete(self):
        s, d, _ = call("POST", "/api/admin/backup", {}, self.admin)
        self.assertEqual(s, 201, d)
        z = zipfile.ZipFile(os.path.join(self.tmp.name, "backups", d["name"]))
        self.assertIn("branding/logo.bin", z.namelist())
        self.assertEqual(call("DELETE", "/api/admin/org/logo", token=self.admin)[0], 200)
        self.assertEqual(call("GET", "/api/public/logo")[0], 404)
        self.assertFalse(call("GET", "/api/public/branding")[1]["logo"])


if __name__ == "__main__":
    unittest.main()
