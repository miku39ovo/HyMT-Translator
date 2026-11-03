# -*- coding: utf-8 -*-
"""单独测 pick_folder：只看它是"弹出来了（阻塞）"还是"立刻返回 canceled"。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import fspick  # noqa: E402

t0 = time.time()
try:
    r = fspick.pick_folder(initial=r"D:\HyMT-Translator\_install\scan_demo",
                           title="HyMT 文件夹对话框自测")
except Exception as e:
    print("EXC", type(e).__name__, e, flush=True)
    r = {}
print("elapsed=%.2fs" % (time.time() - t0), r, flush=True)
