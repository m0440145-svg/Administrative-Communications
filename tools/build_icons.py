"""يولّد static/icons.js من أيقونات Tabler Icons (MIT — Paweł Kuna) المختارة فقط.   py -3 tools/build_icons.py
لا يُحمَّل شيء وقت التشغيل؛ الملف الناتج ثابت ومحلي. أضف اسم الأيقونة إلى NAMES ثم أعد التشغيل."""
import re, sys, urllib.request, os
NAMES = ["home", "mail", "chart-bar", "building", "folders", "settings", "user", "menu-2", "bell", "maximize", "arrows-vertical", "bug", "lock", "key",
         "info-circle", "eye-off", "eye", "keyboard", "power", "inbox", "send", "search", "signature", "camera", "printer", "tag", "file-text", "folder",
         "arrow-back-up", "arrow-forward-up", "download", "clock", "alarm", "paperclip", "device-floppy", "world", "qrcode", "scan", "check", "x", "plus", "shield-check", "route"]
BASE = "https://raw.githubusercontent.com/tabler/tabler-icons/main/icons/outline/%s.svg"
out = {}
for n in NAMES:
    svg = urllib.request.urlopen(BASE % n, timeout=30).read().decode()
    inner = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)
    inner = re.sub(r'<path stroke="none" d="M0 0h24v24H0z" fill="none"\s*/>', "", inner)  # إطار فارغ لا حاجة له
    out[n] = re.sub(r"\s+", " ", inner).strip()
js = ["// مولَّد آليًا من Tabler Icons (MIT, © Paweł Kuna) — انظر THIRD_PARTY_NOTICES.md. لا تعدّله يدويًا: استخدم tools/build_icons.py", "const TABLER_ICONS = {"]
js += [f'  "{k}": {v!r},'.replace("\'", "'") for k, v in out.items()]
js.append("};")
open(os.path.join(os.path.dirname(__file__), "..", "static", "icons.js"), "w", encoding="utf-8", newline="").write("\n".join(js) + "\n")
print(len(out), "icons")
