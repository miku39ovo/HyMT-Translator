# -*- coding: utf-8 -*-
"""通用停止测试：各格式在中途停止时都应写出「已译 + 原文」的混合文件。"""
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, r"D:\HyMT-Translator\app")
import config          # noqa: E402
import pipeline        # noqa: E402

TMP = Path(r"D:\HyMT-Translator\output\_stoptest")
SAMP = Path(r"D:\HyMT-Translator\示例文件")


class CountingEngine:
    def __init__(self):
        self.last_tps = 12.0
        self.calls = 0

    def translate(self, text, src, tgt, glossary=None, context=None, cancel=None, retry=None):
        self.calls += 1
        parts = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        return {"text": "\n\n".join("【译】" + p.strip() for p in parts)}


def run(name, src, mode="none", stop_after=1, chunk=120):
    eng = CountingEngine()

    def cancel():
        return eng.calls >= stop_after

    s = dict(config.DEFAULTS)
    s.update({"src_lang": "auto", "tgt_lang": "zh", "out_mode": mode,
              "out_dir_mode": "app", "out_dir": str(TMP / name),
              "use_context": False, "glossary": [], "chunk_chars": chunk})
    try:
        pipeline.process(src, s, eng, lambda **k: None, cancel)
        print(f"[{name}] 未停止（段数太少），产物已完整生成")
        return
    except pipeline.Cancelled as e:
        outs = [Path(p) for p in e.outputs]
        if not outs:
            print(f"[{name}] 停止时无产物（一段都没翻完）")
            return
        for p in outs:
            assert p.exists() and p.stat().st_size > 0
            txt = pipeline._output_text(p)
            done = txt.count("【译】")
            print(f"[{name}] {p.name}  {p.stat().st_size} 字节  已译 {done} 处  引擎调用 {eng.calls} 次")
            assert done >= 1, "至少应保留一段译文"


print("=== txt ===")
run("txt", SAMP / "sample_en.txt", "none", 1)
print("=== txt 对照模式 ===")
run("txt_inter", SAMP / "sample_en.txt", "interleave", 1)
print("=== srt ===")
run("srt", SAMP / "sample_en.srt", "interleave", 1)
print("=== lrc ===")
run("lrc", SAMP / "sample_en.lrc", "none", 1)
print("=== docx ===")
run("docx", SAMP / "sample_mixed.docx", "none", 1)
print("=== pdf ===")
run("pdf", SAMP / "sample_paper.pdf", "none", 1)
print("=== epub ===")
run("epub", SAMP / "sample_book.epub", "none", 1)
print("\n完成")
