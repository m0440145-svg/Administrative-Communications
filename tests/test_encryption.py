"""تشفير المرفقات عند الراحة (اختياري): يتطلب مكتبة cryptography لاختبار التشفير نفسه، ويُتخطى دونها.
تشغيل كامل:  <python-with-cryptography> -m unittest tests.test_encryption"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18767
URL = f"http://127.0.0.1:{PORT}"
PW = "Admin12345"


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
            p = r.read()
            return r.status, (json.loads(p) if "json" in r.headers.get("Content-Type", "") else p)
    except urllib.error.HTTPError as e:
        p = e.read()
        try:
            return e.code, json.loads(p)
        except Exception:
            return e.code, p


def start(data_dir, enc_key=None, python=None):
    env = dict(os.environ, ACP_DATA=data_dir, ACP_PORT=str(PORT), ACP_ADMIN_PASSWORD=PW, ACP_BACKUP_HOURS="0")
    if enc_key:
        env["ACP_ENC_KEY"] = enc_key
    p = subprocess.Popen([python or sys.executable, os.path.join(ROOT, "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        if p.poll() is not None:
            return p
        try:
            call("GET", "/api/health")
            return p
        except Exception:
            time.sleep(0.1)
    return p


def stop(p):
    p.terminate()
    p.wait(5)


def admin_token():
    return call("POST", "/api/login", {"username": "admin", "password": PW})[1]["token"]


def make_corr(tok):
    return call("POST", "/api/correspondences", {"scope": "external", "direction": "incoming", "subject": "مشفّر", "party": "جهة"}, tok)[1]["id"]


@unittest.skipIf(Fernet is None, "مكتبة cryptography غير مثبّتة")
class EncryptionTest(unittest.TestCase):
    def test_encrypt_decrypt_and_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            k1, k2 = Fernet.generate_key().decode(), Fernet.generate_key().decode()
            plain = b"%PDF-1.4 sensitive body " * 10
            p = start(d, k1)
            try:
                tok = admin_token()
                cid = make_corr(tok)
                s, up = call("POST", f"/api/correspondences/{cid}/attachments", token=tok, headers={"X-Filename": "s.pdf"}, raw=plain)
                self.assertEqual(s, 201)
                stored = [os.path.join(d, "uploads", f) for f in os.listdir(os.path.join(d, "uploads"))][0]
                with open(stored, "rb") as f:
                    on_disk = f.read()
                self.assertNotIn(b"sensitive", on_disk)  # لا نص صريح على القرص
                self.assertEqual(call("GET", f"/api/attachments/{up['id']}", token=tok), (200, plain))
            finally:
                stop(p)
            # تدوير المفاتيح: مفتاح جديد أولًا والقديم للفك؛ تبقى الملفات القديمة مقروءة
            p = start(d, f"{k2},{k1}")
            try:
                tok = admin_token()
                self.assertEqual(call("GET", "/api/attachments/1", token=tok), (200, plain))
                cid = call("GET", "/api/correspondences?size=1", token=tok)[1]["items"][0]["id"]
                s, up2 = call("POST", f"/api/correspondences/{cid}/attachments", token=tok, headers={"X-Filename": "n.pdf"}, raw=b"second")
                self.assertEqual(call("GET", f"/api/attachments/{up2['id']}", token=tok), (200, b"second"))
            finally:
                stop(p)
            # مفتاح خاطئ: لا يُسرَّب شيء ويظهر خطأ واضح
            p = start(d, Fernet.generate_key().decode())
            try:
                tok = admin_token()
                s, body = call("GET", "/api/attachments/1", token=tok)
                self.assertEqual(s, 500)
                self.assertIn("فك تشفير", body["error"])
            finally:
                stop(p)
            # إيقاف التشفير: الملفات المشفّرة تبقى غير قابلة للقراءة دون مفتاح (لا قراءة صامتة لبيانات تالفة)
            p = start(d)
            try:
                tok = admin_token()
                self.assertEqual(call("GET", "/api/attachments/1", token=tok)[0], 500)
            finally:
                stop(p)


class FailClosedTest(unittest.TestCase):
    @unittest.skipIf(Fernet is not None, "المكتبة مثبّتة هنا؛ الاختبار للبيئة التي تفتقدها")
    def test_refuses_to_start_without_library(self):
        with tempfile.TemporaryDirectory() as d:
            p = start(d, "any-key")
            time.sleep(1)
            self.assertIsNotNone(p.poll())  # الخادم رفض البدء بدل العمل دون تشفير
            self.assertNotEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
