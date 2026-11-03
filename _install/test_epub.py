# -*- coding: utf-8 -*-
"""EPUB 管线自测：四种输出模式、包结构合法性、停止后保存部分译文。"""
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, r"D:\HyMT-Translator\app")
import config          # noqa: E402
import pipeline        # noqa: E402

SRC = Path(r"D:\HyMT-Translator\示例文件\sample_book.epub")
TMP = Path(r"D:\HyMT-Translator\output\_epubtest")


class FakeEngine:
    """逐段加前缀，模拟真实模型按段返回译文。"""

    def __init__(self, fail_after=None):
        self.last_tps = 12.0
        self.calls = 0
        self.fail_after = fail_after

    def translate(self, text, src, tgt, glossary=None, context=None, cancel=None, retry=None):
        import re
        self.calls += 1
        parts = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        return {"text": "\n\n".join("【译】" + p.strip() for p in parts)}


def settings(mode, sub, chunk=1200):
    s = dict(config.DEFAULTS)
    s.update({"src_lang": "en", "tgt_lang": "zh", "out_mode": mode,
              "out_dir_mode": "app", "out_dir": str(TMP / sub),
              "use_context": False, "glossary": [], "chunk_chars": chunk})
    return s


def blocks(path):
    return pipeline.epub_text(path).split("\n")


def check_zip(path):
    with zipfile.ZipFile(path) as z:
        info = z.infolist()
        first = info[0]
        assert first.filename == "mimetype", "mimetype 必须是第一条"
        assert first.compress_type == zipfile.ZIP_STORED, "mimetype 必须不压缩"
        assert z.read("mimetype") == b"application/epub+zip"
        bad = z.testzip()
        assert bad is None, f"压缩包损坏：{bad}"
        assert "OEBPS/style.css" in z.namelist(), "样式表应原样保留"
        return z.namelist()


print("=== 1. 仅译文（none）===")
eng = FakeEngine()
outs = pipeline.process(SRC, settings("none", "none"), eng, lambda **k: None, lambda: False)
assert len(outs) == 1, outs
names = check_zip(outs[0])
txt = blocks(outs[0])
assert not any(t.startswith("【译】") is False and t.startswith("Chapter 1") for t in txt), "原文没被替换"
assert any(t.startswith("【译】Chapter 1") for t in txt), txt[:3]
assert not any("llama-server" in t for t in txt), "pre/code 不应被翻译或删除"
print(f"  文件: {outs[0].name}  条目 {len(names)}  段落 {len(txt)}")
print("  首段:", txt[0])

print("=== 2. 原文+译文（interleave）===")
eng = FakeEngine()
outs = pipeline.process(SRC, settings("interleave", "inter"), eng, lambda **k: None, lambda: False)
check_zip(outs[0])
raw = None
with zipfile.ZipFile(outs[0]) as z:
    raw = z.read("OEBPS/text/ch1.xhtml").decode("utf-8")
assert "【译】Chapter 1" in raw
assert "Chapter 1: Why Run a Model Locally" in raw, "对照模式必须保留原文"
assert raw.count("【译】") >= 6, raw.count("【译】")
assert "<pre>" in raw and "llama-server" in raw, "pre 应保持原样"
print(f"  文件: {outs[0].name}  译文段 {raw.count('【译】')} 处")

print("=== 3. 双文件（separate）===")
eng = FakeEngine()
outs = pipeline.process(SRC, settings("separate", "sep"), eng, lambda **k: None, lambda: False)
assert len(outs) == 2, outs
for p in outs:
    check_zip(p)
t1 = blocks(outs[0])
t2 = blocks(outs[1])
assert any(x.startswith("【译】Chapter") for x in t1)
assert "Chapter 1: Why Run a Model Locally" in t2, "对照版应含原文"
print("  译文版首段:", t1[0])
print("  对照版前两段:", t2[0], "|", t2[1])

print("=== 4. 停止：保存已翻部分 ===")
calls = {"n": 0}


def cancel_after_two():
    return calls["n"] >= 2


class CountingEngine(FakeEngine):
    def translate(self, *a, **k):
        calls["n"] += 1
        return super().translate(*a, **k)


try:
    pipeline.process(SRC, settings("none", "stop", chunk=120), CountingEngine(),
                     lambda **k: None, cancel_after_two)
    print("  !! 未按预期停止")
except pipeline.Cancelled as e:
    outs = [Path(p) for p in e.outputs]
    assert outs, "停止时应带回已保存的文件"
    p = outs[0]
    assert p.exists() and p.stat().st_size > 0, "部分译文文件应存在"
    check_zip(p)
    txt = blocks(p)
    done = [t for t in txt if t.startswith("【译】")]
    left = [t for t in txt if not t.startswith("【译】")]
    print(f"  已翻译 {len(done)} 段 / 保留原文 {len(left)} 段")
    print("  产物:", p.name, p.stat().st_size, "字节")
    assert done, "应该有已翻译内容"
    assert left, "未翻到的段落应保留原文"

print("\n全部通过")
