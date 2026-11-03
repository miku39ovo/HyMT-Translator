# -*- coding: utf-8 -*-
"""通过 HTTP 触发系统文件夹对话框（自动 ESC 关闭），验证路由与阻塞不串台。"""
import ctypes
import json
import sys
import threading
import time
import urllib.request
from ctypes import wintypes

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TITLE = "选择译文输出目录"

u = ctypes.WinDLL("user32", use_last_error=True)
u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

found = []


def find():
    out = []

    def cb(h, l):
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, cls, 64)
        if cls.value != "#32770" or not u.IsWindowVisible(h):
            return True
        b = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(h, b, 256)
        # 系统文件夹框的窗口标题固定是「浏览文件夹」，lpszTitle 只是框里的说明文字
        if b.value.strip() == "浏览文件夹":
            out.append(int(h or 0))
        return True

    u.EnumWindows(PROC(cb), 0)
    return out


def killer():
    for _ in range(60):
        time.sleep(0.25)
        w = find()
        if w:
            found.extend(w)
            time.sleep(1.0)          # 留点时间确认真的是弹出来了
            u.PostMessageW(wintypes.HWND(w[0]), 0x0010, 0, 0)   # WM_CLOSE = 取消
            return


t0 = time.time()
threading.Thread(target=killer, daemon=True).start()
req = urllib.request.Request(
    "http://127.0.0.1:18081/api/pickdir",
    data=json.dumps({"initial": r"D:\HyMT-Translator\output", "title": TITLE}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with OPENER.open(req, timeout=60) as r:
    code = r.status
    d = json.loads(r.read().decode("utf-8"))
dt = time.time() - t0

print("http", code, "| 用时 %.1fs" % dt, "| 找到对话框:", bool(found))
print("result:", d)
ok = code == 200 and found and (d.get("canceled") or d.get("ok"))
print("RESULT:", "PASS" if ok else "FAIL")

# 弹窗期间别的接口仍可用
req2 = urllib.request.Request(
    "http://127.0.0.1:18081/api/checkdir",
    data=json.dumps({"mode": "app", "path": r"D:\HyMT-Translator\output"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with OPENER.open(req2, timeout=10) as r:
    print("并发 checkdir:", json.loads(r.read().decode("utf-8"))["ok"])
