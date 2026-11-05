"""مولّدات QR وCode128 وXLSX المكتوبة بلا اعتماديات: اختبارات بنيوية دائمة + اختبارات قراءة فعلية بقارئ/مكتبة إن توفرت
(zxing-cpp وpillow وopenpyxl)، وتُتخطى دونها.   python -m unittest tests.test_codecs"""
import io
import os
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ext_docs as d  # noqa: E402

try:
    import zxingcpp
    from PIL import Image
except ImportError:
    zxingcpp = Image = None
try:
    import openpyxl
except ImportError:
    openpyxl = None

TEXTS = ["A", "ACP|EXT-IN-2026-00001|2026-11-05|3", "x" * 50, "مراسلة سرية للغاية " * 3,
         "otpauth://totp/acp:mnag?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=acp&digits=6&period=30", "y" * 110, "z" * 150, "ع" * 90, "w" * 200]


def render(g, scale=6, quiet=4):
    n = len(g)
    img = Image.new("L", ((n + 2 * quiet) * scale,) * 2, 255)
    px = img.load()
    for y in range(n):
        for x in range(n):
            if g[y][x]:
                for dy in range(scale):
                    for dx in range(scale):
                        px[(x + quiet) * scale + dx, (y + quiet) * scale + dy] = 0
    return img


class QrStructure(unittest.TestCase):
    def test_versions_and_finders(self):
        self.assertEqual(len(d.qr_matrix("A")), 21)                       # الإصدار 1
        self.assertEqual(len(d.qr_matrix("x" * 50)), 33)                  # الإصدار 4
        self.assertEqual(len(d.qr_matrix("y" * 200)), 57)                 # الإصدار 10
        with self.assertRaises(ValueError):
            d.qr_matrix("z" * 400)                                          # أكبر من السعة المدعومة
        g = d.qr_matrix("ACP|EXT-IN-2026-00001")
        n = len(g)
        for ox, oy in ((0, 0), (n - 7, 0), (0, n - 7)):                   # مكتشفات الموضع الثلاثة 7×7
            for i in range(7):
                self.assertTrue(g[oy][ox + i] and g[oy + 6][ox + i] and g[oy + i][ox] and g[oy + i][ox + 6])
            self.assertFalse(g[oy + 1][ox + 1] or g[oy + 5][ox + 5])
            self.assertTrue(g[oy + 3][ox + 3])
        self.assertTrue(g[n - 8][8])                                       # وحدة الظلام الثابتة

    def test_svg_is_valid_xml(self):
        root = ET.fromstring(d.qr_svg("ACP|EXT-IN-2026-00001|2026-11-05|3"))
        self.assertTrue(root.tag.endswith("svg"))
        self.assertIn("viewBox", root.attrib)

    @unittest.skipIf(zxingcpp is None, "zxing-cpp/pillow غير مثبّتين")
    def test_decodes_with_real_scanner(self):
        bad = []
        for t in TEXTS:
            for mask in [None] + list(range(8)):                           # كل الأقنعة تُقرأ، لا القناع التلقائي فقط
                res = zxingcpp.read_barcodes(render(d.qr_matrix(t, mask)))
                if not (res and res[0].text == t):
                    bad.append((t[:20], mask))
        self.assertEqual(bad, [])


class Code128(unittest.TestCase):
    def test_structure_and_checksum(self):
        mods = d.code128_modules("EXT-IN-2026-00001")
        self.assertTrue(mods.endswith("2331112"))                          # نمط الإيقاف
        self.assertTrue(mods.startswith(d.C128[104]))                      # بداية المجموعة B
        self.assertEqual(sum(int(c) for c in d.C128[104]), 11)
        self.assertNotEqual(d.code128_modules("ACP-1"), d.code128_modules("ACP-2"))
        with self.assertRaises(ValueError):
            d.code128_modules("عربي")
        ET.fromstring(d.code128_svg("ACP-1"))

    @unittest.skipIf(zxingcpp is None, "zxing-cpp/pillow غير مثبّتين")
    def test_decodes_with_real_scanner(self):
        for t in ("EXT-IN-2026-00001", "INT-OUT-2026-12345", "ACP-77"):
            mods = "".join(("1" if i % 2 == 0 else "0") * int(w) for i, w in enumerate(d.code128_modules(t)))
            unit, q, h = 3, 10, 80
            img = Image.new("L", ((len(mods) + 2 * q) * unit, h), 255)
            px = img.load()
            for i, m in enumerate(mods):
                if m == "1":
                    for dx in range(unit):
                        for y in range(h):
                            px[(i + q) * unit + dx, y] = 0
            res = zxingcpp.read_barcodes(img)
            self.assertTrue(res and res[0].text == t, t)


class Xlsx(unittest.TestCase):
    DATA = [("ملخص", [["a", "b"], ["نص عربي", 12.5], ["=SUM(1,2)", None], ['<tag> & "q"', True]]), ("ثاني", [[1, 2, 3]])]

    def test_package_structure(self):
        with zipfile.ZipFile(io.BytesIO(d.make_xlsx(self.DATA))) as z:
            names = z.namelist()
            for part in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml"):
                self.assertIn(part, names)
            for n in names:
                ET.fromstring(z.read(n))                                   # كل جزء XML صالح
            s1 = z.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn('rightToLeft="1"', s1)
            self.assertIn("&lt;tag&gt; &amp; &quot;q&quot;".replace("&quot;", '"'), s1.replace("&quot;", '"'))
            self.assertIn('t="inlineStr"><is><t xml:space="preserve">=SUM(1,2)', s1)  # الصيغة تبقى نصًا (لا حقن صيغ)

    @unittest.skipIf(openpyxl is None, "openpyxl غير مثبّتة")
    def test_opens_in_openpyxl(self):
        wb = openpyxl.load_workbook(io.BytesIO(d.make_xlsx(self.DATA)))
        ws = wb["ملخص"]
        rows = [[c.value for c in r] for r in ws.iter_rows()]
        self.assertEqual(rows[1], ["نص عربي", 12.5])
        self.assertEqual(ws["A3"].data_type, "s")
        self.assertEqual(rows[3], ['<tag> & "q"', 1])
        self.assertEqual(wb["ثاني"]["C1"].value, 3)
        self.assertTrue(ws.sheet_view.rightToLeft)


if __name__ == "__main__":
    unittest.main()
