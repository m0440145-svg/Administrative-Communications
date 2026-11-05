"""اختبارات المرحلة 0 للتحصين: كلمات المرور، حدّ المعدّل، خمول الجلسة، إبطال الجلسات، الترويسات، سلسلة السجل، Syslog.   python -m unittest tests.test_harden"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18772
URL = f"http://127.0.0.1:{PORT}"


def call(method, path, body=None, token=None):
    h, data = {}, None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(URL + path, data=data, method=method, headers=h)) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), e.headers


class HardenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        cls.udp.bind(("127.0.0.1", 0))
        cls.udp.settimeout(3)
        env = dict(os.environ, ACP_DATA=cls.tmp.name, ACP_PORT=str(PORT), ACP_ADMIN_PASSWORD="Admin12345", ACP_BACKUP_HOURS="0",
                   ACP_SYSLOG_HOST="127.0.0.1", ACP_SYSLOG_PORT=str(cls.udp.getsockname()[1]), ACP_HSTS="1")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                call("GET", "/api/health")
                break
            except Exception:
                time.sleep(0.1)
        cls.admin = call("POST", "/api/login", {"username": "admin", "password": "Admin12345"})[1]["token"]

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.udp.close()
        cls.tmp.cleanup()

    def put(self, **kw):
        s, d, _ = call("PUT", "/api/admin/settings", kw, self.admin)
        self.assertEqual(s, 200, d)
        time.sleep(0.05)

    def mkuser(self, name):
        s, d, _ = call("POST", "/api/users", {"username": name, "full_name": name, "password": "Passw0rd123", "role": "employee", "clearance": 0, "department_id": 2}, self.admin)
        self.assertEqual(s, 201, d)
        return call("POST", "/api/login", {"username": name, "password": "Passw0rd123"})[1]["token"]

    def test_1_headers(self):
        _, _, h = call("GET", "/api/health")
        self.assertIn("camera=()", h["Permissions-Policy"])
        self.assertEqual(h["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertIn("max-age=", h["Strict-Transport-Security"])
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])  # لم تتأثر الترويسات السابقة

    def test_2_password_policy(self):
        t = self.mkuser("pw1")
        ok = lambda new: call("POST", "/api/me/password", {"current": "Passw0rd123", "new": new}, t)[0]
        self.assertEqual(ok("Abcd1234"), 200)  # السلوك الافتراضي لم يتغيّر
        cur = {"v": "Abcd1234"}
        def chg(new):
            s = call("POST", "/api/me/password", {"current": cur["v"], "new": new}, t)[0]
            if s == 200: cur["v"] = new
            return s
        self.put(pw_min_length="12", pw_require_case="1", pw_block_common="1")
        self.assertEqual(chg("Abcd12345"), 400)           # أقصر من 12
        self.assertEqual(chg("abcdefgh12345"), 400)       # بلا حروف كبيرة
        self.assertEqual(chg("Abcdefgh12345"), 200)
        self.assertEqual(call("PUT", "/api/admin/settings", {"pw_min_length": "3"}, self.admin)[0], 400)
        self.put(pw_min_length="8", pw_require_case="0", pw_block_common="1")
        self.assertEqual(chg("Passw0rd123"), 400)         # شائعة
        self.put(pw_block_common="0")
        self.assertEqual(chg("Passw0rd123"), 200)         # الافتراضي يقبلها كما كان

    def test_3_password_change_revokes_other_sessions(self):
        t1 = self.mkuser("pw2")
        t2 = call("POST", "/api/login", {"username": "pw2", "password": "Passw0rd123"})[1]["token"]
        self.assertEqual(call("POST", "/api/me/password", {"current": "Passw0rd123", "new": "Newpass456"}, t1)[0], 200)
        self.assertEqual(call("GET", "/api/me", token=t1)[0], 200)   # الجلسة الحالية باقية
        self.assertEqual(call("GET", "/api/me", token=t2)[0], 401)   # الأخرى أُبطلت

    def test_4_rate_limit(self):
        self.put(rate_login_per_min="5")
        codes = [call("POST", "/api/login", {"username": "nobody", "password": "x"})[0] for _ in range(8)]
        self.assertIn(429, codes)
        self.assertEqual(codes[0], 401)
        self.put(rate_login_per_min="0")  # 0 = بلا حد
        self.assertNotIn(429, [call("POST", "/api/login", {"username": "nobody", "password": "x"})[0] for _ in range(8)])
        self.put(rate_login_per_min="60")

    def test_5_idle_timeout(self):
        t = self.mkuser("idl")
        self.put(idle_minutes="1")
        self.assertEqual(call("GET", "/api/me", token=t)[0], 200)
        db = sqlite3.connect(os.path.join(self.tmp.name, "acp.db"), timeout=30)
        db.execute("UPDATE session_activity SET last_seen='2000-01-01 00:00:00'")
        db.commit(); db.close()
        self.assertEqual(call("GET", "/api/me", token=t)[0], 401)
        self.put(idle_minutes="0")
        self.assertEqual(call("GET", "/api/me", token=self.admin)[0], 200)

    def test_6_chain_alert(self):
        db = sqlite3.connect(os.path.join(self.tmp.name, "acp.db"), timeout=30)
        for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='audit_log'").fetchall():
            db.execute(f"DROP TRIGGER {n}")
        db.execute("UPDATE audit_log SET detail='tampered' WHERE id=(SELECT MIN(id) FROM audit_log WHERE hash IS NOT NULL)")
        db.execute("DELETE FROM settings WHERE k='chain_last_check'")
        db.commit(); db.close()
        code = ("import server as S;c=S.connect();S.EXT_TICK[-1](c);"
                "print(c.execute(\"SELECT COUNT(*) FROM notifications WHERE title LIKE '%سلسلة سجل%'\").fetchone()[0]);"
                "S.EXT_TICK[-1](c);print(c.execute(\"SELECT COUNT(*) FROM notifications WHERE title LIKE '%سلسلة سجل%'\").fetchone()[0])")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=dict(os.environ, ACP_DATA=self.tmp.name, PYTHONIOENCODING="utf-8"),
                             capture_output=True, text=True, timeout=60).stdout.split()
        self.assertEqual(out[-2], "1")   # تنبيه واحد للمدير
        self.assertEqual(out[-1], "1")   # لا تكرار عند إعادة الفحص ضمن المدة

    def test_7_syslog(self):
        call("POST", "/api/login", {"username": "admin", "password": "Admin12345"})
        seen = []
        try:
            while True:
                seen.append(self.udp.recv(4096).decode())
        except socket.timeout:
            pass
        self.assertTrue(any("action=login" in m and "<110>1" in m for m in seen), seen[:3])


if __name__ == "__main__":
    unittest.main()
