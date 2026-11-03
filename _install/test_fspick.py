# -*- coding: utf-8 -*-
"""fspick 回归测试：PIDL 路径互转 + 对话框能弹出并返回取消。"""
import ctypes
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, r"D:\HyMT-Translator\app")
import fspick  # noqa: E402

LOG = open(r"D:\HyMT-Translator\logs\pick_dbg.txt", "w", encoding="utf-8")


def log(*a):
    print(*a, file=LOG, flush=True)


u = ctypes.WinDLL("user32", use_last_error=True)
u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def dialogs():
    me = ctypes.windll.kernel32.GetCurrentProcessId()
    out = []

    def cb(hwnd, _lp):
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, cls, 64)
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if cls.value == "#32770" and pid.value == me and u.IsWindowVisible(hwnd):
            out.append(int(hwnd or 0))
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


# --- 1. PIDL 互转 ---
fspick._ole32.CoInitializeEx(None, 2)
for p in (r"D:\HyMT-Translator\output", r"C:\Windows"):
    pidl = fspick.path_to_pidl(p)
    back = fspick._pidl_to_path(pidl) if pidl else ""
    log("roundtrip", p, "->", bool(pidl), back, "OK" if back.lower() == p.lower() else "MISMATCH")
    if pidl:
        fspick._ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
fspick._ole32.CoUninitialize()


# --- 2. 弹窗后 ESC 取消 ---
def driver():
    for _ in range(25):
        time.sleep(0.2)
        w = dialogs()
        if w:
            log("dialog appeared:", w)
            u.keybd_event(0x1B, 0, 0, None)
            u.keybd_event(0x1B, 0, 2, None)
            log("esc sent")
            return
    log("no dialog appeared")


threading.Thread(target=driver, daemon=True).start()
t0 = time.time()
r = fspick.pick_folder(initial=r"D:\HyMT-Translator\output", title="选择译文输出目录")
log("pick result:", r, "elapsed %.2fs" % (time.time() - t0))
LOG.close()
