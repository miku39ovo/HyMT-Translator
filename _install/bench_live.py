# -*- coding: utf-8 -*-
"""通过应用自身的引擎接口实测翻译速度（CUDA vs 之前 Vulkan 的 12 tok/s）"""
import sys, time
sys.path.insert(0, r"D:\HyMT-Translator\app")
import config, engine

cfg = config.load()
eng = engine.get_engine(cfg)
print("backend:", eng.backend(), "| state:", eng.state)

cases = [
    ("en", "zh", "Local inference trades a little speed for a lot of privacy. "
                 "Your documents never leave the machine, and there is no usage quota."),
    ("zh", "ja", "混元翻译模型支持中文与其他三十多种语言之间的互译，适合处理论文和字幕。"),
    ("ja", "en", "このモデルはローカルで動作するため、機密文書でも安心して翻訳できます。"),
]
for src, tgt, text in cases:
    t0 = time.time()
    r = eng.translate(text, src, tgt, None, None, None)
    dt = time.time() - t0
    print("\n[%s->%s] %.2fs  %.1f tok/s" % (src, tgt, dt, eng.last_tps))
    print("  ", r["text"].strip().replace("\n", " ")[:120])

# 长一点的段落看稳定速度
long_text = (
    "Machine translation has changed a great deal over the past decade. "
    "Early systems relied on hand written rules and statistical alignments, "
    "which produced fluent output only for closely related language pairs. "
    "Neural approaches replaced that pipeline with a single sequence to sequence model, "
    "and later work reduced the whole task to conditional generation with a decoder only architecture. "
    "The result is a system that needs far less feature engineering and generalises much better. "
) * 2
t0 = time.time()
r = eng.translate(long_text, "en", "zh", None, None, None)
dt = time.time() - t0
n_in = len(long_text)
n_out = len(r["text"])
print("\n[长段 en->zh] %.2fs  %.1f tok/s  输入 %d 字符 -> 输出 %d 字" % (dt, eng.last_tps, n_in, n_out))
