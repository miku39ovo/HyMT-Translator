# -*- coding: utf-8 -*-
"""用已在运行的推理服务验证多种语言方向的提示词路由与输出质量。"""
import sys, time
sys.path.insert(0, r"D:/HyMT-Translator/app")
import config, engine

cfg = config.load()
eng = engine.get_engine(cfg)
print("engine:", eng.state)

cases = [
    ("zh", "fr", "混元翻译模型支持中文与其他三十多种语言之间的互译，适合处理论文和字幕。"),
    ("fr", "zh", "Ce modèle de traduction fonctionne entièrement en local, sans envoyer vos documents sur Internet."),
    ("de", "ja", "Das Modell läuft lokal auf einer einzelnen Grafikkarte und verarbeitet auch lange Dokumente."),
    ("ja", "en", "このモデルはローカルで動作するため、機密文書でも安心して翻訳できます。"),
    ("en", "zh-Hant", "Subtitles, papers, and contracts can all be translated while keeping the original layout."),
    ("en", "ko", "Batch translation keeps the original formatting of Word, PDF, and SRT files."),
    ("auto", "zh", "This sentence has no explicit source language, so it must be detected automatically."),
]
for src, tgt, text in cases:
    t0 = time.time()
    r = eng.translate(text, src, tgt, None, None, None)
    dt = time.time() - t0
    print("\n[%s->%s] %.1fs  %.1f tok/s" % (src, tgt, dt, eng.last_tps))
    print("  原文:", text)
    print("  译文:", r["text"].strip())
