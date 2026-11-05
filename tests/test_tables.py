"""فرز قائمة المعاملات على الخادم (قائمة بيضاء).   python -m unittest tests.test_tables"""
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
PORT = 18774
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


class SortTest(unittest.TestCase):
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
        cls.t = call("POST", "/api/login", {"username": "admin", "password": "Admin12345"})[1]["token"]
        for subj, party in (("ب موضوع", "جهة ج"), ("أ موضوع", "جهة ب"), ("ت موضوع", "جهة أ")):
            assert call("POST", "/api/correspondences", {"scope": "external", "direction": "incoming", "subject": subj, "party": party, "doc_date": "2026-01-10"}, cls.t)[0] == 201

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.tmp.cleanup()

    def subjects(self, q):
        s, d = call("GET", "/api/correspondences?" + q, token=self.t)
        self.assertEqual(s, 200, d)
        return [x["subject"] for x in d["items"]]

    def test_sort(self):
        asc = self.subjects("by=subject&dir=asc")
        self.assertEqual(asc, sorted(asc))
        self.assertEqual(self.subjects("by=subject&dir=desc"), asc[::-1])
        self.assertEqual(self.subjects("by=party&dir=asc")[0], "ت موضوع")
        self.assertEqual(self.subjects("sort=old")[0], "ب موضوع")      # السلوك السابق
        self.assertEqual(self.subjects("")[0], "ت موضوع")              # الافتراضي: الأحدث أولًا

    def test_rejects_unknown_column(self):
        for q in ("by=password_hash", "by=subject&dir=up", "by=1;DROP TABLE users"):
            self.assertEqual(call("GET", "/api/correspondences?" + q.replace(" ", "%20"), token=self.t)[0], 400, q)

    def test_zz_awaiting_reply_kpi(self):
        kpi = lambda q="direction=incoming": call("GET", "/api/correspondences?" + q, token=self.t)[1]["kpi"]["awaiting_reply"]
        before = kpi()
        s, c = call("POST", "/api/correspondences", {"scope": "external", "direction": "incoming", "subject": "تحتاج رد", "party": "جهة", "doc_date": "2026-01-10", "needs_reply": 1}, self.t)
        self.assertEqual(s, 201, c)
        call("POST", "/api/correspondences", {"scope": "external", "direction": "incoming", "subject": "لا تحتاج رد", "party": "جهة", "doc_date": "2026-01-10"}, self.t)
        self.assertEqual(kpi(), before + 1)                       # لا تُحتسب التي لم تُصنَّف «تحتاج رد»
        awaiting = call("GET", "/api/correspondences?direction=incoming&reply=awaiting", token=self.t)[1]
        self.assertEqual(awaiting["total"], before + 1)           # الفلتر يطابق العدّاد
        s, r = call("POST", "/api/correspondences", {"scope": "external", "direction": "outgoing", "subject": "رد", "party": "جهة", "doc_date": "2026-01-11", "parent_id": c["id"]}, self.t)
        self.assertEqual(s, 201, r)
        self.assertEqual(kpi(), before)                           # بعد الرد يخرج من العدّاد
        self.assertEqual(kpi("direction=outgoing"), 0)


if __name__ == "__main__":
    unittest.main()
