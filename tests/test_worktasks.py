"""المهام: إضافة مهمة، تحويل معاملة عاجلة إلى مهمة، الصلاحيات والخصوصية والإشعارات.   python -m unittest tests.test_worktasks"""
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
PORT = 18775
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


class TaskTest(unittest.TestCase):
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
        cls.tok = {}
        for u in ("emp_a", "emp_b"):
            assert call("POST", "/api/users", {"username": u, "full_name": u, "password": "Passw0rd123", "role": "employee", "clearance": 0, "department_id": 2}, cls.admin)[0] == 201
            t = call("POST", "/api/login", {"username": u, "password": "Passw0rd123"})[1]["token"]
            call("POST", "/api/me/password", {"current": "Passw0rd123", "new": "Newpass456"}, t)
            cls.tok[u] = call("POST", "/api/login", {"username": u, "password": "Newpass456"})[1]["token"]
        cls.uid = {x["username"]: x["id"] for x in call("GET", "/api/users", token=cls.admin)[1]}

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.tmp.cleanup()

    def corr(self, **kw):
        b = dict(scope="external", direction="incoming", subject="معاملة عاجلة", party="جهة", doc_date="2026-01-10", priority="urgent")
        b.update(kw)
        s, c = call("POST", "/api/correspondences", b, self.admin)
        self.assertEqual(s, 201, c)
        return c

    def test_1_create_and_complete(self):
        s, t = call("POST", "/api/work-tasks", {"title": "مهمة شخصية", "due_date": "2026-12-01", "priority": "high"}, self.tok["emp_a"])
        self.assertEqual((s, t["assignee_id"], t["priority"]), (201, self.uid["emp_a"], "high"))
        for bad in ({"title": ""}, {"title": "x", "due_date": "01/12/2026"}, {"title": "x", "priority": "boom"}, {"title": "x", "due_date": "2026-13-45"}):
            self.assertEqual(call("POST", "/api/work-tasks", bad, self.tok["emp_a"])[0], 400, bad)
        self.assertEqual(call("POST", "/api/work-tasks", {"title": "x", "assignee_id": self.uid["emp_b"]}, self.tok["emp_a"])[0], 403)  # الموظف يُسند لنفسه فقط
        self.assertEqual(call("POST", f"/api/work-tasks/{t['id']}/complete", {"note": "تم"}, self.tok["emp_b"])[0], 403)
        self.assertEqual(call("POST", f"/api/work-tasks/{t['id']}/complete", {"note": "تم"}, self.tok["emp_a"])[0], 200)
        open_ = call("GET", "/api/work-tasks?status=open", token=self.tok["emp_a"])[1]
        self.assertNotIn(t["id"], [x["id"] for x in open_])
        self.assertEqual(call("POST", f"/api/work-tasks/{t['id']}/reopen", {}, self.tok["emp_a"])[0], 200)
        self.assertEqual(call("DELETE", f"/api/work-tasks/{t['id']}", token=self.tok["emp_b"])[0], 403)
        self.assertEqual(call("DELETE", f"/api/work-tasks/{t['id']}", token=self.tok["emp_a"])[0], 200)

    def test_2_assign_by_admin_notifies(self):
        s, t = call("POST", "/api/work-tasks", {"title": "أنجز التقرير", "assignee_id": self.uid["emp_b"], "due_date": "2026-12-05"}, self.admin)
        self.assertEqual(s, 201, t)
        mine = call("GET", "/api/work-tasks", token=self.tok["emp_b"])[1]
        self.assertIn(t["id"], [x["id"] for x in mine])
        self.assertNotIn(t["id"], [x["id"] for x in call("GET", "/api/work-tasks", token=self.tok["emp_a"])[1]])
        notes = call("GET", "/api/notifications", token=self.tok["emp_b"])[1]["items"]
        self.assertTrue(any("أنجز التقرير" in n["title"] for n in notes))
        created = call("GET", "/api/work-tasks?scope=created", token=self.admin)[1]
        self.assertIn(t["id"], [x["id"] for x in created])
        self.assertEqual(call("GET", "/api/work-tasks?scope=all", token=self.tok["emp_a"])[0], 403)
        self.assertEqual(call("POST", f"/api/work-tasks/{t['id']}/complete", {}, self.tok["emp_b"])[0], 200)
        self.assertTrue(any("أُنجزت" in n["title"] for n in call("GET", "/api/notifications", token=self.admin)[1]["items"]))

    def test_3_urgent_correspondence_to_task(self):
        c = self.corr()
        s, t = call("POST", f"/api/correspondences/{c['id']}/to-task", {}, self.admin)
        self.assertEqual(s, 201, t)
        self.assertEqual((t["priority"], t["corr_id"], t["ref_no"]), ("urgent", c["id"], c["ref_no"]))
        self.assertTrue(t["title"].startswith("متابعة:") and t["due_date"])
        listed = call("GET", f"/api/work-tasks?corr_id={c['id']}", token=self.admin)[1]
        self.assertEqual([x["id"] for x in listed], [t["id"]])
        n = self.corr(priority="normal")  # غير عاجلة: أولوية عادية
        self.assertEqual(call("POST", f"/api/correspondences/{n['id']}/to-task", {"due_date": "2026-12-31"}, self.admin)[1]["priority"], "normal")

    def test_4_privacy_of_linked_correspondence(self):
        c = self.corr(priority="normal", subject="سري جدًا عن الراتب", conf_level=1)
        # موظف لا يرى المعاملة لا يستطيع ربط مهمة بها ولا تحويلها
        self.assertEqual(call("POST", f"/api/correspondences/{c['id']}/to-task", {}, self.tok["emp_a"])[0], 404)
        self.assertEqual(call("POST", "/api/work-tasks", {"title": "x", "ref_no": c["ref_no"]}, self.tok["emp_a"])[0], 404)
        self.assertEqual(call("GET", f"/api/work-tasks?corr_id={c['id']}", token=self.tok["emp_a"])[0], 404)
        # مهمة أُسندت لموظف لا يرى المعاملة: يرى المهمة دون موضوع المعاملة ودون رابط
        s, t = call("POST", f"/api/correspondences/{c['id']}/to-task", {"assignee_id": self.uid["emp_a"]}, self.admin)
        self.assertEqual(s, 201, t)
        mine = [x for x in call("GET", "/api/work-tasks", token=self.tok["emp_a"])[1] if x["id"] == t["id"]][0]
        self.assertIsNone(mine["corr_subject"])
        self.assertIsNone(mine["corr_id"])
        self.assertNotIn("الراتب", json.dumps({k: v for k, v in mine.items() if k != "title"}, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
