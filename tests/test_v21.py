"""اختبارات الإصدار 2.1: الحالات والمسودات، البحث المركّب، SLA والتصعيد، قنوات البريد/SMS، استقبال EML/IMAP، SSE،
كشف الانحراف، التقارير المجدولة، الملف الموحّد، QR/باركود، MFA، ABAC، سجل غير قابل للتعديل، JWT/OpenAPI، مسارات متوازية،
الأرشيف البارد، لجنة الإتلاف، النسخ البديل، BI.   python -m unittest tests.test_v21"""
import base64
import hashlib
import hmac
import http.client
import io
import json
import os
import socketserver
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18769
URL = f"http://127.0.0.1:{PORT}"
ADMIN_PW = "Admin12345"
USER_PW = "Newpass456"


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
            return r.status, (json.loads(p) if "json" in r.headers.get("Content-Type", "") else p), r.headers
    except urllib.error.HTTPError as e:
        p = e.read()
        try:
            return e.code, json.loads(p), e.headers
        except Exception:
            return e.code, p, e.headers


def login(u, p):
    s, d, _ = call("POST", "/api/login", {"username": u, "password": p})
    assert s == 200, d
    return d["token"]


def totp(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 15
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** 6).zfill(6)


class SmtpSink(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    messages = []


class SmtpHandler(socketserver.StreamRequestHandler):
    def handle(self):
        w = lambda s: self.wfile.write((s + "\r\n").encode())
        w("220 sink ready")
        data_mode, buf = False, []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if data_mode:
                if line in (b".\r\n", b".\n"):
                    self.server.messages.append(b"".join(buf))
                    data_mode, buf = False, []
                    w("250 queued")
                else:
                    buf.append(line[1:] if line.startswith(b"..") else line)
                continue
            cmd = line.decode(errors="ignore").strip().upper()
            if cmd.startswith(("EHLO", "HELO")):
                w("250 sink")
            elif cmd.startswith(("MAIL", "RCPT", "RSET", "NOOP")):
                w("250 ok")
            elif cmd.startswith("DATA"):
                w("354 go")
                data_mode = True
            elif cmd.startswith("QUIT"):
                w("221 bye")
                return
            else:
                w("250 ok")


class SmsGateway(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        SmsGateway.received.append((json.loads(self.rfile.read(n)), self.headers.get("Authorization")))
        self.send_response(200); self.end_headers(); self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


class V21Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.copy_dir = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp.name, "acp.db")
        cls.smtp = SmtpSink(("127.0.0.1", 0), SmtpHandler)
        cls.sms = HTTPServer(("127.0.0.1", 0), SmsGateway)
        threading.Thread(target=cls.smtp.serve_forever, daemon=True).start()
        threading.Thread(target=cls.sms.serve_forever, daemon=True).start()
        env = dict(os.environ, ACP_DATA=cls.tmp.name, ACP_PORT=str(PORT), ACP_ADMIN_PASSWORD=ADMIN_PW, ACP_BACKUP_HOURS="0",
                   ACP_SMTP_HOST="127.0.0.1", ACP_SMTP_PORT=str(cls.smtp.server_address[1]), ACP_SMTP_TLS="none", ACP_SMTP_FROM="acp@test.local",
                   ACP_SMS_URL=f"http://127.0.0.1:{cls.sms.server_address[1]}/send", ACP_SMS_TOKEN="smstok", ACP_BACKUP_COPY_DIR=cls.copy_dir)
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                call("GET", "/api/health")
                break
            except Exception:
                time.sleep(0.1)
        cls.admin = login("admin", ADMIN_PW)
        cls.tok, cls.uid = {}, {}
        specs = (("clerk1", "clerk", 1, 1, "clerk1@test.sa", "0500000001"), ("emp1", "employee", 0, 2, "emp1@test.sa", "0500000002"),
                 ("emp2", "employee", 0, 2, "emp2@test.sa", None), ("emp3", "employee", 0, 2, None, None), ("mgr1", "manager", 1, 2, "mgr1@test.sa", None))
        for u, role, clr, dept, mail, phone in specs:
            body = {"username": u, "full_name": u, "password": "Passw0rd123", "role": role, "clearance": clr, "department_id": dept}
            if mail: body["email"] = mail
            if phone: body["phone"] = phone
            assert call("POST", "/api/users", body, cls.admin)[0] == 201
            t = login(u, "Passw0rd123")
            assert call("POST", "/api/me/password", {"current": "Passw0rd123", "new": USER_PW}, t)[0] == 200
            cls.tok[u] = login(u, USER_PW)
        cls.uid = {x["username"]: x["id"] for x in call("GET", "/api/users", token=cls.admin)[1]}

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.smtp.shutdown(); cls.sms.shutdown()
        cls.tmp.cleanup()

    def corr(self, token=None, **kw):
        body = dict(scope="external", direction="incoming", subject="خطاب تجريبي", party="وزارة المالية", doc_date="2026-01-10")
        body.update(kw)
        return call("POST", "/api/correspondences", body, token or self.tok["clerk1"])

    def get(self, cid, token=None):
        return call("GET", f"/api/correspondences/{cid}", token=token or self.admin)

    def dbq(self, sql, params=()):
        c = sqlite3.connect(self.db)
        try:
            r = c.execute(sql, params).fetchall()
            c.commit()
            return r
        finally:
            c.close()

    def flush(self):
        return call("POST", "/api/admin/outbox/flush", token=self.admin)[1]

    def subjects_to(self, addr):
        out = []
        for m in self.smtp.messages:
            s = m.decode("utf-8", "ignore")
            if f"To: {addr}" in s:
                out.append(s)
        return out

    # ───────── الحالات والمسودات ─────────
    def test_01_statuses_and_drafts(self):
        s, d, _ = self.corr(self.tok["emp1"], scope="internal", draft=True, subject="مسودة خطاب", assigned_to=[self.uid["emp2"]])
        self.assertEqual(s, 201)
        det = self.get(d["id"], self.tok["emp1"])[1]
        self.assertEqual((det["status"], det["assignees"]), ("draft", []))  # المسودة بلا إسناد
        self.assertEqual(self.get(d["id"], self.tok["emp2"])[0], 404)        # لا يراها غير منشئها
        self.assertEqual(self.get(d["id"], self.tok["clerk1"])[0], 404)
        self.assertEqual(self.get(d["id"], self.admin)[0], 200)               # المدير الشامل يراها
        self.assertEqual(call("PUT", f"/api/correspondences/{d['id']}", {"status": "bogus"}, self.tok["emp1"])[0], 400)
        self.assertEqual(call("PUT", f"/api/correspondences/{d['id']}", {"status": "new"}, self.tok["emp1"])[0], 200)  # منشئ المسودة يقدّمها
        self.assertEqual(call("PUT", f"/api/correspondences/{d['id']}", {"status": "under_review"}, self.tok["emp1"])[0], 403)  # بعد التقديم لا صلاحية إدارة له
        for st in ("under_review", "awaiting_signature"):
            self.assertEqual(call("PUT", f"/api/correspondences/{d['id']}", {"status": st}, self.admin)[0], 200)

    # ───────── البحث المركّب ─────────
    def test_02_boolean_search(self):
        ids = {}
        for k, subj in (("a", "عقد صيانة مبنى الإدارة"), ("b", "عقد توريد أجهزة حاسب"), ("c", "مناقصة صيانة مكيفات")):
            ids[k] = self.corr(subject=subj + " بوليان")[1]["id"]
        q = lambda s: {i["id"] for i in call("GET", "/api/search?q=" + urllib.parse.quote(s + " بوليان"), token=self.admin)[1]["items"]}
        self.assertEqual(q("عقد AND صيانة"), {ids["a"]})
        self.assertEqual(q("عقد OR مناقصة"), set(ids.values()))
        self.assertEqual(q("عقد NOT صيانة"), {ids["b"]})
        self.assertEqual(q('"عقد توريد"'), {ids["b"]})
        self.assertEqual(q("صيانة -مناقصة"), {ids["a"]})
        self.assertEqual(q("(عقد OR مناقصة) AND صيانة"), {ids["a"], ids["c"]})
        self.assertEqual(q("عقد صيانة"), {ids["a"]})  # AND ضمني
        self.assertEqual(call("GET", "/api/search?q=" + urllib.parse.quote("NOT عقد"), token=self.admin)[0], 400)
        r = call("GET", "/api/search?q=" + urllib.parse.quote("صيانة بوليان") + "&direction=outgoing", token=self.admin)[1]
        self.assertEqual(r["items"], [])
        self.assertIn("AND", call("GET", "/api/search?q=" + urllib.parse.quote("عقد صيانة"), token=self.admin)[1]["query"])

    # ───────── SLA بساعات العمل والتصعيد ─────────
    def test_03_business_hours_unit(self):
        code = ("import os,sys,json; sys.path.insert(0,%r); import server as S; from datetime import datetime as D;"
                "c={'days':{6,0,1,2,3},'start':(8,0),'end':(16,0)}; f=lambda s,h: S.add_business_hours(D.strptime(s,'%%Y-%%m-%%d %%H:%%M'),h,c).strftime('%%Y-%%m-%%d %%H:%%M');"
                "b=lambda a,z: S.business_seconds(D.strptime(a,'%%Y-%%m-%%d %%H:%%M'),D.strptime(z,'%%Y-%%m-%%d %%H:%%M'),c)/3600;"
                "print(json.dumps([f('2026-11-08 09:00',4),f('2026-11-08 14:00',4),f('2026-11-12 15:00',4),f('2026-11-13 10:00',4),f('2026-11-08 07:00',2),b('2026-11-12 15:00','2026-11-15 10:00'),b('2026-11-13 08:00','2026-11-14 20:00')]))") % ROOT
        env = dict(os.environ, ACP_DATA=tempfile.mkdtemp())
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        res = json.loads(out.stdout.strip().splitlines()[-1])
        # 2026-11-08 أحد، 11-12 خميس، 11-13 جمعة (إجازة)، 11-14 سبت (إجازة)
        self.assertEqual(res[0], "2026-11-08 13:00")
        self.assertEqual(res[1], "2026-11-09 10:00")      # ساعتان اليوم وساعتان غدًا
        self.assertEqual(res[2], "2026-11-15 11:00")      # ساعة الخميس ثم الأحد 8–11
        self.assertEqual(res[3], "2026-11-15 12:00")      # يبدأ من أول الأحد
        self.assertEqual(res[4], "2026-11-08 10:00")      # قبل الدوام يبدأ عند 8
        self.assertEqual(res[5], 3.0)                     # الخميس 15–16 (1) + الأحد 8–10 (2)
        self.assertEqual(res[6], 0.0)                     # جمعة وسبت لا ساعات عمل

    def test_04_sla_assignment_and_escalation(self):
        a = self.admin
        self.assertEqual(call("PUT", "/api/admin/sla", {"policies": {"very_urgent": 4}, "work_days": "0,1,2,3,4,5,6", "work_start": "00:00", "work_end": "23:59"}, a)[0], 200)
        self.assertEqual(call("PUT", "/api/admin/sla", {"work_days": "9"}, a)[0], 400)
        self.assertEqual(call("PUT", "/api/admin/sla", {"policies": {"nope": 4}}, a)[0], 400)
        s, d, _ = self.corr(priority="very_urgent", assigned_to=[self.uid["emp1"]], department_id=[2], subject="عاجل جدًا SLA")
        cid = d["id"]
        det = self.get(cid)[1]
        self.assertTrue(det["sla_due_at"] and det["sla_hours"] == 4)
        s, n, _ = self.corr(subject="عادي بلا SLA")
        self.assertIsNone(self.get(n["id"])[1]["sla_due_at"])
        lst = call("GET", "/api/correspondences?size=100", token=self.admin)[1]["items"]
        self.assertEqual({i["id"]: i["sla_state"] for i in lst}[cid], "ok")
        # 80٪ من المهلة: تنبيه الموظف فقط
        t80 = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 3.2 * 3600))
        self.dbq("UPDATE correspondences SET created_at=? WHERE id=?", (t80, cid))
        r = call("POST", "/api/admin/reminders/run", token=a)[1]
        self.assertGreaterEqual(r["extra"]["sla_escalation"]["warn"], 1)
        n1 = call("GET", "/api/notifications", token=self.tok["emp1"])[1]["items"]
        self.assertTrue(any(x["kind"] == "sla_warn" and x["corr_id"] == cid for x in n1))
        self.assertFalse(any(x["kind"] == "sla_breach" and x["corr_id"] == cid for x in call("GET", "/api/notifications", token=self.tok["mgr1"])[1]["items"]))
        lst = call("GET", "/api/correspondences?size=100", token=self.admin)[1]["items"]
        self.assertEqual({i["id"]: i["sla_state"] for i in lst}[cid], "warn")
        # تجاوز المهلة: تصعيد للمدير المباشر والمدير الشامل
        past = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 5 * 3600))
        self.dbq("UPDATE correspondences SET created_at=?, sla_due_at=? WHERE id=?", (past, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 3600)), cid))
        call("POST", "/api/admin/reminders/run", token=a)
        self.assertTrue(any(x["kind"] == "sla_breach" and x["corr_id"] == cid for x in call("GET", "/api/notifications", token=self.tok["emp1"])[1]["items"]))
        self.assertTrue(any(x["kind"] == "sla_breach" and x["corr_id"] == cid for x in call("GET", "/api/notifications", token=self.tok["mgr1"])[1]["items"]))
        self.assertTrue(any(x["kind"] == "sla_breach" and x["corr_id"] == cid for x in call("GET", "/api/notifications", token=a)[1]["items"]))
        lst = call("GET", "/api/correspondences?size=100", token=self.admin)[1]["items"]
        self.assertEqual({i["id"]: i["sla_state"] for i in lst}[cid], "breach")
        r2 = call("POST", "/api/admin/reminders/run", token=a)[1]
        self.assertEqual(r2["extra"]["sla_escalation"], {"warn": 0, "breach": 0})  # لا تكرار
        # الإنجاز يُسقط التصعيد
        call("PUT", f"/api/correspondences/{cid}", {"status": "closed"}, self.tok["clerk1"])
        lst = call("GET", "/api/correspondences?size=100", token=self.admin)[1]["items"]
        self.assertIsNone({i["id"]: i["sla_state"] for i in lst}[cid])
        k = call("GET", "/api/kpi", token=a)[1]
        self.assertIn("sla_policy_compliance_pct", k)

    # ───────── قنوات البريد وSMS ─────────
    def test_05_email_sms_channels(self):
        self.smtp.messages.clear(); SmsGateway.received.clear()
        self.assertEqual(call("PUT", "/api/me/prefs", {"sms_on": True, "theme": "auto"}, self.tok["emp1"])[0], 200)
        ch = call("GET", "/api/admin/channels", token=self.admin)[1]
        self.assertTrue(ch["email"] and ch["sms"])
        s, d, _ = self.corr(assigned_to=[self.uid["emp1"]], subject="إسناد بالبريد")
        res = self.flush()
        self.assertGreaterEqual(res["sent"], 1)
        mails = self.subjects_to("emp1@test.sa")
        self.assertTrue(any("EXT-IN" in m for m in mails))
        # الموضوع السري لا يخرج خارج المنصة
        s, sec, _ = self.corr(priority="secret", assigned_to=[self.uid["mgr1"]], subject="موضوع سري جدا لا يخرج")
        self.flush()
        got = "".join(self.subjects_to("mgr1@test.sa"))
        self.assertIn("EXT-IN", got)
        raw = " ".join(base64.b64decode(p).decode("utf-8", "ignore") for p in __import__("re").findall(r"\r\n\r\n([A-Za-z0-9+/=\r\n]{20,})", got)) + got
        self.assertNotIn("موضوع سري جدا لا يخرج", raw)
        # تعطيل البريد من التفضيلات
        call("PUT", "/api/me/prefs", {"email_on": False, "theme": "auto"}, self.tok["emp2"])
        before = len(self.subjects_to("emp2@test.sa"))
        self.corr(assigned_to=[self.uid["emp2"]], subject="لن يصل بريد")
        self.flush()
        self.assertEqual(len(self.subjects_to("emp2@test.sa")), before)
        # SMS: تأخر المهلة فقط (sla_breach / overdue)
        self.corr(assigned_to=[self.uid["emp1"]], due_date="2020-01-01", subject="متأخرة للـSMS")
        call("POST", "/api/admin/reminders/run", token=self.admin)
        self.flush()
        self.assertTrue(any(m["to"] == "0500000002" and "EXT-IN" in m["message"] for m, tok in SmsGateway.received))
        self.assertTrue(all(tok == "Bearer smstok" for m, tok in SmsGateway.received))

    def test_06_send_outgoing_email_with_tracking(self):
        self.smtp.messages.clear()
        s, d, _ = self.corr(direction="outgoing", subject="خطاب للإرسال")
        cid = d["id"]
        up = call("POST", f"/api/correspondences/{cid}/attachments", token=self.tok["clerk1"], headers={"X-Filename": "letter.pdf"}, raw=b"%PDF-1.4 hello")[1]
        s, r, _ = call("POST", f"/api/correspondences/{cid}/send-email", {"to": "party@example.org", "message": "مرفق خطابنا", "att_ids": [up["id"]]}, self.tok["clerk1"])
        self.assertEqual((s, r["status"]), (201, "sent"))
        mail = self.subjects_to("party@example.org")[0]
        self.assertIn("letter.pdf", mail)
        self.assertIn("application/pdf", mail)
        dl = call("GET", f"/api/correspondences/{cid}/deliveries", token=self.tok["clerk1"])[1]
        self.assertEqual((dl[0]["status"], dl[0]["to_addr"]), ("sent", "party@example.org"))
        self.assertEqual(call("POST", f"/api/correspondences/{cid}/send-email", {"to": "not-an-email"}, self.tok["clerk1"])[0], 400)
        self.assertEqual(call("POST", f"/api/correspondences/{cid}/send-email", {"to": "x@y.sa"}, self.tok["emp1"])[0], 404)
        s, inc, _ = self.corr(subject="وارد لا يُرسل")
        self.assertEqual(call("POST", f"/api/correspondences/{inc['id']}/send-email", {"to": "x@y.sa"}, self.tok["clerk1"])[0], 400)
        s, sec, _ = self.corr(direction="outgoing", priority="secret", subject="صادر سري")
        self.assertEqual(call("POST", f"/api/correspondences/{sec['id']}/send-email", {"to": "x@y.sa"}, self.tok["clerk1"])[0], 400)

    # ───────── استقبال البريد (EML + IMAP) ─────────
    def _eml(self, mid="<m1@test>"):
        m = EmailMessage()
        m["From"] = "الجهة المرسلة <sender@ext.org>"
        m["To"] = "inbox@org.sa"
        m["Subject"] = "طلب اعتماد ميزانية من البريد"
        m["Date"] = "Wed, 04 Nov 2026 10:00:00 +0300"
        m["Message-ID"] = mid
        m.set_content("نص الرسالة الواردة عبر البريد الإلكتروني")
        m.add_attachment(b"%PDF-1.4 att", maintype="application", subtype="pdf", filename="budget.pdf")
        m.add_attachment(b"MZ evil", maintype="application", subtype="octet-stream", filename="bad.exe")
        return m.as_bytes()

    def test_07_eml_ingestion(self):
        s, r, _ = call("POST", "/api/admin/import/eml", token=self.tok["clerk1"], raw=self._eml())
        self.assertEqual(s, 201)
        det = self.get(r["id"])[1]
        self.assertEqual((det["source"], det["direction"], det["party"], det["doc_date"]), ("email", "incoming", "الجهة المرسلة", "2026-11-04"))
        self.assertEqual(det["subject"], "طلب اعتماد ميزانية من البريد")
        names = sorted(a["filename"] for a in det["attachments"])
        self.assertEqual(names, ["budget.pdf", "message.eml"])  # المرفق الخطر مرفوض والرسالة الأصلية محفوظة
        self.assertEqual(call("POST", "/api/admin/import/eml", token=self.tok["clerk1"], raw=self._eml())[1], {"duplicate": True})
        self.assertEqual(call("POST", "/api/admin/import/eml", token=self.tok["emp1"], raw=self._eml("<m2@test>"))[0], 403)
        self.assertTrue(any(i["id"] == r["id"] for i in call("GET", "/api/search?q=" + urllib.parse.quote("نص الرسالة الواردة"), token=self.admin)[1]["items"]))

    def test_08_imap_poll_with_fake_client(self):
        code = ("import os,sys,json; sys.path.insert(0,%r); import server as S; S.init_db(); c=S.connect();"
                "actor=dict(c.execute(\"SELECT * FROM users WHERE username='admin'\").fetchone())\n"
                "class F:\n    def __init__(s): s.flags=[]; s.raw=%r\n    def select(s,f): return ('OK',[b'1'])\n    def search(s,a,b): return ('OK',[b'1 2'])\n"
                "    def fetch(s,n,w): return ('OK',[(b'1 (RFC822)', s.raw if n==b'1' else b'garbage')])\n    def store(s,n,a,b): s.flags.append(n)\n"
                "f=F(); done=S.imap_poll(c,f,actor); print(json.dumps([done,[x.decode() for x in f.flags],c.execute(\"SELECT COUNT(*) FROM correspondences WHERE source='email'\").fetchone()[0]]))") % (ROOT, self._eml("<imap1@test>"))
        script = os.path.join(tempfile.mkdtemp(), "imap_case.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(code)
        out = subprocess.run([sys.executable, script], env=dict(os.environ, ACP_DATA=tempfile.mkdtemp(), ACP_ADMIN_PASSWORD="Admin12345", PYTHONIOENCODING="utf-8"),
                             capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        done, flags, n = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual((done, flags, n), (2, ["1", "2"], 2))  # الرسالة الفاسدة تُقيَّد أيضًا كمعاملة بلا مرفقات؛ ولا يُعلَّم مقروءًا إلا بعد نجاح القيد

    # ───────── الإشعارات اللحظية SSE ─────────
    def test_09_sse_realtime(self):
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=8)
        conn.request("GET", "/api/events", headers={"Authorization": "Bearer " + self.tok["emp3"]})
        resp = conn.getresponse()
        self.assertEqual((resp.status, resp.getheader("Content-Type").split(";")[0]), (200, "text/event-stream"))
        got = []

        def reader():
            try:
                while True:
                    line = resp.fp.readline()
                    if not line:
                        return
                    got.append(line.decode().strip())
                    if line.startswith(b"data: ") and "assigned" in line.decode():
                        return
            except Exception:
                return
        th = threading.Thread(target=reader, daemon=True); th.start()
        time.sleep(0.5)
        self.corr(assigned_to=[self.uid["emp3"]], subject="لحظي SSE")
        th.join(6)
        conn.close()
        self.assertTrue(any(l.startswith("data: ") and "assigned" in l and "SSE" not in "" for l in got), got)
        self.assertEqual(call("GET", "/api/events")[0], 401)

    # ───────── كشف الانحراف + التقارير المجدولة ─────────
    def test_10_anomaly_detection(self):
        today = time.strftime("%Y-%m-%d")
        for i in range(1, 8):
            day = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
            self.dbq("INSERT OR REPLACE INTO kpi_daily VALUES(?,?,?,?)", (day, 1, 5, i % 2))
        for i in range(6):
            self.corr(due_date="2020-01-01", subject=f"متأخرة انحراف {i}", department_id=1)
        r = call("POST", "/api/admin/reminders/run", token=self.admin)[1]
        found = r["extra"]["anomaly_scan"]
        self.assertTrue(any("ارتفاع غير معتاد" in f for f in found), found)
        self.assertTrue(any(n["kind"] == "anomaly" for n in call("GET", "/api/notifications", token=self.admin)[1]["items"]))
        # تكدس موظف بعينه
        for i in range(10):
            self.corr(assigned_to=[self.uid["emp3"]], subject=f"تكدس {i}")
        self.corr(assigned_to=[self.uid["emp2"]], subject="لموظف آخر")
        self.corr(assigned_to=[self.uid["emp1"]], subject="لموظف ثالث")
        found2 = call("POST", "/api/admin/reminders/run", token=self.admin)[1]["extra"]["anomaly_scan"]
        self.assertTrue(any("تكدس" in f and "emp3" in f for f in found2), found2)

    def test_11_scheduled_reports(self):
        self.smtp.messages.clear()
        wd = (time.localtime().tm_wday + 1) % 7
        s, sch, _ = call("POST", "/api/admin/report-schedules", {"name": "تقرير الأحد", "weekday": wd, "hour": 0, "recipients": [self.uid["emp1"], self.uid["mgr1"]]}, self.admin)
        self.assertEqual(s, 201)
        self.assertEqual(call("POST", "/api/admin/report-schedules", {"name": "x", "weekday": 9, "recipients": [1]}, self.admin)[0], 400)
        self.assertEqual(call("POST", "/api/admin/report-schedules", {"name": "x", "weekday": 1, "recipients": []}, self.admin)[0], 400)
        s, run, _ = call("POST", f"/api/admin/report-schedules/{sch['id']}/run", token=self.admin)
        self.assertEqual(s, 201)
        s, data, h = call("GET", f"/api/admin/report-runs/{run['run_id']}/download", token=self.admin)
        self.assertEqual(s, 200)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn("xl/worksheets/sheet1.xml", z.namelist())
            self.assertIn("ملخص المؤشرات".encode(), z.read("xl/workbook.xml"))
        self.flush()
        mail = self.subjects_to("emp1@test.sa")
        self.assertTrue(any(".xlsx" in m for m in mail))
        self.assertTrue(any(n["kind"] == "report" for n in call("GET", "/api/notifications", token=self.tok["emp1"])[1]["items"]))
        # التشغيل الدوري لا يكرر في اليوم نفسه بعد run (last_run=اليوم)
        n_runs = len(call("GET", "/api/admin/report-runs", token=self.admin)[1])
        call("POST", "/api/admin/reminders/run", token=self.admin)
        self.assertEqual(len(call("GET", "/api/admin/report-runs", token=self.admin)[1]), n_runs)
        self.assertEqual(call("GET", "/api/admin/report-runs", token=self.tok["clerk1"])[0], 403)

    # ───────── الملف الموحّد والتهميشات والربط والتكرار ─────────
    def test_12_dossier_notes_related_duplicates(self):
        c1 = self.tok["clerk1"]
        s, root, _ = self.corr(subject="معاملة أصل ملف", party="جهة الملف", party_ref="REF-777", needs_reply=1)
        call("POST", f"/api/correspondences/{root['id']}/attachments", token=c1, headers={"X-Filename": "a.pdf"}, raw=b"%PDF a")
        s, reply, _ = call("POST", "/api/correspondences", dict(scope="external", direction="outgoing", subject="رد على الأصل", party="جهة الملف", doc_date="2026-11-05", parent_id=root["id"]), c1)
        s, other, _ = self.corr(subject="مراسلة مرتبطة بالملف", party="جهة أخرى")
        self.assertEqual(call("POST", f"/api/correspondences/{root['id']}/related", {"ref_no": self.get(other["id"])[1]["ref_no"]}, c1)[0], 201)
        self.assertEqual(call("POST", f"/api/correspondences/{root['id']}/related", {"ref_no": "NOPE-1"}, c1)[0], 404)
        self.assertEqual(call("POST", f"/api/correspondences/{root['id']}/related", {"ref_no": self.get(root["id"])[1]["ref_no"]}, c1)[0], 400)
        self.assertEqual(call("POST", f"/api/correspondences/{root['id']}/notes", {"text": "ملاحظة بحثية فريدة زرقاء"}, c1)[0], 201)
        self.assertEqual(call("POST", f"/api/correspondences/{root['id']}/notes", {"kind": "stamp", "text": "معتمد"}, c1)[0], 201)
        self.assertEqual(call("POST", f"/api/correspondences/{root['id']}/notes", {"kind": "stamp", "text": "ختم مزيف"}, c1)[0], 400)
        for tok_name in ("emp1",):
            self.assertEqual(call("POST", f"/api/correspondences/{root['id']}/notes", {"text": "x"}, self.tok[tok_name])[0], 404)
        d = call("GET", f"/api/correspondences/{reply['id']}/dossier", token=c1)[1]  # من الرد نصل للأصل
        self.assertEqual((d["root"], d["current"]), (root["id"], reply["id"]))
        rel = {l["id"]: l["relation"] for l in d["letters"]}
        self.assertEqual(rel, {root["id"]: "الأصل", reply["id"]: "رد/متابعة", other["id"]: "مرتبطة"})
        types = {e["type"] for e in d["events"]}
        self.assertTrue({"letter", "attachment", "note", "stamp"} <= types)
        self.assertEqual([e["at"] for e in d["events"]], sorted(e["at"] for e in d["events"]))
        det = self.get(root["id"], c1)[1]
        self.assertEqual(len(det["notes"]), 2)
        self.assertEqual(det["related"][0]["id"], other["id"])
        self.assertTrue(any(i["id"] == root["id"] for i in call("GET", "/api/search?q=" + urllib.parse.quote("ملاحظة بحثية فريدة زرقاء"), token=self.admin)[1]["items"]))
        # الخصوصية: من لا يرى المراسلة لا يرى ملفها
        self.assertEqual(call("GET", f"/api/correspondences/{root['id']}/dossier", token=self.tok["emp1"])[0], 404)
        # التكرار
        dup = call("GET", "/api/duplicates?party=" + urllib.parse.quote("جهة الملف") + "&party_ref=REF-777&subject=x", token=c1)[1]
        self.assertTrue(dup and dup[0]["id"] == root["id"] and dup[0]["reason"] == "نفس رقم مرجع الجهة")
        dup2 = call("GET", "/api/duplicates?party=" + urllib.parse.quote("جهة الملف") + "&subject=" + urllib.parse.quote("معاملة أصل ملف"), token=c1)[1]
        self.assertTrue(any(x["id"] == root["id"] for x in dup2))
        self.assertEqual(call("GET", "/api/duplicates?party=zzz&subject=" + urllib.parse.quote("موضوع فريد تمامًا"), token=c1)[1], [])
        self.assertEqual(call("DELETE", f"/api/correspondences/{root['id']}/related/{other['id']}", token=c1)[0], 200)
        self.assertEqual(self.get(root["id"], c1)[1]["related"], [])

    def test_13_pins_columns(self):
        s, d, _ = self.corr(subject="للتثبيت")
        self.assertEqual(call("POST", f"/api/correspondences/{d['id']}/pin", {"pinned": True}, self.tok["clerk1"])[0], 200)
        lst = call("GET", "/api/correspondences?pinned=1", token=self.tok["clerk1"])[1]["items"]
        self.assertEqual([i["id"] for i in lst], [d["id"]])
        self.assertTrue(lst[0]["pinned"])
        self.assertEqual(call("GET", "/api/correspondences?pinned=1", token=self.admin)[1]["items"], [])  # تثبيت شخصي
        call("POST", f"/api/correspondences/{d['id']}/pin", {"pinned": False}, self.tok["clerk1"])
        self.assertEqual(call("GET", "/api/correspondences?pinned=1", token=self.tok["clerk1"])[1]["items"], [])
        self.assertEqual(call("PUT", "/api/me/prefs", {"columns": ["subject", "status", "sla"]}, self.tok["clerk1"])[0], 200)
        self.assertEqual(call("GET", "/api/me/prefs", token=self.tok["clerk1"])[1]["columns"], ["subject", "status", "sla"])
        self.assertEqual(call("PUT", "/api/me/prefs", {"columns": ["subject", "evil"]}, self.tok["clerk1"])[0], 400)

    # ───────── الباركود وQR والخطاب الرسمي والتصدير ─────────
    def test_14_label_lookup_letter_xlsx(self):
        c1 = self.tok["clerk1"]
        s, d, _ = self.corr(direction="outgoing", subject="خطاب رسمي للطباعة", party="الجهة المستلمة")
        cid = d["id"]
        call("PUT", "/api/admin/settings", {"org_name": "الجهة التجريبية", "org_header": "المملكة العربية السعودية", "org_footer": "ص.ب 1"}, self.admin)
        lab = call("GET", f"/api/correspondences/{cid}/label", token=c1)[1]
        self.assertTrue(lab["qr_svg"].startswith("<svg") and lab["barcode_svg"].startswith("<svg"))
        self.assertTrue(lab["payload"].startswith("ACP|EXT-OUT-"))
        self.assertEqual(call("GET", "/api/lookup?ref=" + lab["ref_no"], token=c1)[1]["id"], cid)
        self.assertEqual(call("GET", "/api/lookup?ref=" + urllib.parse.quote(lab["payload"]), token=c1)[1]["id"], cid)
        self.assertEqual(call("GET", "/api/lookup?ref=" + lab["ref_no"].lower(), token=c1)[0], 200)
        self.assertEqual(call("GET", "/api/lookup?ref=" + lab["ref_no"], token=self.tok["emp1"])[0], 404)
        self.assertEqual(call("GET", "/api/lookup?ref=bad%20ref!", token=c1)[0], 400)
        let = call("GET", f"/api/correspondences/{cid}/letter", token=c1)[1]
        self.assertEqual((let["org_name"], let["party"], let["subject"]), ("الجهة التجريبية", "الجهة المستلمة", "خطاب رسمي للطباعة"))
        s, data, h = call("GET", "/api/export/xlsx?direction=outgoing", token=c1)
        self.assertEqual(s, 200)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("خطاب رسمي للطباعة", sheet)
        self.assertEqual(call("GET", "/api/export/xlsx", token=self.tok["emp1"])[0], 403)

    # ───────── MFA (TOTP) ─────────
    def test_15_mfa_totp(self):
        t = self.tok["mgr1"]
        self.addCleanup(lambda: call("POST", "/api/admin/users/%d/mfa/reset" % self.uid["mgr1"], token=self.admin))
        self.assertFalse(call("GET", "/api/me/mfa", token=t)[1]["enabled"])
        self.assertTrue(call("GET", "/api/me/mfa", token=t)[1]["required"])  # المدراء مشمولون افتراضيًا
        s, setup, _ = call("POST", "/api/me/mfa/setup", token=t)
        self.assertEqual(s, 200)
        self.assertTrue(setup["uri"].startswith("otpauth://totp/") and setup["qr_svg"].startswith("<svg"))
        sec = setup["secret"]
        c0 = int(time.time() // 30)
        self.assertEqual(call("POST", "/api/me/mfa/enable", {"code": "000000"}, t)[0], 400)
        s, en, _ = call("POST", "/api/me/mfa/enable", {"code": totp(sec, c0)}, t)
        self.assertEqual(s, 200)
        rec = en["recovery_codes"]
        self.assertEqual(len(rec), 8)
        # الدخول يتطلب خطوة ثانية
        s, r, _ = call("POST", "/api/login", {"username": "mgr1", "password": USER_PW})
        self.assertEqual((s, r.get("mfa_required")), (200, True))
        self.assertNotIn("token", r)
        ticket = r["ticket"]
        self.assertEqual(call("POST", "/api/login/mfa", {"ticket": ticket, "code": "123456"})[0], 401)
        self.assertEqual(call("POST", "/api/login/mfa", {"ticket": ticket, "code": totp(sec, c0)})[0], 401)  # رمز مستخدم سابقًا (منع الإعادة)
        # رمز استرداد يعمل مرة واحدة فقط (ويدخل بدل TOTP)
        s, ok, _ = call("POST", "/api/login/mfa", {"ticket": ticket, "code": rec[0]})
        self.assertEqual(s, 200)
        self.assertEqual(call("GET", "/api/me", token=ok["token"])[1]["username"], "mgr1")
        ticket2 = call("POST", "/api/login", {"username": "mgr1", "password": USER_PW})[1]["ticket"]
        self.assertEqual(call("POST", "/api/login/mfa", {"ticket": ticket2, "code": rec[4]})[0], 200)
        ticket3 = call("POST", "/api/login", {"username": "mgr1", "password": USER_PW})[1]["ticket"]
        self.assertEqual(call("POST", "/api/login/mfa", {"ticket": ticket3, "code": rec[0]})[0], 401)
        self.assertEqual(call("GET", "/api/me/mfa", token=t)[1]["recovery_left"], 6)
        # محاولات كثيرة تُبطل التذكرة
        tk = call("POST", "/api/login", {"username": "mgr1", "password": USER_PW})[1]["ticket"]
        codes = [call("POST", "/api/login/mfa", {"ticket": tk, "code": "111111"})[0] for _ in range(6)]
        self.assertEqual(codes[-1], 401 if False else codes[-1])
        self.assertIn(429, codes + [call("POST", "/api/login/mfa", {"ticket": tk, "code": "111111"})[0]])
        # التوقيع يتطلب OTP بعد تفعيل المصادقة الثنائية (لمن يملك صلاحية الاعتماد)
        call("PUT", "/api/admin/perms", {"role": "manager", "perm": "manage_all", "allowed": True}, self.admin)
        s, d, _ = self.corr(direction="outgoing", department_id=[2], subject="توقيع بـ OTP")
        tk2 = call("POST", "/api/login", {"username": "mgr1", "password": USER_PW})[1]["ticket"]
        mtok = call("POST", "/api/login/mfa", {"ticket": tk2, "code": rec[1]})[1]["token"]  # c0+1 لم يُستهلك بعد
        self.assertEqual(call("POST", f"/api/correspondences/{d['id']}/approve", {"password": USER_PW}, mtok)[0], 400)
        self.assertEqual(call("POST", f"/api/correspondences/{d['id']}/approve", {"password": USER_PW, "otp": "000000"}, mtok)[0], 400)
        self.assertEqual(call("POST", f"/api/correspondences/{d['id']}/approve", {"password": USER_PW, "otp": totp(sec, int(time.time() // 30) + 1)}, mtok)[0], 201)
        # الإيقاف يتطلب كلمة المرور + رمزًا؛ وللمدير إعادة الضبط
        self.assertEqual(call("POST", "/api/me/mfa/disable", {"password": "bad", "code": rec[2]}, mtok)[0], 400)
        self.assertEqual(call("POST", "/api/me/mfa/disable", {"password": USER_PW, "code": rec[2]}, mtok)[0], 200)  # كلمة مرور + رمز استرداد
        self.assertEqual(call("POST", "/api/login", {"username": "mgr1", "password": USER_PW})[1].get("mfa_required"), None)
        self.assertEqual(call("POST", "/api/admin/users/%d/mfa/reset" % self.uid["mgr1"], token=self.admin)[0], 400 if False else 200)
        self.tok["mgr1"] = login("mgr1", USER_PW)

    def test_15b_totp_login(self):
        t = self.tok["emp3"]
        self.addCleanup(lambda: call("POST", "/api/admin/users/%d/mfa/reset" % self.uid["emp3"], token=self.admin))
        sec = call("POST", "/api/me/mfa/setup", token=t)[1]["secret"]
        c0 = int(time.time() // 30)
        self.assertEqual(call("POST", "/api/me/mfa/enable", {"code": totp(sec, c0)}, t)[0], 200)
        tk = call("POST", "/api/login", {"username": "emp3", "password": USER_PW})[1]["ticket"]
        self.assertEqual(call("POST", "/api/login/mfa", {"ticket": tk, "code": totp(sec, c0 + 1)})[0], 200)  # TOTP صالح (العدّاد التالي)
        self.assertEqual(call("POST", "/api/login/mfa", {"ticket": "bogus", "code": "123456"})[0], 401)

    # ───────── ABAC الصارم + السجل غير القابل للتعديل + الاطلاع ─────────
    def test_16_strict_secret_audit_view(self):
        self.dbq("INSERT INTO classifications(key,label,color,conf_level,sort) VALUES('ts','سري للغاية','#000000',2,99)")
        self.dbq("INSERT INTO correspondences(ref_no,scope,direction,subject,party,doc_date,priority,conf_level,status,created_by,created_at,updated_at) "
                 "VALUES('EXT-IN-2000-99999','external','incoming','سري للغاية فعلًا','س','2026-01-01','ts',2,'new',?, '2026-01-01 00:00:00','2026-01-01 00:00:00')", (self.uid["clerk1"],))
        cid = self.dbq("SELECT id FROM correspondences WHERE ref_no='EXT-IN-2000-99999'")[0][0]
        self.assertEqual(self.get(cid)[0], 200)  # الوضع العادي: المدير الشامل يرى
        self.assertEqual(call("PUT", "/api/admin/settings", {"abac_strict_secret": "1"}, self.admin)[0], 200)
        self.assertEqual(self.get(cid)[0], 404)  # الصارم: لا يراها إلا الأطراف
        self.assertNotIn(cid, [i["id"] for i in call("GET", "/api/correspondences?size=100", token=self.admin)[1]["items"]])
        self.assertEqual(call("PUT", "/api/admin/settings", {"abac_strict_secret": "0"}, self.admin)[0], 200)
        self.assertEqual(self.get(cid)[0], 200)
        # محاولة وصول غير مصرح بها تُسجَّل
        self.get(cid, self.tok["emp1"])
        log = call("GET", "/api/admin/audit?action=access_denied", token=self.admin)[1]
        self.assertTrue(any(r["username"] == "emp1" and r["entity_id"] == cid for r in log))
        # الاطلاع يُسجَّل مرة كل 10 دقائق
        s, d, _ = self.corr(subject="سجل الاطلاع")
        for _ in range(3):
            self.get(d["id"], self.tok["clerk1"])
        views = [r for r in call("GET", "/api/admin/audit?action=view", token=self.admin)[1] if r["entity_id"] == d["id"] and r["username"] == "clerk1"]
        self.assertEqual(len(views), 1)
        self.assertIn("EXT-IN", views[0]["detail"])
        # السجل غير قابل للتعديل/الحذف
        rid = self.dbq("SELECT id FROM audit_log ORDER BY id DESC LIMIT 1")[0][0]
        for sql in ("UPDATE audit_log SET detail='x' WHERE id=?", "DELETE FROM audit_log WHERE id=?"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.dbq(sql, (rid,))
        self.assertTrue(call("GET", "/api/admin/audit/verify", token=self.admin)[1]["ok"])

    # ───────── JWT / OAuth2 / OpenAPI ─────────
    def test_17_jwt_oauth_openapi(self):
        s, k, _ = call("POST", "/api/admin/apikeys", {"name": "BI", "user_id": self.uid["clerk1"]}, self.admin)
        self.assertEqual(s, 201)
        s, tk, _ = call("POST", "/api/oauth/token", {"grant_type": "client_credentials", "client_secret": k["key"]})
        self.assertEqual((s, tk["token_type"], tk["expires_in"]), (200, "Bearer", 3600))
        me = call("GET", "/api/me", token=tk["access_token"])
        self.assertEqual((me[0], me[1]["username"]), (200, "clerk1"))
        self.assertEqual(call("GET", "/api/correspondences?size=1", token=tk["access_token"])[0], 200)
        head, body, sig = tk["access_token"].split(".")
        forged = f"{head}.{body}.{sig[:-2]}AA"
        self.assertEqual(call("GET", "/api/me", token=forged)[0], 401)
        payload = json.loads(base64.urlsafe_b64decode(body + "=="))
        payload["sub"] = self.uid["admin"] if "admin" in self.uid else 1
        body2 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        self.assertEqual(call("GET", "/api/me", token=f"{head}.{body2}.{sig}")[0], 401)  # تعديل الحمولة يُبطل التوقيع
        self.assertEqual(call("POST", "/api/oauth/token", {"grant_type": "client_credentials", "client_secret": "acp_bad"})[0], 401)
        self.assertEqual(call("POST", "/api/oauth/token", {"grant_type": "password"})[0], 400)
        call("DELETE", f"/api/admin/apikeys/{k['id']}", token=self.admin)
        self.assertEqual(call("POST", "/api/oauth/token", {"grant_type": "client_credentials", "client_secret": k["key"]})[0], 401)
        spec = call("GET", "/api/openapi.json")[1]
        self.assertEqual(spec["openapi"], "3.0.3")
        self.assertIn("post", spec["paths"]["/api/correspondences"])
        self.assertIn("{id}", "".join(spec["paths"]))
        self.assertGreater(len(spec["paths"]), 80)
        self.assertEqual(spec["paths"]["/api/admin/audit"]["get"]["x-roles"], ["admin"])

    # ───────── مسارات متوازية وشرطية ─────────
    def test_18_parallel_and_conditional_workflow(self):
        a, mg, adm = self.admin, self.uid["mgr1"], self.uid["admin"] if "admin" in self.uid else 1
        # لأن mgr1 يحتاج OTP بعد التجربة السابقة تم إعادة ضبطه
        steps = [{"name": "مراجعة أولى", "kind": "review", "user_id": mg, "days": 2},
                 {"name": "اعتماد المدير", "kind": "approve", "user_id": mg, "days": 2, "stage": 2},
                 {"name": "اعتماد الإدارة العليا", "kind": "approve", "user_id": adm, "days": 2, "stage": 2},
                 {"name": "للعاجل فقط", "kind": "review", "user_id": mg, "days": 1, "when": {"priority_in": ["urgent", "very_urgent"]}},
                 {"name": "ختام", "kind": "review", "user_id": adm, "days": 1}]
        s, w, _ = call("POST", "/api/workflows", {"name": "متوازي شرطي", "steps": steps, "close_on_finish": True}, a)
        self.assertEqual(s, 201, w)
        self.assertEqual(call("POST", "/api/workflows", {"name": "سيء", "steps": [{"name": "x", "kind": "review", "role": "admin", "when": {"bad": 1}}]}, a)[0], 400)
        self.assertEqual(call("POST", "/api/workflows", {"name": "سيء2", "steps": [{"name": "x", "kind": "review", "role": "admin", "stage": 99}]}, a)[0], 400)
        s, d, _ = self.corr(direction="outgoing", subject="مسار متوازٍ")
        cid = d["id"]
        r = call("POST", f"/api/correspondences/{cid}/workflow", {"workflow_id": w["id"]}, self.tok["clerk1"])
        self.assertEqual(r[0], 201)
        self.assertEqual(r[1]["steps"], 4)  # خطوة «للعاجل فقط» استُبعدت لأن التصنيف عادي
        st = lambda: self.get(cid)[1]
        self.assertEqual(st()["status"], "under_review")
        steps_ = st()["workflow"]["steps"]
        self.assertEqual([(s_["stage"], s_["status"]) for s_ in steps_], [(0, "pending"), (1, "waiting"), (1, "waiting"), (2, "waiting")])
        mtok = self.tok["mgr1"]
        self.assertEqual(call("POST", f"/api/wf/steps/{steps_[0]['id']}/decide", {"decision": "done"}, mtok)[0], 200)
        steps_ = st()["workflow"]["steps"]
        self.assertEqual([s_["status"] for s_ in steps_], ["done", "pending", "pending", "waiting"])  # مرحلتان متوازيتان فُعّلتا معًا
        self.assertEqual(st()["status"], "awaiting_signature")
        # اعتماد أحدهما لا يُكمل المرحلة
        self.assertEqual(call("POST", f"/api/wf/steps/{steps_[1]['id']}/decide", {"decision": "approve", "password": USER_PW}, mtok)[0], 200)
        steps_ = st()["workflow"]["steps"]
        self.assertEqual([s_["status"] for s_ in steps_], ["done", "done", "pending", "waiting"])
        self.assertEqual(st()["status"], "awaiting_signature")
        self.assertEqual(call("POST", f"/api/wf/steps/{steps_[2]['id']}/decide", {"decision": "approve", "password": ADMIN_PW}, self.admin)[0], 200)
        steps_ = st()["workflow"]["steps"]
        self.assertEqual([s_["status"] for s_ in steps_], ["done", "done", "done", "pending"])
        self.assertEqual(call("POST", f"/api/wf/steps/{steps_[3]['id']}/decide", {"decision": "done"}, self.admin)[0], 200)
        det = st()
        self.assertEqual((det["workflow"]["status"], det["status"]), ("done", "closed"))
        self.assertEqual(len(det["signatures"]), 2)
        # الرفض في مرحلة متوازية يلغي الشقيقة
        s, d2, _ = self.corr(direction="outgoing", priority="urgent", subject="رفض متوازٍ")
        r2 = call("POST", f"/api/correspondences/{d2['id']}/workflow", {"workflow_id": w["id"]}, self.tok["clerk1"])
        self.assertEqual(r2[1]["steps"], 5)  # العاجل يشمل الخطوة الشرطية
        sx = self.get(d2["id"])[1]["workflow"]["steps"]
        call("POST", f"/api/wf/steps/{sx[0]['id']}/decide", {"decision": "done"}, mtok)
        sx = self.get(d2["id"])[1]["workflow"]["steps"]
        self.assertEqual(call("POST", f"/api/wf/steps/{sx[1]['id']}/decide", {"decision": "reject", "comment": "مرفوض", "password": USER_PW}, mtok)[0], 200)
        det2 = self.get(d2["id"])[1]
        self.assertEqual(det2["workflow"]["status"], "rejected")
        self.assertTrue(all(s_["status"] in ("done", "rejected", "skipped") for s_ in det2["workflow"]["steps"]))
        self.assertEqual(det2["status"], "in_progress")

    # ───────── الأرشيف البارد + لجنة الإتلاف + النسخ البديل ─────────
    def test_19_cold_tier_committee_offsite(self):
        a = self.admin
        s, d, _ = self.corr(subject="للأرشيف البارد")
        cid = d["id"]
        up = call("POST", f"/api/correspondences/{cid}/attachments", token=self.tok["clerk1"], headers={"X-Filename": "cold.pdf"}, raw=b"%PDF cold content")[1]
        call("PUT", f"/api/correspondences/{cid}", {"status": "archived"}, self.admin)
        self.dbq("UPDATE correspondences SET updated_at='2020-01-01 00:00:00' WHERE id=?", (cid,))
        self.assertEqual(call("PUT", "/api/admin/settings", {"cold_after_days": "30"}, a)[0], 200)
        res = call("POST", "/api/admin/cold/run", token=a)[1]
        self.assertGreaterEqual(res["moved"], 1)
        stored = self.dbq("SELECT stored_name FROM attachments WHERE id=?", (up["id"],))[0][0]
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "cold", stored)))
        self.assertFalse(os.path.isfile(os.path.join(self.tmp.name, "uploads", stored)))
        self.assertEqual(call("GET", f"/api/attachments/{up['id']}", token=a)[1], b"%PDF cold content")  # يقرأ من البارد بسلاسة
        # النسخ الاحتياطي يشمل البارد وينسخ لموقع بديل
        s, b, _ = call("POST", "/api/admin/backup", token=a)
        self.assertEqual(s, 201)
        with zipfile.ZipFile(os.path.join(self.tmp.name, "backups", b["name"])) as z:
            self.assertIn("cold/" + stored, z.namelist())
        self.assertTrue(os.path.isfile(os.path.join(self.copy_dir, b["name"])))
        sysd = call("GET", "/api/admin/system", token=a)[1]
        self.assertTrue(sysd["offsite_backup"]["ok"] and sysd["cold_files"] >= 1)
        # لجنة الوثائق: الإتلاف المباشر يُمنع عند اشتراط الاعتماد
        call("POST", "/api/admin/retention", {"name": "إتلاف", "after_days": 30, "action": "dispose"}, a)
        self.assertEqual(call("PUT", "/api/admin/settings", {"disposal_approvals": "1"}, a)[0], 200)
        direct = call("POST", "/api/admin/retention/apply", {"action": "dispose", "ids": [cid], "reason": "x", "password": ADMIN_PW}, a)
        self.assertEqual(direct[0], 400)
        self.assertIn("لجنة", direct[1]["error"])
        self.assertEqual(call("POST", "/api/admin/disposal-batches", {"ids": [cid], "reason": ""}, a)[0], 400)
        s, batch, _ = call("POST", "/api/admin/disposal-batches", {"ids": [cid], "reason": "انتهت المدة"}, a)
        self.assertEqual(s, 201)
        bid = batch["id"]
        vote = lambda tok, dec="approve", pw=USER_PW, cm=None: call("POST", f"/api/admin/disposal-batches/{bid}/vote", {"decision": dec, "password": pw, "comment": cm}, tok)
        self.assertEqual(vote(a, pw=ADMIN_PW)[0], 403)            # المنشئ لا يصوّت (فصل المهام)
        self.assertEqual(vote(self.tok["emp1"])[0], 403)           # ليس عضو لجنة
        call("PUT", "/api/admin/perms", {"role": "manager", "perm": "disposal_committee", "allowed": True}, a)
        self.tok["mgr1"] = login("mgr1", USER_PW)
        self.assertEqual(call("POST", f"/api/admin/disposal-batches/{bid}/execute", {"password": ADMIN_PW}, a)[0], 400)  # لم تُعتمد بعد
        self.assertEqual(vote(self.tok["mgr1"], pw="bad")[0], 400)
        listing = call("GET", "/api/admin/disposal-batches", token=self.tok["mgr1"])[1]
        self.assertEqual((listing[0]["id"], listing[0]["count"], listing[0]["status"]), (bid, 1, "pending"))
        self.assertEqual(vote(self.tok["mgr1"])[1], {"status": "approved"})
        self.assertEqual(vote(self.tok["mgr1"])[0], 404)           # أُغلق التصويت
        # تجميد قانوني بعد الاعتماد يستثني المراسلة عند التنفيذ
        call("POST", f"/api/correspondences/{cid}/legal-hold", {"hold": True, "reason": "قضية"}, a)
        r = call("POST", f"/api/admin/disposal-batches/{bid}/execute", {"password": ADMIN_PW}, a)[1]
        self.assertEqual((r["done"], r["skipped"]), (0, 1))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "cold", stored)))
        # دفعة ثانية بعد رفع التجميد تُنفَّذ فعلًا وتحذف الملف البارد
        call("POST", f"/api/correspondences/{cid}/legal-hold", {"hold": False}, a)
        batch2 = call("POST", "/api/admin/disposal-batches", {"ids": [cid], "reason": "دفعة ثانية"}, a)[1]["id"]
        call("POST", f"/api/admin/disposal-batches/{batch2}/vote", {"decision": "approve", "password": USER_PW}, self.tok["mgr1"])
        r2 = call("POST", f"/api/admin/disposal-batches/{batch2}/execute", {"password": ADMIN_PW}, a)[1]
        self.assertEqual(r2["done"], 1)
        self.assertFalse(os.path.isfile(os.path.join(self.tmp.name, "cold", stored)))
        self.assertEqual(call("GET", f"/api/attachments/{up['id']}", token=a)[0], 410)
        # الرفض يُغلق الدفعة
        s, d3, _ = self.corr(subject="دفعة مرفوضة")
        call("PUT", f"/api/correspondences/{d3['id']}", {"status": "archived"}, a)
        self.dbq("UPDATE correspondences SET updated_at='2020-01-01 00:00:00' WHERE id=?", (d3["id"],))
        b3 = call("POST", "/api/admin/disposal-batches", {"ids": [d3["id"]], "reason": "اختبار الرفض"}, a)[1]["id"]
        self.assertEqual(call("POST", f"/api/admin/disposal-batches/{b3}/vote", {"decision": "reject", "password": USER_PW}, self.tok["mgr1"])[0], 400)  # سبب الرفض إلزامي
        self.assertEqual(call("POST", f"/api/admin/disposal-batches/{b3}/vote", {"decision": "reject", "password": USER_PW, "comment": "نحتاجها"}, self.tok["mgr1"])[1], {"status": "rejected"})
        call("PUT", "/api/admin/settings", {"disposal_approvals": "0"}, a)

    # ───────── مزامنة الإجازات + موصّل BI ─────────
    def test_20_leaves_and_bi(self):
        csv_ok = "username,delegate,from,to,note\nemp1,emp2,2026-12-01,2026-12-10,إجازة سنوية\nemp1,emp1,2026-12-01,2026-12-02,نفسه\n".encode()
        r = call("POST", "/api/admin/import/leaves?dry_run=1", token=self.admin, raw=csv_ok)[1]
        self.assertEqual((r["created"], r["errors"]), (1, 1))
        self.assertEqual(call("GET", "/api/admin/delegations", token=self.admin)[1] and [d for d in call("GET", "/api/admin/delegations", token=self.admin)[1] if d["start_date"] == "2026-12-01"], [])
        r = call("POST", "/api/admin/import/leaves", token=self.admin, raw=csv_ok)[1]
        self.assertEqual(r["created"], 1)
        self.assertEqual(call("POST", "/api/admin/import/leaves", token=self.admin, raw=csv_ok)[1]["skipped"], 1)
        self.assertTrue(any(d["note"] == "إجازة سنوية" for d in call("GET", "/api/admin/delegations", token=self.admin)[1]))
        # BI
        bi = call("GET", "/api/bi/facts?size=5", token=self.tok["clerk1"])[1]
        self.assertEqual(len(bi["items"]), 5)
        self.assertGreater(bi["total"], 5)
        self.assertEqual(bi["next_page"], 2)
        self.assertNotIn("subject", bi["items"][0])  # لا نصوص افتراضيًا
        for k in ("ref_no", "status", "priority", "unit", "sla_due_at", "source", "attachments", "replies"):
            self.assertIn(k, bi["items"][0])
        with_text = call("GET", "/api/bi/facts?size=5&include_text=1", token=self.tok["clerk1"])[1]["items"]
        self.assertTrue(any("subject" in i for i in with_text))
        csvd = call("GET", "/api/bi/facts?format=csv&size=3", token=self.tok["clerk1"])[1]
        self.assertTrue(csvd.startswith(b"\xef\xbb\xbf") and csvd.count(b"\n") >= 3)
        self.assertEqual(call("GET", "/api/bi/facts", token=self.tok["emp1"])[0], 403)
        self.assertEqual(call("GET", "/api/bi/facts?since=2099-01-01", token=self.admin)[1]["total"], 0)
        k = call("GET", "/api/kpi", token=self.admin)[1]
        for key in ("by_source", "electronic_share_pct", "employee_turnaround", "unit_turnaround", "sla_breached_open"):
            self.assertIn(key, k)
        self.assertIn("email", k["by_source"])


if __name__ == "__main__":
    unittest.main()
