"""الدردشة داخل المعاملة: الصلاحيات والفورية والإشارات وغير المقروء.   python -m unittest tests.test_chat"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18778
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


class ChatTest(unittest.TestCase):
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
        cls.tok, cls.uid = {}, {}
        for u in ("chat_a", "chat_b", "chat_c"):
            assert call("POST", "/api/users", {"username": u, "full_name": u, "password": "Passw0rd123", "role": "employee", "clearance": 0, "department_id": 2}, cls.admin)[0] == 201
            t = call("POST", "/api/login", {"username": u, "password": "Passw0rd123"})[1]["token"]
            call("POST", "/api/me/password", {"current": "Passw0rd123", "new": "Newpass456"}, t)
            cls.tok[u] = call("POST", "/api/login", {"username": u, "password": "Newpass456"})[1]["token"]
        cls.uid = {x["username"]: x["id"] for x in call("GET", "/api/users", token=cls.admin)[1]}
        s, c = call("POST", "/api/correspondences", {"scope": "external", "direction": "incoming", "subject": "معاملة للدردشة", "party": "جهة", "doc_date": "2026-01-10",
                                                     "assigned_to": [cls.uid["chat_a"], cls.uid["chat_b"]]}, cls.admin)
        assert s == 201, c
        cls.cid, cls.ref = c["id"], c["ref_no"]

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.tmp.cleanup()

    def test_1_permissions(self):
        self.assertEqual(call("GET", f"/api/correspondences/{self.cid}/chat", token=self.tok["chat_c"])[0], 404)   # لا يرى المعاملة
        self.assertEqual(call("POST", f"/api/correspondences/{self.cid}/chat", {"body": "x"}, self.tok["chat_c"])[0], 404)
        self.assertEqual(call("POST", f"/api/correspondences/{self.cid}/chat", {"body": "   "}, self.tok["chat_a"])[0], 400)
        self.assertEqual(call("POST", f"/api/correspondences/{self.cid}/chat", {"body": "ا" * 2001}, self.tok["chat_a"])[0], 400)

    def test_2_messages_unread_and_delete(self):
        s, m = call("POST", f"/api/correspondences/{self.cid}/chat", {"body": "مرحبًا <b>زملاء</b>\x00"}, self.tok["chat_a"])
        self.assertEqual(s, 201, m)
        self.assertEqual(m["body"], "مرحبًا <b>زملاء</b>")                # المحارف التحكمية تُزال والنص يُخزَّن كما هو (يُعرض نصًا فقط)
        un = call("GET", "/api/chat/unread", token=self.tok["chat_b"])[1]
        self.assertEqual((un["total"], un["items"][0]["id"]), (1, self.cid))
        self.assertEqual(call("GET", "/api/chat/unread", token=self.tok["chat_a"])[1]["total"], 0)   # المرسل لا يُحتسب عليه
        r = call("GET", f"/api/correspondences/{self.cid}/chat", token=self.tok["chat_b"])[1]
        self.assertEqual([x["body"] for x in r["messages"]], ["مرحبًا <b>زملاء</b>"])
        self.assertFalse(r["messages"][0]["mine"])
        self.assertEqual(call("GET", "/api/chat/unread", token=self.tok["chat_b"])[1]["total"], 0)   # القراءة تُصفّر العدّاد
        self.assertEqual(call("GET", f"/api/correspondences/{self.cid}/chat?after={m['id']}", token=self.tok["chat_b"])[1]["messages"], [])
        self.assertEqual(call("DELETE", f"/api/chat/{m['id']}", token=self.tok["chat_b"])[0], 403)   # لا يحذف رسالة غيره
        self.assertEqual(call("DELETE", f"/api/chat/{m['id']}", token=self.tok["chat_a"])[0], 200)
        last = call("GET", f"/api/correspondences/{self.cid}/chat", token=self.tok["chat_b"])[1]["messages"][-1]
        self.assertEqual((last["deleted"], last["body"]), (True, None))

    def test_3_mention_notifies_only_who_can_see(self):
        call("POST", f"/api/correspondences/{self.cid}/chat", {"body": "@chat_b راجع هذا و@chat_c أيضًا"}, self.tok["chat_a"])
        nb = call("GET", "/api/notifications", token=self.tok["chat_b"])[1]["items"]
        self.assertTrue(any("ذكرك" in n["title"] for n in nb))
        nc = call("GET", "/api/notifications", token=self.tok["chat_c"])[1]["items"]
        self.assertFalse(any("ذكرك" in n["title"] for n in nc))      # chat_c لا يرى المعاملة: لا تسريب

    def test_4_realtime_event(self):
        got = []

        def listen(tok, ready):
            req = urllib.request.Request(URL + "/api/events", headers={"Authorization": "Bearer " + tok})
            with urllib.request.urlopen(req, timeout=20) as r:
                buf = b""
                ready.set()
                while len(got) < 1:
                    ch = r.read1(4096) if hasattr(r, "read1") else r.read(1)
                    if not ch:
                        break
                    buf += ch
                    if b"event: chat" in buf and b"\n\n" in buf.split(b"event: chat")[1]:
                        got.append(buf.decode())
                        break
        ready_b, ready_c = threading.Event(), threading.Event()
        tb = threading.Thread(target=listen, args=(self.tok["chat_b"], ready_b), daemon=True)
        tc_got = []
        tb.start()
        self.assertTrue(ready_b.wait(8))
        time.sleep(0.5)
        call("POST", f"/api/correspondences/{self.cid}/chat", {"body": "رسالة فورية"}, self.tok["chat_a"])
        tb.join(10)
        self.assertTrue(got, "لم يصل حدث chat للمتصل")
        self.assertIn(f'"corr_id": {self.cid}', got[0])
        self.assertIn(self.ref, got[0])


if __name__ == "__main__":
    unittest.main()
