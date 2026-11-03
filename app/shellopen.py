# -*- coding: utf-8 -*-
"""系统文件操作：打开文件 / 在资源管理器中定位并高亮，且把窗口提到最前。

Windows 下浏览器是前台窗口，直接在后台进程里 SetForegroundWindow 会被前台锁
挡住，所以这里用 AttachThreadInput 把当前线程挂到目标窗口线程上，再配合
HWND_TOPMOST 保证文件夹窗口一定盖在浏览器上面。
"""
import os
import subprocess
import threading
import time
from pathlib import Path

IS_WIN = os.name == "nt"

# ---------------- Windows 原生调用 ----------------
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    _user32.EnumWindows.argtypes = [_ENUMPROC, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClassNameW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _user32.AttachThreadInput.restype = wintypes.BOOL
    _user32.BringWindowToTop.argtypes = [wintypes.HWND]
    _user32.BringWindowToTop.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.SetFocus.argtypes = [wintypes.HWND]
    _user32.SetFocus.restype = wintypes.HWND

    _SWP_FLAGS = 0x0001 | 0x0002 | 0x0040      # NOSIZE | NOMOVE | SHOWWINDOW
    _HWND_TOPMOST = -1
    _HWND_NOTOPMOST = -2
    _SW_RESTORE = 9

    def _as_int(v):
        """回调里的句柄可能是 int、None 或 c_void_p，统一成整数。"""
        if isinstance(v, int):
            return v
        return int(getattr(v, "value", 0) or 0)

    def _explorer_windows():
        """枚举所有资源管理器（CabinetWClass）窗口 → [(hwnd, 标题)]。"""
        found = []

        def cb(hwnd, _lp):
            cls = ctypes.create_unicode_buffer(128)
            _user32.GetClassNameW(hwnd, cls, 128)
            if cls.value == "CabinetWClass":
                title = ctypes.create_unicode_buffer(512)
                _user32.GetWindowTextW(hwnd, title, 512)
                found.append((_as_int(hwnd), title.value))
            return True

        _user32.EnumWindows(_ENUMPROC(cb), 0)
        return found

    def _set_pos(hwnd, insert_after):
        return bool(_user32.SetWindowPos(
            wintypes.HWND(hwnd), wintypes.HWND(insert_after),
            0, 0, 0, 0, _SWP_FLAGS))

    def _force_foreground(hwnd):
        """绕过前台锁：把当前线程挂到目标窗口线程上再抢焦点。"""
        target = wintypes.HWND(hwnd)
        fg = _user32.GetForegroundWindow()
        if fg and _as_int(fg) == int(hwnd):
            return
        tid_tg = int(_user32.GetWindowThreadProcessId(target, None) or 0)
        if not tid_tg:
            return
        tid_fg = int(_user32.GetWindowThreadProcessId(fg, None) or 0) if fg else 0
        tid_me = int(_kernel32.GetCurrentThreadId())
        attached = []
        for tid in (tid_fg, tid_me):
            if tid and tid != tid_tg and _user32.AttachThreadInput(tid, tid_tg, True):
                attached.append(tid)
        try:
            _user32.BringWindowToTop(target)
            _user32.SetForegroundWindow(target)
            _user32.SetFocus(target)
        finally:
            for tid in attached:
                _user32.AttachThreadInput(tid, tid_tg, False)

    def _bring_front(hwnd, hold_ms):
        target = wintypes.HWND(hwnd)
        _user32.ShowWindow(target, _SW_RESTORE)
        _set_pos(hwnd, _HWND_TOPMOST)
        _force_foreground(hwnd)
        if hold_ms:
            def _drop():
                try:
                    _set_pos(hwnd, _HWND_NOTOPMOST)
                except Exception:
                    pass
            t = threading.Timer(hold_ms / 1000.0, _drop)
            t.daemon = True
            t.start()

    def _score(title, want):
        """Win11 的标题是「文件夹 - 文件资源管理器」，所以用包含匹配兜底。"""
        t = (title or "").strip().lower()
        if not t:
            return 0
        if t == want:
            return 3
        if want and want in t:
            return 2
        return 1

    def _find_window(folder_name, before, timeout):
        """等资源管理器把窗口开出来；标题命中文件夹名的优先。"""
        want = folder_name.strip().lower()
        deadline = time.time() + timeout
        while time.time() < deadline:
            wins = _explorer_windows()
            new = [(h, t) for h, t in wins if h not in before]
            pool = new or wins
            best, best_score = 0, 0
            for h, t in reversed(pool):
                sc = _score(t, want)
                if sc > best_score:
                    best, best_score = h, sc
                if best_score == 3:
                    break
            if best:
                return best
            time.sleep(0.12)
        return 0

# ---------------- 公共接口 ----------------
def reveal(path, hold_ms=6000, timeout=4.0):
    """在文件夹里选中目标文件并高亮，窗口压到浏览器之上。"""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "路径不存在"}
    folder = p if p.is_dir() else p.parent
    try:
        if not IS_WIN:
            if os.uname().sysname == "Darwin":      # noqa: F821  仅 mac 走到
                subprocess.Popen(["open", "-R", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
            return {"ok": True, "hinted": False, "backend": "os"}
    except Exception:
        pass

    before = {h for h, _ in _explorer_windows()}
    if p.is_dir():
        subprocess.Popen(f'explorer "{p}"')
    else:
        subprocess.Popen(f'explorer /select,"{p}"')
    hwnd = _find_window(folder.name, before, timeout)
    if hwnd:
        _bring_front(hwnd, hold_ms)
    return {"ok": True, "hinted": bool(hwnd), "backend": "explorer"}


def open_path(path, folder=False):
    """用默认程序打开文件，或在资源管理器里打开目录。"""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "路径不存在"}
    if folder and not p.is_dir():
        p = p.parent
    if IS_WIN:
        os.startfile(str(p))
    elif os.uname().sysname == "Darwin":            # noqa: F821
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])
    return {"ok": True}
