# -*- coding: utf-8 -*-
"""验证 reveal：文件被选中、资源管理器窗口被置顶，hold 结束后自动取消置顶。"""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import shellopen  # noqa: E402

TARGET = r"D:\HyMT-Translator\output\2026-11-02\long_article.对照.en.txt"
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
WS_EX_TOPMOST = 0x00000008
GWL_EXSTYLE = -20


def topmost(hwnd):
    return bool(user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE) & WS_EX_TOPMOST)


def pick():
    for h, t in shellopen._explorer_windows():
        if "2026-11-02" in t:
            return h, t
    return 0, ""


res = shellopen.reveal(TARGET, hold_ms=3500)
print("reveal →", res)
time.sleep(0.4)
h, t = pick()
live = topmost(h) if h else None
print("window:", h, t, "| 置顶中:", live)

time.sleep(4.2)
after = topmost(h) if h else None
print("hold 结束后置顶:", after)

ok = res.get("hinted") and live is True and after is False
print("RESULT:", "PASS" if ok else "FAIL")
