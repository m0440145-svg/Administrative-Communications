#!/usr/bin/env python3
"""منصة الاتصالات الإدارية — خادم الواجهة البرمجية (بدون أي اعتماديات خارجية).

التشغيل:  python server.py            (الافتراضي 127.0.0.1:8080)
المتغيرات: ACP_HOST, ACP_PORT, ACP_DATA, ACP_ADMIN_PASSWORD
"""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import traceback
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("ACP_DATA", os.path.join(BASE, "data"))
UPLOADS = os.path.join(DATA, "uploads")
DB_PATH = os.path.join(DATA, "acp.db")
STATIC = os.path.join(BASE, "static")

MAX_JSON = 1 * 1024 * 1024
MAX_UPLOAD = 20 * 1024 * 1024
SESSION_HOURS = 8
MAX_FAILED = 5
LOCK_MINUTES = 15
ALLOWED_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv",
               ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".zip", ".msg", ".eml"}

ROLES = ("admin", "manager", "clerk", "employee")
STATUSES = ("new", "in_progress", "referred", "closed", "archived")
PRIORITIES = ("normal", "high", "urgent")
WEBHOOK_EVENTS = ("correspondence.created", "correspondence.referred", "correspondence.status")

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS departments(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, full_name TEXT NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL, department_id INTEGER REFERENCES departments(id),
  clearance INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
  must_change INTEGER NOT NULL DEFAULT 0, failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS counters(k TEXT PRIMARY KEY, n INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS correspondences(
  id INTEGER PRIMARY KEY, ref_no TEXT NOT NULL UNIQUE,
  scope TEXT NOT NULL CHECK(scope IN('external','internal')),
  direction TEXT NOT NULL CHECK(direction IN('incoming','outgoing')),
  subject TEXT NOT NULL, summary TEXT, party TEXT NOT NULL, party_ref TEXT,
  party_dept_id INTEGER REFERENCES departments(id),
  doc_date TEXT NOT NULL, category TEXT, priority TEXT NOT NULL DEFAULT 'normal',
  conf_level INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'new',
  department_id INTEGER REFERENCES departments(id), assigned_to INTEGER REFERENCES users(id),
  due_date TEXT, parent_id INTEGER REFERENCES correspondences(id),
  created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_c_status ON correspondences(status);
CREATE INDEX IF NOT EXISTS ix_c_dept ON correspondences(department_id);
CREATE INDEX IF NOT EXISTS ix_c_assigned ON correspondences(assigned_to);
CREATE INDEX IF NOT EXISTS ix_c_parent ON correspondences(parent_id);
CREATE TABLE IF NOT EXISTS attachments(
  id INTEGER PRIMARY KEY, corr_id INTEGER NOT NULL REFERENCES correspondences(id),
  filename TEXT NOT NULL, stored_name TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
  uploaded_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transfers(
  id INTEGER PRIMARY KEY, corr_id INTEGER NOT NULL REFERENCES correspondences(id),
  from_user INTEGER NOT NULL REFERENCES users(id), to_user INTEGER NOT NULL REFERENCES users(id),
  instruction TEXT, due_date TEXT, status TEXT NOT NULL DEFAULT 'pending',
  note TEXT, created_at TEXT NOT NULL, done_at TEXT);
CREATE INDEX IF NOT EXISTS ix_t_to ON transfers(to_user, status);
CREATE TABLE IF NOT EXISTS reads(
  corr_id INTEGER NOT NULL REFERENCES correspondences(id), user_id INTEGER NOT NULL REFERENCES users(id),
  at TEXT NOT NULL, PRIMARY KEY(corr_id, user_id));
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, user_id INTEGER, username TEXT, action TEXT NOT NULL,
  entity TEXT, entity_id INTEGER, detail TEXT, ip TEXT);
CREATE INDEX IF NOT EXISTS ix_a_entity ON audit_log(entity, entity_id);
CREATE TABLE IF NOT EXISTS api_keys(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id), active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS webhooks(
  id INTEGER PRIMARY KEY, url TEXT NOT NULL, events TEXT NOT NULL, secret TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
"""

DEFAULT_DEPARTMENTS = ["الإدارة العامة", "الشؤون الإدارية", "الشؤون المالية",
                       "الموارد البشرية", "تقنية المعلومات", "الشؤون القانونية"]


# ───────────────────────── أدوات عامة ─────────────────────────
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def hash_password(pw, iters=240_000):
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), iters).hex()
    return f"pbkdf2${iters}${salt}${h}"


def verify_password(pw, stored):
    try:
        _, iters, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), int(iters)).hex()
        return hmac.compare_digest(calc, h)
    except Exception:
        return False


DUMMY_HASH = hash_password("dummy-password-for-timing")


def check_password_policy(pw):
    if not isinstance(pw, str) or len(pw) < 8 or len(pw) > 128:
        raise ApiError(400, "كلمة المرور يجب أن تكون بين 8 و128 حرفًا")
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        raise ApiError(400, "كلمة المرور يجب أن تحتوي على حروف وأرقام")


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def audit(ctx, action, entity=None, entity_id=None, detail=None):
    u = ctx.user or {}
    who = u.get("username")
    if u.get("via"):
        who = f"{who} ({u['via']})"
    ctx.conn.execute(
        "INSERT INTO audit_log(at,user_id,username,action,entity,entity_id,detail,ip) VALUES(?,?,?,?,?,?,?,?)",
        (now(), u.get("id"), who, action, entity, entity_id, detail, ctx.ip))


def init_db():
    os.makedirs(UPLOADS, exist_ok=True)
    conn = connect()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0] == 0:
        for d in DEFAULT_DEPARTMENTS:
            conn.execute("INSERT INTO departments(name) VALUES(?)", (d,))
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        pw = os.environ.get("ACP_ADMIN_PASSWORD") or secrets.token_urlsafe(9) + "7a"
        admin_user = os.environ.get("ACP_ADMIN_USER", "admin")
        conn.execute(
            "INSERT INTO users(username,full_name,password_hash,role,department_id,clearance,must_change,created_at)"
            " VALUES(?,'مدير النظام',?, 'admin',1,2,?,?)",
            (admin_user, hash_password(pw), 0 if os.environ.get("ACP_ADMIN_PASSWORD") else 1, now()))
        print("=" * 60)
        print(" تم إنشاء حساب المدير لأول مرة:")
        print(f"   اسم المستخدم : {admin_user}")
        if not os.environ.get("ACP_ADMIN_PASSWORD"):
            print(f"   كلمة المرور  : {pw}   (سيُطلب تغييرها عند أول دخول)")
        else:
            print("   كلمة المرور  : (من المتغير ACP_ADMIN_PASSWORD)")
        print("=" * 60)
    conn.close()


# ───────────────────────── الصلاحيات ─────────────────────────
def visible_clause(u, alias="c"):
    """شرط SQL يحدد المراسلات التي يحق للمستخدم رؤيتها (الدور + درجة السرية + الارتباط)."""
    a = alias
    if u["role"] == "admin":
        return "1=1", []
    conf = f"{a}.conf_level<=?"
    involved = (f"{a}.created_by=? OR {a}.assigned_to=? OR EXISTS(SELECT 1 FROM transfers t "
                f"WHERE t.corr_id={a}.id AND (t.to_user=? OR t.from_user=?))")
    p = [u["clearance"]]
    ip = [u["id"], u["id"], u["id"], u["id"]]
    if u["role"] == "clerk":
        return f"({conf})", p
    if u["role"] == "manager" and u["department_id"]:
        return (f"({conf} AND ({a}.department_id=? OR {a}.party_dept_id=? OR {involved}))",
                p + [u["department_id"], u["department_id"]] + ip)
    return f"({conf} AND ({involved}))", p + ip


def get_visible(ctx, cid):
    sql, p = visible_clause(ctx.user)
    row = ctx.conn.execute(f"SELECT c.* FROM correspondences c WHERE c.id=? AND {sql}", [cid] + p).fetchone()
    if not row:
        raise ApiError(404, "المراسلة غير موجودة أو لا تملك صلاحية الاطلاع عليها")
    return row


def can_manage(u, corr):
    return u["role"] in ("admin", "manager", "clerk") or corr["assigned_to"] == u["id"]


# ───────────────────────── التحقق من المدخلات ─────────────────────────
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def f_text(b, name, maxlen, required=False, label=None):
    v = b.get(name)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise ApiError(400, f"الحقل مطلوب: {label or name}")
        return None
    if not isinstance(v, str):
        raise ApiError(400, f"قيمة غير صالحة: {label or name}")
    v = v.strip()
    if len(v) > maxlen:
        raise ApiError(400, f"الحقل طويل جدًا: {label or name}")
    return v


def f_date(b, name, required=False, label=None):
    v = f_text(b, name, 10, required, label)
    if v is None:
        return None
    if not DATE_RE.match(v):
        raise ApiError(400, f"تاريخ غير صالح: {label or name}")
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise ApiError(400, f"تاريخ غير صالح: {label or name}")
    return v


def f_enum(b, name, allowed, default=None, label=None):
    v = b.get(name, default)
    if v not in allowed:
        raise ApiError(400, f"قيمة غير مقبولة: {label or name}")
    return v


def f_int(b, name, lo=None, hi=None, required=False):
    v = b.get(name)
    if v in (None, ""):
        if required:
            raise ApiError(400, f"الحقل مطلوب: {name}")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, str)) or not str(v).lstrip("-").isdigit():
        raise ApiError(400, f"قيمة غير صالحة: {name}")
    v = int(v)
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ApiError(400, f"قيمة خارج النطاق: {name}")
    return v


def user_row(conn, uid):
    r = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
    if not r:
        raise ApiError(400, "المستخدم غير موجود أو معطّل")
    return r


def dept_exists(conn, did):
    if did is not None and not conn.execute("SELECT 1 FROM departments WHERE id=?", (did,)).fetchone():
        raise ApiError(400, "الإدارة غير موجودة")


# ───────────────────────── الويب هوك (التكامل) ─────────────────────────
def fire_webhooks(event, payload):
    def work():
        try:
            conn = connect()
            hooks = conn.execute("SELECT * FROM webhooks WHERE active=1").fetchall()
            conn.close()
            body = json.dumps({"event": event, "at": now(), "data": payload}, ensure_ascii=False).encode()
            for h in hooks:
                if event not in h["events"].split(","):
                    continue
                sig = hmac.new(h["secret"].encode(), body, hashlib.sha256).hexdigest()
                req = urllib.request.Request(h["url"], data=body, method="POST", headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "X-ACP-Event": event, "X-ACP-Signature": "sha256=" + sig})
                try:
                    urllib.request.urlopen(req, timeout=5).read(1024)
                except Exception as e:  # لا نوقف النظام عند فشل جهة خارجية
                    print(f"[webhook] {h['url']} فشل: {e}", file=sys.stderr)
        except Exception:
            traceback.print_exc()
    threading.Thread(target=work, daemon=True).start()


def hook_payload(c):
    d = {"id": c["id"], "ref_no": c["ref_no"], "scope": c["scope"], "direction": c["direction"],
         "status": c["status"], "conf_level": c["conf_level"]}
    if c["conf_level"] == 0:  # لا نُرسل عناوين المراسلات السرية خارج النظام
        d["subject"] = c["subject"]
    return d


# ───────────────────────── سياق الطلب والتوجيه ─────────────────────────
class Ctx:
    def __init__(self, handler, conn, user, params, query):
        self.h, self.conn, self.user, self.params, self.query = handler, conn, user, params, query
        self.ip = handler.client_address[0]
        self._body = None

    @property
    def body(self):
        if self._body is None:
            n = int(self.h.headers.get("Content-Length") or 0)
            if n > MAX_JSON:
                raise ApiError(413, "حجم الطلب كبير")
            raw = self.h.rfile.read(n) if n else b""
            try:
                self._body = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                raise ApiError(400, "صيغة JSON غير صالحة")
            if not isinstance(self._body, dict):
                raise ApiError(400, "صيغة الطلب غير صالحة")
        return self._body


class Raw:
    def __init__(self, status, body, headers):
        self.status, self.body, self.headers = status, body, headers


ROUTES = []


def route(method, pattern, auth=True, roles=None):
    def deco(fn):
        ROUTES.append((method, re.compile(pattern), fn, auth, roles))
        return fn
    return deco


# ───────────────────────── المصادقة ─────────────────────────
@route("POST", r"/api/login", auth=False)
def login(ctx):
    b = ctx.body
    username = f_text(b, "username", 64, True, "اسم المستخدم")
    pw = b.get("password")
    if not isinstance(pw, str) or len(pw) > 256:
        raise ApiError(400, "بيانات الدخول غير صحيحة")
    u = ctx.conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if u and u["locked_until"] and u["locked_until"] > now():
        raise ApiError(423, "الحساب مقفل مؤقتًا بسبب محاولات فاشلة، حاول لاحقًا")
    ok = verify_password(pw, u["password_hash"] if u else DUMMY_HASH)
    if not u or not ok or not u["active"]:
        if u:
            n = u["failed_attempts"] + 1
            lock = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S") if n >= MAX_FAILED else None
            ctx.conn.execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
                             (0 if lock else n, lock, u["id"]))
        ctx.user = {"username": username}
        audit(ctx, "login_failed")
        raise ApiError(401, "اسم المستخدم أو كلمة المرور غير صحيحة")
    ctx.conn.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (u["id"],))
    token = secrets.token_urlsafe(32)
    exp = (datetime.now() + timedelta(hours=SESSION_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    ctx.conn.execute("DELETE FROM sessions WHERE expires_at<?", (now(),))
    ctx.conn.execute("INSERT INTO sessions VALUES(?,?,?)", (sha(token), u["id"], exp))
    ctx.user = dict(u)
    audit(ctx, "login")
    return 200, {"token": token, "user": public_user(u, True)}


@route("POST", r"/api/logout")
def logout(ctx):
    tok = (ctx.h.headers.get("Authorization") or "")[7:]
    ctx.conn.execute("DELETE FROM sessions WHERE token_hash=?", (sha(tok),))
    audit(ctx, "logout")
    return 200, {"ok": True}


def public_user(u, full=False):
    d = {"id": u["id"], "full_name": u["full_name"], "role": u["role"], "department_id": u["department_id"]}
    if full:
        d.update(username=u["username"], clearance=u["clearance"], must_change=bool(u["must_change"]),
                 active=bool(u["active"]))
    return d


@route("GET", r"/api/me")
def me(ctx):
    return 200, public_user(ctx.user, True)


@route("POST", r"/api/me/password")
def change_password(ctx):
    b = ctx.body
    if not verify_password(str(b.get("current", "")), ctx.user["password_hash"]):
        raise ApiError(400, "كلمة المرور الحالية غير صحيحة")
    check_password_policy(b.get("new"))
    ctx.conn.execute("UPDATE users SET password_hash=?, must_change=0 WHERE id=?",
                     (hash_password(b["new"]), ctx.user["id"]))
    audit(ctx, "password_changed", "user", ctx.user["id"])
    return 200, {"ok": True}


# ───────────────────────── المراسلات ─────────────────────────
FIELDS = ("c.*, d.name AS department_name, pd.name AS party_dept_name, "
          "a.full_name AS assignee_name, cr.full_name AS creator_name")
JOINS = ("FROM correspondences c LEFT JOIN departments d ON d.id=c.department_id "
         "LEFT JOIN departments pd ON pd.id=c.party_dept_id "
         "LEFT JOIN users a ON a.id=c.assigned_to LEFT JOIN users cr ON cr.id=c.created_by")


def like(s):
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def build_filters(ctx):
    q, u = ctx.query, ctx.user
    sql, p = visible_clause(u)
    where = [sql]
    if q.get("scope") in ("external", "internal"):
        where.append("c.scope=?"); p.append(q["scope"])
    if q.get("direction") in ("incoming", "outgoing"):
        where.append("c.direction=?"); p.append(q["direction"])
    if q.get("status") in STATUSES:
        where.append("c.status=?"); p.append(q["status"])
    if q.get("priority") in PRIORITIES:
        where.append("c.priority=?"); p.append(q["priority"])
    if q.get("dept", "").isdigit():
        where.append("c.department_id=?"); p.append(int(q["dept"]))
    if DATE_RE.match(q.get("from", "")):
        where.append("c.doc_date>=?"); p.append(q["from"])
    if DATE_RE.match(q.get("to", "")):
        where.append("c.doc_date<=?"); p.append(q["to"])
    if q.get("overdue") == "1":
        where.append("c.due_date IS NOT NULL AND c.due_date<date('now','localtime') "
                     "AND c.status NOT IN('closed','archived')")
    if q.get("mine") == "1":
        where.append("c.assigned_to=?"); p.append(u["id"])
    if q.get("read") in ("read", "unread"):
        where.append(("" if q["read"] == "read" else "NOT ") +
                     "EXISTS(SELECT 1 FROM reads rd WHERE rd.corr_id=c.id AND rd.user_id=?)")
        p.append(u["id"])
    if q.get("has_att") == "1":
        where.append("EXISTS(SELECT 1 FROM attachments x WHERE x.corr_id=c.id)")
    since = {"today": "date('now','localtime')", "week": "date('now','localtime','-6 days')",
             "month": "date('now','localtime','-1 month')", "year": "date('now','localtime','-1 year')"}.get(q.get("period"))
    if since:
        where.append(f"c.created_at>={since}")
    s = (q.get("q") or "").strip()[:100]
    if s:
        k = like(s)
        where.append("(c.subject LIKE ? ESCAPE '\\' OR c.ref_no LIKE ? ESCAPE '\\' OR c.party LIKE ? ESCAPE '\\' "
                     "OR c.party_ref LIKE ? ESCAPE '\\' OR c.summary LIKE ? ESCAPE '\\')")
        p += [k] * 5
    return " AND ".join(where), p


@route("GET", r"/api/correspondences")
def list_corr(ctx):
    where, p = build_filters(ctx)
    page = max(1, int(ctx.query.get("page", "1")) if ctx.query.get("page", "1").isdigit() else 1)
    size = min(100, int(ctx.query["size"])) if ctx.query.get("size", "").isdigit() and int(ctx.query["size"]) > 0 else 20
    total = ctx.conn.execute(f"SELECT COUNT(*) {JOINS} WHERE {where}", p).fetchone()[0]
    extra = ("(SELECT COUNT(*) FROM attachments x WHERE x.corr_id=c.id) AS att_count, "
             "EXISTS(SELECT 1 FROM reads rd WHERE rd.corr_id=c.id AND rd.user_id=?) AS is_read")
    order = "ASC" if ctx.query.get("sort") == "old" else "DESC"
    rows = ctx.conn.execute(
        f"SELECT {FIELDS}, {extra} {JOINS} WHERE {where} ORDER BY c.id {order} LIMIT ? OFFSET ?",
        [ctx.user["id"]] + p + [size, (page - 1) * size]).fetchall()
    # مؤشرات الصفحة: تتبع النطاق والاتجاه فقط وتتجاهل بقية الفلاتر
    vs, vp = visible_clause(ctx.user)
    base, bp = [vs], list(vp)
    for k in ("scope", "direction"):
        if ctx.query.get(k) in ("external", "internal", "incoming", "outgoing"):
            base.append(f"c.{k}=?"); bp.append(ctx.query[k])
    bw = " AND ".join(base)

    def n(extra_sql="", params=()):
        return ctx.conn.execute(f"SELECT COUNT(*) FROM correspondences c WHERE {bw} {extra_sql}",
                                bp + list(params)).fetchone()[0]
    kpi = {"total": n(),
           "unread": n("AND NOT EXISTS(SELECT 1 FROM reads rd WHERE rd.corr_id=c.id AND rd.user_id=?)", [ctx.user["id"]]),
           "in_progress": n("AND c.status='in_progress'"),
           "urgent": n("AND c.priority='urgent' AND c.status NOT IN('closed','archived')")}
    return 200, {"total": total, "page": page, "size": size, "kpi": kpi, "items": [dict(r) for r in rows]}


@route("POST", r"/api/correspondences/read")
def mark_read(ctx):
    ids = ctx.body.get("ids")
    if not isinstance(ids, list) or len(ids) > 200 or not all(isinstance(i, int) for i in ids):
        raise ApiError(400, "قائمة غير صالحة")
    vs, vp = visible_clause(ctx.user)
    with tx(ctx.conn):
        for i in ids:
            if ctx.conn.execute(f"SELECT 1 FROM correspondences c WHERE c.id=? AND {vs}", [i] + vp).fetchone():
                ctx.conn.execute("INSERT OR IGNORE INTO reads VALUES(?,?,?)", (i, ctx.user["id"], now()))
    return 200, {"ok": True}


@route("POST", r"/api/correspondences")
def create_corr(ctx):
    b, u, conn = ctx.body, ctx.user, ctx.conn
    scope = f_enum(b, "scope", ("external", "internal"), label="نطاق المراسلة")
    direction = f_enum(b, "direction", ("incoming", "outgoing"), label="الاتجاه")
    if scope == "external" and u["role"] == "employee":
        raise ApiError(403, "المراسلات الخارجية يسجّلها مكتب الصادر والوارد أو المدير فقط")
    subject = f_text(b, "subject", 300, True, "الموضوع")
    summary = f_text(b, "summary", 5000)
    party = f_text(b, "party", 200, True, "الجهة")
    party_ref = f_text(b, "party_ref", 100)
    doc_date = f_date(b, "doc_date", True, "تاريخ المراسلة")
    due = f_date(b, "due_date", label="تاريخ الاستحقاق")
    category = f_text(b, "category", 60)
    priority = f_enum(b, "priority", PRIORITIES, "normal", "الأولوية")
    conf = f_int(b, "conf_level", 0, 2) or 0
    if conf > u["clearance"]:
        raise ApiError(403, "لا يمكنك تسجيل مراسلة بدرجة سرية أعلى من صلاحيتك")
    dept = f_int(b, "department_id") if u["role"] in ("admin", "clerk") else None
    dept = dept or u["department_id"]
    pdept = f_int(b, "party_dept_id")
    dept_exists(conn, dept); dept_exists(conn, pdept)
    assigned = f_int(b, "assigned_to")
    if assigned:
        a = user_row(conn, assigned)
        if a["clearance"] < conf:
            raise ApiError(400, "المستخدم المُسنَد إليه لا يملك صلاحية لهذه الدرجة من السرية")
    parent = f_int(b, "parent_id")
    with tx(conn):
        if parent:
            get_visible(ctx, parent)
        year = date.today().year
        prefix = ("EXT" if scope == "external" else "INT") + "-" + ("IN" if direction == "incoming" else "OUT")
        key = f"{prefix}-{year}"
        r = conn.execute("SELECT n FROM counters WHERE k=?", (key,)).fetchone()
        n = (r[0] if r else 0) + 1
        conn.execute("INSERT INTO counters(k,n) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET n=excluded.n", (key, n))
        ref = f"{key}-{n:05d}"
        status = "referred" if assigned else "new"
        cur = conn.execute(
            "INSERT INTO correspondences(ref_no,scope,direction,subject,summary,party,party_ref,party_dept_id,doc_date,"
            "category,priority,conf_level,status,department_id,assigned_to,due_date,parent_id,created_by,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ref, scope, direction, subject, summary, party, party_ref, pdept, doc_date, category, priority, conf,
             status, dept, assigned, due, parent, u["id"], now(), now()))
        cid = cur.lastrowid
        conn.execute("INSERT INTO reads VALUES(?,?,?)", (cid, u["id"], now()))
        if assigned:
            conn.execute("INSERT INTO transfers(corr_id,from_user,to_user,instruction,due_date,created_at) VALUES(?,?,?,?,?,?)",
                         (cid, u["id"], assigned, "إسناد عند التسجيل", due, now()))
        audit(ctx, "create", "correspondence", cid, ref)
    row = conn.execute("SELECT * FROM correspondences WHERE id=?", (cid,)).fetchone()
    fire_webhooks("correspondence.created", hook_payload(row))
    return 201, {"id": cid, "ref_no": ref}


@route("GET", r"/api/correspondences/(?P<id>\d+)")
def get_corr(ctx):
    cid = int(ctx.params["id"])
    get_visible(ctx, cid)
    conn = ctx.conn
    conn.execute("INSERT OR IGNORE INTO reads VALUES(?,?,?)", (cid, ctx.user["id"], now()))
    c = dict(conn.execute(f"SELECT {FIELDS} {JOINS} WHERE c.id=?", (cid,)).fetchone())
    c["attachments"] = [dict(r) for r in conn.execute(
        "SELECT a.id,a.filename,a.size,a.sha256,a.created_at,u.full_name AS uploader FROM attachments a "
        "JOIN users u ON u.id=a.uploaded_by WHERE a.corr_id=? ORDER BY a.id", (cid,))]
    c["transfers"] = [dict(r) for r in conn.execute(
        "SELECT t.*, f.full_name AS from_name, o.full_name AS to_name FROM transfers t "
        "JOIN users f ON f.id=t.from_user JOIN users o ON o.id=t.to_user WHERE t.corr_id=? ORDER BY t.id", (cid,))]
    vs, vp = visible_clause(ctx.user)
    c["replies"] = [dict(r) for r in conn.execute(
        f"SELECT c.id,c.ref_no,c.subject,c.direction,c.scope,c.status,c.doc_date FROM correspondences c "
        f"WHERE c.parent_id=? AND {vs} ORDER BY c.id", [cid] + vp)]
    if c["parent_id"]:
        pr = conn.execute(f"SELECT c.id,c.ref_no,c.subject FROM correspondences c WHERE c.id=? AND {vs}",
                          [c["parent_id"]] + vp).fetchone()
        c["parent"] = dict(pr) if pr else None
    c["timeline"] = [dict(r) for r in conn.execute(
        "SELECT at,username,action,detail FROM audit_log WHERE entity='correspondence' AND entity_id=? "
        "AND action<>'view' ORDER BY id", (cid,))]
    c["can_manage"] = can_manage(ctx.user, c)
    return 200, c


@route("PUT", r"/api/correspondences/(?P<id>\d+)")
def update_corr(ctx):
    cid = int(ctx.params["id"])
    c = get_visible(ctx, cid)
    if not can_manage(ctx.user, c):
        raise ApiError(403, "لا تملك صلاحية تعديل هذه المراسلة")
    b, conn = ctx.body, ctx.conn
    sets, p, notes = [], [], []
    if "status" in b:
        st = f_enum(b, "status", STATUSES, label="الحالة")
        if c["status"] == "archived" and ctx.user["role"] != "admin":
            raise ApiError(403, "المراسلة المؤرشفة لا يعدّلها إلا المدير")
        sets.append("status=?"); p.append(st); notes.append(f"الحالة: {c['status']} ← {st}")
    if "priority" in b:
        pr = f_enum(b, "priority", PRIORITIES, label="الأولوية")
        sets.append("priority=?"); p.append(pr); notes.append(f"الأولوية: {pr}")
    if "due_date" in b:
        dd = f_date(b, "due_date", label="الاستحقاق")
        sets.append("due_date=?"); p.append(dd); notes.append(f"الاستحقاق: {dd}")
    if not sets:
        raise ApiError(400, "لا يوجد ما يُحدَّث")
    note = f_text(b, "note", 500)
    if note:
        notes.append("ملاحظة: " + note)
    sets.append("updated_at=?"); p.append(now())
    with tx(conn):
        conn.execute(f"UPDATE correspondences SET {','.join(sets)} WHERE id=?", p + [cid])
        audit(ctx, "update", "correspondence", cid, " | ".join(notes))
    if "status" in b:
        fire_webhooks("correspondence.status", hook_payload(conn.execute(
            "SELECT * FROM correspondences WHERE id=?", (cid,)).fetchone()))
    return 200, {"ok": True}


@route("POST", r"/api/correspondences/(?P<id>\d+)/transfers")
def add_transfer(ctx):
    cid = int(ctx.params["id"])
    c = get_visible(ctx, cid)
    if not can_manage(ctx.user, c):
        raise ApiError(403, "لا تملك صلاحية إحالة هذه المراسلة")
    if c["status"] in ("closed", "archived"):
        raise ApiError(400, "لا يمكن إحالة مراسلة مغلقة أو مؤرشفة")
    b, conn = ctx.body, ctx.conn
    to = f_int(b, "to_user", required=True)
    target = user_row(conn, to)
    if target["clearance"] < c["conf_level"]:
        raise ApiError(400, "المستخدم المستلم لا يملك صلاحية لهذه الدرجة من السرية")
    instr = f_text(b, "instruction", 1000)
    due = f_date(b, "due_date", label="الاستحقاق")
    with tx(conn):
        conn.execute("UPDATE transfers SET status='superseded' WHERE corr_id=? AND status='pending'", (cid,))
        conn.execute("INSERT INTO transfers(corr_id,from_user,to_user,instruction,due_date,created_at) VALUES(?,?,?,?,?,?)",
                     (cid, ctx.user["id"], to, instr, due, now()))
        conn.execute("UPDATE correspondences SET assigned_to=?, status='referred', due_date=COALESCE(?,due_date), "
                     "updated_at=? WHERE id=?", (to, due, now(), cid))
        audit(ctx, "transfer", "correspondence", cid, f"إحالة إلى {target['full_name']}" + (f" — {instr}" if instr else ""))
    fire_webhooks("correspondence.referred", hook_payload(conn.execute(
        "SELECT * FROM correspondences WHERE id=?", (cid,)).fetchone()))
    return 201, {"ok": True}


@route("POST", r"/api/transfers/(?P<id>\d+)/complete")
def complete_transfer(ctx):
    t = ctx.conn.execute("SELECT * FROM transfers WHERE id=?", (int(ctx.params["id"]),)).fetchone()
    if not t or t["to_user"] != ctx.user["id"] or t["status"] != "pending":
        raise ApiError(404, "الإحالة غير موجودة أو ليست موجّهة إليك")
    note = f_text(ctx.body, "note", 1000)
    with tx(ctx.conn):
        ctx.conn.execute("UPDATE transfers SET status='done', note=?, done_at=? WHERE id=?", (note, now(), t["id"]))
        ctx.conn.execute("UPDATE correspondences SET status='in_progress', updated_at=? "
                         "WHERE id=? AND status='referred'", (now(), t["corr_id"]))
        audit(ctx, "transfer_done", "correspondence", t["corr_id"], note or "تم الاطلاع/التنفيذ")
    return 200, {"ok": True}


@route("GET", r"/api/tasks")
def my_tasks(ctx):
    rows = ctx.conn.execute(
        "SELECT t.id AS transfer_id, t.instruction, t.due_date AS transfer_due, t.created_at AS transferred_at, "
        "c.id, c.ref_no, c.subject, c.priority, c.conf_level, c.status, f.full_name AS from_name "
        "FROM transfers t JOIN correspondences c ON c.id=t.corr_id JOIN users f ON f.id=t.from_user "
        "WHERE t.to_user=? AND t.status='pending' ORDER BY t.id DESC", (ctx.user["id"],)).fetchall()
    return 200, [dict(r) for r in rows]


# ───────────────────────── المرفقات ─────────────────────────
def clean_filename(raw):
    name = os.path.basename(urllib.parse.unquote(raw or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "", name).strip(". ")[:150]
    if not name:
        raise ApiError(400, "اسم الملف غير صالح")
    if os.path.splitext(name)[1].lower() not in ALLOWED_EXT:
        raise ApiError(400, "نوع الملف غير مسموح به")
    return name


@route("POST", r"/api/correspondences/(?P<id>\d+)/attachments")
def upload(ctx):
    cid = int(ctx.params["id"])
    c = get_visible(ctx, cid)
    if c["status"] == "archived":
        raise ApiError(400, "لا يمكن إضافة مرفقات لمراسلة مؤرشفة")
    name = clean_filename(ctx.h.headers.get("X-Filename"))
    n = int(ctx.h.headers.get("Content-Length") or 0)
    if n <= 0:
        raise ApiError(400, "الملف فارغ")
    if n > MAX_UPLOAD:
        raise ApiError(413, "حجم الملف يتجاوز 20 ميغابايت")
    data = ctx.h.rfile.read(n)
    stored = secrets.token_hex(16)
    with open(os.path.join(UPLOADS, stored), "wb") as f:
        f.write(data)
    digest = hashlib.sha256(data).hexdigest()
    try:
        with tx(ctx.conn):
            cur = ctx.conn.execute(
                "INSERT INTO attachments(corr_id,filename,stored_name,size,sha256,uploaded_by,created_at) VALUES(?,?,?,?,?,?,?)",
                (cid, name, stored, n, digest, ctx.user["id"], now()))
            audit(ctx, "attach", "correspondence", cid, f"{name} (sha256:{digest[:12]}…)")
    except BaseException:
        os.remove(os.path.join(UPLOADS, stored))
        raise
    return 201, {"id": cur.lastrowid, "sha256": digest}


@route("GET", r"/api/attachments/(?P<id>\d+)")
def download(ctx):
    a = ctx.conn.execute("SELECT * FROM attachments WHERE id=?", (int(ctx.params["id"]),)).fetchone()
    if not a:
        raise ApiError(404, "المرفق غير موجود")
    get_visible(ctx, a["corr_id"])
    path = os.path.join(UPLOADS, a["stored_name"])
    if not os.path.isfile(path):
        raise ApiError(404, "الملف مفقود من التخزين")
    with open(path, "rb") as f:
        data = f.read()
    if hashlib.sha256(data).hexdigest() != a["sha256"]:
        audit(ctx, "integrity_failure", "attachment", a["id"], a["filename"])
        raise ApiError(500, "فشل التحقق من سلامة الملف (البصمة لا تطابق)")
    audit(ctx, "download", "correspondence", a["corr_id"], a["filename"])
    q = urllib.parse.quote(a["filename"])
    return Raw(200, data, {"Content-Type": "application/octet-stream",
                           "Content-Disposition": f"attachment; filename*=UTF-8''{q}"})


# ───────────────────────── لوحة المعلومات والتصدير ─────────────────────────
@route("GET", r"/api/dashboard")
def dashboard(ctx):
    vs, vp = visible_clause(ctx.user)
    conn = ctx.conn

    def one(extra="", params=()):
        return conn.execute(f"SELECT COUNT(*) FROM correspondences c WHERE {vs} {extra}", vp + list(params)).fetchone()[0]

    by_status = {r[0]: r[1] for r in conn.execute(
        f"SELECT c.status, COUNT(*) FROM correspondences c WHERE {vs} GROUP BY c.status", vp)}
    by_type = [dict(scope=r[0], direction=r[1], n=r[2]) for r in conn.execute(
        f"SELECT c.scope, c.direction, COUNT(*) FROM correspondences c WHERE {vs} GROUP BY 1,2", vp)]
    monthly = [dict(month=r[0], direction=r[1], n=r[2]) for r in conn.execute(
        f"SELECT substr(c.doc_date,1,7), c.direction, COUNT(*) FROM correspondences c WHERE {vs} "
        f"AND c.doc_date>=date('now','localtime','-5 months','start of month') GROUP BY 1,2 ORDER BY 1", vp)]
    recent = [dict(r) for r in conn.execute(
        f"SELECT c.id,c.ref_no,c.subject,c.direction,c.scope,c.status,c.priority,c.doc_date FROM correspondences c "
        f"WHERE {vs} ORDER BY c.id DESC LIMIT 6", vp)]
    return 200, {
        "total": one(), "by_status": by_status, "by_type": by_type, "monthly": monthly, "recent": recent,
        "overdue": one("AND c.due_date IS NOT NULL AND c.due_date<date('now','localtime') AND c.status NOT IN('closed','archived')"),
        "urgent_open": one("AND c.priority='urgent' AND c.status NOT IN('closed','archived')"),
        "my_tasks": conn.execute("SELECT COUNT(*) FROM transfers WHERE to_user=? AND status='pending'",
                                 (ctx.user["id"],)).fetchone()[0],
    }


def csv_safe(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@route("GET", r"/api/export")
def export_csv(ctx):
    if ctx.user["role"] == "employee":
        raise ApiError(403, "التصدير متاح للمدراء ومكتب الصادر والوارد")
    where, p = build_filters(ctx)
    rows = ctx.conn.execute(f"SELECT {FIELDS} {JOINS} WHERE {where} ORDER BY c.id DESC LIMIT 20000", p).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["رقم القيد", "النطاق", "الاتجاه", "الموضوع", "الجهة", "رقم مرجع الجهة", "تاريخ المراسلة",
                "الأولوية", "السرية", "الحالة", "الإدارة", "المسؤول", "الاستحقاق", "تاريخ التسجيل"])
    for r in rows:
        w.writerow([csv_safe(x) for x in (
            r["ref_no"], r["scope"], r["direction"], r["subject"], r["party"], r["party_ref"], r["doc_date"],
            r["priority"], r["conf_level"], r["status"], r["department_name"], r["assignee_name"], r["due_date"],
            r["created_at"])])
    audit(ctx, "export", detail=f"{len(rows)} سجل")
    return Raw(200, ("﻿" + out.getvalue()).encode("utf-8"), {
        "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": "attachment; filename=correspondences.csv"})


# ───────────────────────── المستخدمون والإدارات ─────────────────────────
@route("GET", r"/api/departments")
def list_depts(ctx):
    return 200, [dict(r) for r in ctx.conn.execute("SELECT id,name,active FROM departments ORDER BY id")]


@route("POST", r"/api/departments", roles=("admin",))
def add_dept(ctx):
    name = f_text(ctx.body, "name", 100, True, "اسم الإدارة")
    try:
        cur = ctx.conn.execute("INSERT INTO departments(name) VALUES(?)", (name,))
    except sqlite3.IntegrityError:
        raise ApiError(409, "الإدارة موجودة مسبقًا")
    audit(ctx, "dept_create", "department", cur.lastrowid, name)
    return 201, {"id": cur.lastrowid}


@route("GET", r"/api/users")
def list_users(ctx):
    full = ctx.user["role"] == "admin"
    rows = ctx.conn.execute("SELECT * FROM users ORDER BY full_name").fetchall()
    return 200, [public_user(r, full) for r in rows if full or r["active"]]


@route("POST", r"/api/users", roles=("admin",))
def add_user(ctx):
    b = ctx.body
    username = f_text(b, "username", 32, True, "اسم المستخدم")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise ApiError(400, "اسم المستخدم: 3–32 حرفًا إنجليزيًا أو أرقامًا أو (_ . -)")
    check_password_policy(b.get("password"))
    full_name = f_text(b, "full_name", 100, True, "الاسم الكامل")
    role = f_enum(b, "role", ROLES, label="الدور")
    dept = f_int(b, "department_id")
    dept_exists(ctx.conn, dept)
    clearance = f_int(b, "clearance", 0, 2) or 0
    try:
        cur = ctx.conn.execute(
            "INSERT INTO users(username,full_name,password_hash,role,department_id,clearance,must_change,created_at) "
            "VALUES(?,?,?,?,?,?,1,?)", (username, full_name, hash_password(b["password"]), role, dept, clearance, now()))
    except sqlite3.IntegrityError:
        raise ApiError(409, "اسم المستخدم مستخدم مسبقًا")
    audit(ctx, "user_create", "user", cur.lastrowid, f"{username} ({role})")
    return 201, {"id": cur.lastrowid}


@route("PUT", r"/api/users/(?P<id>\d+)", roles=("admin",))
def edit_user(ctx):
    uid, b = int(ctx.params["id"]), ctx.body
    target = ctx.conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not target:
        raise ApiError(404, "المستخدم غير موجود")
    sets, p, notes = [], [], []
    if "full_name" in b:
        sets.append("full_name=?"); p.append(f_text(b, "full_name", 100, True, "الاسم"))
    if "role" in b:
        sets.append("role=?"); p.append(f_enum(b, "role", ROLES)); notes.append("role=" + b["role"])
    if "department_id" in b:
        d = f_int(b, "department_id"); dept_exists(ctx.conn, d); sets.append("department_id=?"); p.append(d)
    if "clearance" in b:
        sets.append("clearance=?"); p.append(f_int(b, "clearance", 0, 2, True)); notes.append(f"clearance={b['clearance']}")
    if "active" in b:
        if uid == ctx.user["id"] and not b["active"]:
            raise ApiError(400, "لا يمكنك تعطيل حسابك")
        sets.append("active=?"); p.append(1 if b["active"] else 0); notes.append(f"active={b['active']}")
    if b.get("password"):
        check_password_policy(b["password"])
        sets += ["password_hash=?", "must_change=1", "failed_attempts=0", "locked_until=NULL"]
        p.append(hash_password(b["password"])); notes.append("تصفير كلمة المرور")
    if not sets:
        raise ApiError(400, "لا يوجد ما يُحدَّث")
    with tx(ctx.conn):
        ctx.conn.execute(f"UPDATE users SET {','.join(sets)} WHERE id=?", p + [uid])
        if "active" in b and not b["active"]:
            ctx.conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        audit(ctx, "user_update", "user", uid, ", ".join(notes))
    return 200, {"ok": True}


# ───────────────────────── التدقيق والتكامل (للمدير) ─────────────────────────
@route("GET", r"/api/admin/audit", roles=("admin",))
def audit_list(ctx):
    q = ctx.query
    where, p = ["1=1"], []
    if q.get("action"):
        where.append("action=?"); p.append(q["action"][:40])
    if q.get("q"):
        where.append("(username LIKE ? ESCAPE '\\' OR detail LIKE ? ESCAPE '\\')"); p += [like(q["q"][:60])] * 2
    limit = min(500, int(q["limit"])) if q.get("limit", "").isdigit() else 200
    rows = ctx.conn.execute(f"SELECT * FROM audit_log WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", p + [limit])
    return 200, [dict(r) for r in rows]


@route("GET", r"/api/admin/apikeys", roles=("admin",))
def keys_list(ctx):
    return 200, [dict(r) for r in ctx.conn.execute(
        "SELECT k.id,k.name,k.prefix,k.active,k.created_at,u.full_name AS user_name FROM api_keys k "
        "JOIN users u ON u.id=k.user_id ORDER BY k.id DESC")]


@route("POST", r"/api/admin/apikeys", roles=("admin",))
def keys_create(ctx):
    name = f_text(ctx.body, "name", 80, True, "اسم المفتاح")
    user = user_row(ctx.conn, f_int(ctx.body, "user_id", required=True))
    key = "acp_" + secrets.token_urlsafe(32)
    cur = ctx.conn.execute("INSERT INTO api_keys(name,key_hash,prefix,user_id,created_at) VALUES(?,?,?,?,?)",
                           (name, sha(key), key[:10], user["id"], now()))
    audit(ctx, "apikey_create", "apikey", cur.lastrowid, f"{name} → {user['username']}")
    return 201, {"id": cur.lastrowid, "key": key}


@route("DELETE", r"/api/admin/apikeys/(?P<id>\d+)", roles=("admin",))
def keys_revoke(ctx):
    ctx.conn.execute("UPDATE api_keys SET active=0 WHERE id=?", (int(ctx.params["id"]),))
    audit(ctx, "apikey_revoke", "apikey", int(ctx.params["id"]))
    return 200, {"ok": True}


@route("GET", r"/api/admin/webhooks", roles=("admin",))
def hooks_list(ctx):
    return 200, [dict(r) for r in ctx.conn.execute("SELECT id,url,events,active,created_at FROM webhooks ORDER BY id DESC")]


@route("POST", r"/api/admin/webhooks", roles=("admin",))
def hooks_create(ctx):
    url = f_text(ctx.body, "url", 500, True, "الرابط")
    pu = urllib.parse.urlparse(url)
    if pu.scheme not in ("http", "https") or not pu.hostname:
        raise ApiError(400, "الرابط يجب أن يبدأ بـ http:// أو https://")
    events = ctx.body.get("events") or list(WEBHOOK_EVENTS)
    if not isinstance(events, list) or not set(events) <= set(WEBHOOK_EVENTS):
        raise ApiError(400, "أحداث غير مدعومة")
    secret = secrets.token_hex(24)
    cur = ctx.conn.execute("INSERT INTO webhooks(url,events,secret,created_at) VALUES(?,?,?,?)",
                           (url, ",".join(events), secret, now()))
    audit(ctx, "webhook_create", "webhook", cur.lastrowid, url)
    return 201, {"id": cur.lastrowid, "secret": secret}


@route("DELETE", r"/api/admin/webhooks/(?P<id>\d+)", roles=("admin",))
def hooks_delete(ctx):
    ctx.conn.execute("DELETE FROM webhooks WHERE id=?", (int(ctx.params["id"]),))
    audit(ctx, "webhook_delete", "webhook", int(ctx.params["id"]))
    return 200, {"ok": True}


@route("GET", r"/api/health", auth=False)
def health(ctx):
    return 200, {"status": "ok"}


# ───────────────────────── خادم HTTP ─────────────────────────
SEC_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    server_version = "ACP"
    sys_version = ""

    def log_message(self, fmt, *args):
        if os.environ.get("ACP_VERBOSE"):
            super().log_message(fmt, *args)

    def send(self, status, body, headers):
        self.send_response(status)
        for k, v in {**SEC_HEADERS, **headers}.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status, obj):
        self.send(status, json.dumps(obj, ensure_ascii=False).encode(), {"Content-Type": "application/json; charset=utf-8"})

    def authenticate(self, conn):
        key = self.headers.get("X-API-Key")
        if key:
            r = conn.execute("SELECT u.*, k.name AS key_name FROM api_keys k JOIN users u ON u.id=k.user_id "
                             "WHERE k.key_hash=? AND k.active=1 AND u.active=1", (sha(key),)).fetchone()
            if r:
                u = dict(r)
                u["via"] = "API:" + u.pop("key_name")
                return u
            return None
        a = self.headers.get("Authorization") or ""
        if a.startswith("Bearer "):
            r = conn.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                             "WHERE s.token_hash=? AND s.expires_at>? AND u.active=1", (sha(a[7:]), now())).fetchone()
            return dict(r) if r else None
        return None

    def serve_static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC, rel))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            return self.send_json(404, {"error": "غير موجود"})
        with open(full, "rb") as f:
            data = f.read()
        self.send(200, data, {"Content-Type": MIME.get(os.path.splitext(full)[1], "application/octet-stream"),
                              "Cache-Control": "no-cache"})

    def handle_any(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            if self.command in ("GET", "HEAD"):
                return self.serve_static(path)
            return self.send_json(405, {"error": "الطريقة غير مسموحة"})
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        conn = connect()
        try:
            for method, rx, fn, auth, roles in ROUTES:
                m = rx.fullmatch(path)
                if not m or method != self.command:
                    continue
                user = self.authenticate(conn) if (auth or self.headers.get("Authorization")) else None
                if auth and not user:
                    return self.send_json(401, {"error": "يلزم تسجيل الدخول"})
                if user and user["must_change"] and not user.get("via") and path not in ("/api/me", "/api/me/password", "/api/logout"):
                    return self.send_json(403, {"error": "يجب تغيير كلمة المرور أولًا", "must_change": True})
                if roles and user["role"] not in roles:
                    return self.send_json(403, {"error": "لا تملك الصلاحية"})
                ctx = Ctx(self, conn, user, m.groupdict(), query)
                res = fn(ctx)
                if isinstance(res, Raw):
                    return self.send(res.status, res.body, res.headers)
                return self.send_json(*res)
            return self.send_json(404, {"error": "المسار غير موجود"})
        except ApiError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "خطأ داخلي في الخادم"})
        finally:
            conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = handle_any


def main():
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    init_db()
    host = os.environ.get("ACP_HOST", "127.0.0.1")
    port = int(os.environ.get("ACP_PORT") or os.environ.get("PORT") or 8080)
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"منصة الاتصالات الإدارية تعمل على http://{host}:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
