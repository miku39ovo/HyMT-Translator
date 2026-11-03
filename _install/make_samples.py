# -*- coding: utf-8 -*-
"""生成测试样例文件（txt / srt / lrc / docx / pdf）"""
import os
from pathlib import Path

import docx
import fitz
from docx.shared import Pt

OUT = Path(r"D:\HyMT-Translator\_install\samples")
OUT.mkdir(parents=True, exist_ok=True)

# ---------- TXT ----------
txt_en = """Machine translation has changed dramatically in the past decade. Early systems relied on
hand-crafted rules and statistical alignments, which struggled with long-range dependencies and
idiomatic expressions.

Recent neural approaches, especially decoder-only language models, treat translation as a
conditional generation problem. They can exploit context, terminology constraints, and even
document-level cues to produce more fluent output.

However, these models still make mistakes: they may omit clauses, mistranslate technical terms,
or drift in style over long documents. Careful evaluation remains essential.
"""
txt_zh = """机器翻译在过去十年发生了巨大变化。早期的系统依赖手工规则和统计对齐，在长距离依赖和习惯表达上
表现不佳。

近年的神经网络方法，尤其是仅解码器语言模型，把翻译视为条件生成问题。它们可以利用上下文、术语约束，
甚至文档级线索，产出更流畅的结果。

不过这些模型依然会犯错：漏译从句、误译术语，或在长文档中风格漂移。细致的评估依然必不可少。
"""
(OUT / "sample_en.txt").write_text(txt_en, encoding="utf-8")
(OUT / "sample_zh.txt").write_text(txt_zh, encoding="utf-8")

# ---------- SRT ----------
srt = """1
00:00:01,000 --> 00:00:04,200
Good morning everyone, thanks for joining.

2
00:00:04,300 --> 00:00:08,500
Today we will look at how a translation model
handles long technical documents.

3
00:00:08,600 --> 00:00:12,000
The first problem is segmentation.

4
00:00:12,100 --> 00:00:16,400
A single sentence may span several clauses,
and each clause carries its own terminology.

5
00:00:16,500 --> 00:00:20,000
Let us start with a simple example.
"""
(OUT / "sample_en.srt").write_text(srt, encoding="utf-8")

# ---------- LRC ----------
lrc = """[ti:Yesterday Once More]
[ar:Carpenters]
[al:Now & Then]
[by:test]
[offset:0]
[00:00.00]When I was young I'd listen to the radio
[00:04.50]Waiting for my favorite songs
[00:08.20]When they played I'd sing along
[00:12.00]It made me smile
[00:16.30]Those were such happy times
[00:20.10]And not so long ago
"""
(OUT / "sample_en.lrc").write_text(lrc, encoding="utf-8")

# ---------- DOCX ----------
d = docx.Document()
h = d.add_heading("Attention Is All You Need — 摘要节选", level=1)
p = d.add_paragraph()
r = p.add_run("The dominant sequence transduction models are based on complex recurrent or "
              "convolutional neural networks that include an encoder and a decoder.")
p2 = d.add_paragraph()
p2.add_run("We propose a new simple network architecture, the ")
b = p2.add_run("Transformer")
b.bold = True
p2.add_run(", based solely on attention mechanisms, dispensing with recurrence and convolutions entirely.")
d.add_paragraph("Experiments on two machine translation tasks show these models to be superior in quality "
                "while being more parallelizable and requiring significantly less time to train.")
d.add_heading("主要贡献", level=2)
for t in ["提出完全基于注意力的序列转换模型。",
          "在 WMT 2014 英德任务上取得 28.4 BLEU，超过此前最佳结果 2 BLEU 以上。",
          "训练成本显著降低，八块 GPU 训练 3.5 天即可达到当时最优。"]:
    d.add_paragraph(t, style="List Bullet")

tbl = d.add_table(rows=3, cols=2)
tbl.style = "Table Grid"
data = [("Model", "BLEU"), ("Transformer (base)", "27.3"), ("Transformer (big)", "28.4")]
for i, row in enumerate(data):
    for j, v in enumerate(row):
        cell = tbl.cell(i, j)
        cell.paragraphs[0].add_run(v)
d.save(str(OUT / "sample_mixed.docx"))

# ---------- PDF（双栏，模拟论文） ----------
pdf = fitz.open()
page = pdf.new_page(width=595, height=842)
left = [
    ("Abstract", 18),
    ("Neural machine translation (NMT) has achieved remarkable progress in recent years. "
     "However, translating long documents remains challenging because models must maintain "
     "consistency across sentences and paragraphs.", 0),
    ("In this paper we study document-level translation with a decoder-only architecture, "
     "and evaluate three strategies for injecting context: sentence-level batching, "
     "sliding windows, and retrieval of similar segments.", 0),
]
right = [
    ("Experiments", 12),
    ("We evaluate on three language pairs: English-Chinese, English-Japanese and "
     "Chinese-Japanese. All models are quantized to four bits and served locally.", 0),
    ("Results indicate that a sliding window of two preceding sentences improves "
     "terminology consistency by 6.8 percent without measurable latency cost.", 0),
]
y = 70
for text, size in left:
    if size:
        page.insert_text((60, y), text, fontname="helv", fontsize=size)
        y += 26
    else:
        rect = fitz.Rect(60, y, 285, y + 150)
        page.insert_textbox(rect, text, fontname="helv", fontsize=9.5, lineheight=1.5)
        y += 150
y = 70
for text, size in right:
    if size:
        page.insert_text((320, y), text, fontname="helv", fontsize=size)
        y += 26
    else:
        rect = fitz.Rect(320, y, 540, y + 150)
        page.insert_textbox(rect, text, fontname="helv", fontsize=9.5, lineheight=1.5)
        y += 150
pdf.save(str(OUT / "sample_paper.pdf"))
pdf.close()

for f in sorted(OUT.iterdir()):
    print(f.name, f.stat().st_size, "bytes")
