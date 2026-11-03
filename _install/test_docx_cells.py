# -*- coding: utf-8 -*-
"""校验 docx 表格单元格（含合并单元格）不会被重复处理。"""
import sys
sys.path.insert(0, r"D:/HyMT-Translator/app")
from pathlib import Path
import docx
import config, pipeline

# 1. 造一个带合并单元格的样张
d = docx.Document()
d.add_heading("表格合并测试", 1)
d.add_paragraph("正文第一段：这是用来测试段落配对是否正确的句子。")
t = d.add_table(rows=3, cols=3)
t.style = "Table Grid"
vals = [["表头A", "表头B", "表头C"],
        ["第一行", "第一行内容", "第一行备注"],
        ["第二行", "第二行内容", "第二行备注"]]
for r, row in enumerate(vals):
    for c, v in enumerate(row):
        t.cell(r, c).text = v
t.cell(0, 0).merge(t.cell(0, 1))
t.cell(1, 2).merge(t.cell(2, 2))
d.add_paragraph("正文结束段。")
p = Path(r"D:/HyMT-Translator/uploads/_test/merged.docx")
p.parent.mkdir(parents=True, exist_ok=True)
d.save(str(p))

# 2. 统计 slots（合并单元格不应重复）
doc = docx.Document(str(p))
slots = pipeline._docx_paragraph_slots(doc)
texts = [s.text.strip() for s in slots]
print("slots:", len(slots), texts)
dup = [x for x in set(texts) if x and texts.count(x) > 1]
print("重复段:", dup)
assert not dup, "存在重复段落，去重失败"

# 3. 假引擎：逐段加前缀，保持空行结构（模拟真实模型）
class FakeEngine:
    last_tps = 12.0
    def translate(self, text, src, tgt, glossary=None, context=None, cancel=None, retry=None):
        paras = re.split(r"\n\s*\n", text)
        return {"text": "\n\n".join("[%s]%s" % (tgt, x.strip()) for x in paras if x.strip())}
import re

settings = dict(config.DEFAULTS)
settings.update({"src_lang": "zh", "tgt_lang": "en", "out_mode": "interleave",
                 "out_dir_mode": "app", "use_context": False, "glossary": []})
outs = pipeline.do_docx(p, settings, FakeEngine(), lambda **kw: None, lambda: False)
print("输出:", outs)

out = docx.Document(str(outs[0]))
body = [x.text.strip() for x in out.paragraphs if x.text.strip()]
print("正文段落:", body)

# 表格：每格应为 [原文, 译文] 交替
print("表格行数:", len(out.tables[0].rows))
bad = 0
seen = set()
for row in out.tables[0].rows:
    for cell in row.cells:
        if id(cell._tc) in seen:
            continue
        seen.add(id(cell._tc))
        ps = [x.text.strip() for x in cell.paragraphs if x.text.strip()]
        print("   cell:", ps)
        if len(ps) % 2 or any(not ps[i].startswith("[en]") for i in range(1, len(ps), 2)):
            bad += 1
print("异常单元格:", bad)
assert bad == 0
print("PASS")
