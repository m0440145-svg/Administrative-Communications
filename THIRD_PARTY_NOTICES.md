# إشعارات المكونات الخارجية — Third-Party Notices

منصة وصال لا تعتمد على أي مكتبة خارجية وقت التشغيل. الملفات الثابتة التالية فقط مستخرجة من مشاريع مفتوحة المصدر وتُستضاف محليًا:

## 1) Tabler Icons — أيقونات SVG مختارة (43 أيقونة) في `static/icons.js`
الملف مولَّد بالأداة `tools/build_icons.py` من https://github.com/tabler/tabler-icons — ترخيص MIT:

```
MIT License

Copyright (c) 2020-2026 Paweł Kuna

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 2) خط Tajawal — `static/fonts/`
Copyright (c) 2017 Boutros International — SIL Open Font License 1.1 (https://openfontlicense.org). انظر `static/fonts/LICENSE.txt`.

## 3) Tabler (واجهة المستخدم)
تصميم بعض المكونات (شارات الحالة، حالات الفراغ، خطوط الاتجاه المصغّرة…) **مستلهم** من لغة تصميم Tabler (MIT، © The Tabler Authors) ومكتوب من جديد في `static/ui.css` دون نسخ ملفات Tabler CSS/JS. لم يُضمَّن Bootstrap ولا أي ملف من `@tabler/core`.
