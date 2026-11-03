# -*- coding: utf-8 -*-
"""原生「选择文件夹」对话框（纯 ctypes，不依赖 tkinter / pywin32）。

后台服务跑在 pythonw 里，没有控制台；本机 Python 也没编译 _tkinter
（`import tkinter` 直接报错），所以只能直接调 Windows Shell 的接口。

这里用 SHBrowseForFolderW 而不是 IFileDialog：后者要自己按 vtable 下标
调 COM 方法，IShellItem 那几个槽位在实测里很容易踩错（写坏指针直接崩
进程，连异常都没有），而 SHBrowseForFolderW + SHGetPathFromIDListW 是
两个普通导出函数，签名固定、结构体简单，稳定性高得多。

两个必须注意的点：
1. 必须在**调用它的线程**里 CoInitializeEx(APARTMENTTHREADED)，否则
   BIF_NEWDIALOGSTYLE 的新式对话框出不来；
2. hwndOwner 传当前前台窗口句柄，对话框才会压在浏览器上面。
"""

import ctypes
import os
from ctypes import wintypes

_ole32 = ctypes.WinDLL("ole32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)

COINIT_APARTMENTTHREADED = 2
RPC_E_CHANGED_MODE = 0x80010106

# BROWSEINFO.ulFlags
BIF_RETURNONLYFSDIRS = 0x0001
BIF_EDITBOX = 0x0010
BIF_NEWDIALOGSTYLE = 0x0040
BIF_USENEWUI = BIF_EDITBOX | BIF_NEWDIALOGSTYLE
BIF_VALIDATE = 0x0020

_MAX_PATH = 32768

# BFFM_* 回调消息
_BFFM_INITIALIZED = 1
_BFFM_SETSELECTIONW = 0x0467
_BFFCALLBACK = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.UINT,
                                  wintypes.LPARAM, wintypes.LPARAM)


class BROWSEINFOW(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", wintypes.HWND),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", wintypes.LPWSTR),
        ("lpszTitle", wintypes.LPCWSTR),
        ("ulFlags", wintypes.UINT),
        ("lpfn", ctypes.c_void_p),
        ("lParam", ctypes.c_void_p),
        ("iImage", ctypes.c_int),
    ]


_shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
_shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
_shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
_shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
_shell32.SHParseDisplayName.argtypes = [
    wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]
_shell32.SHParseDisplayName.restype = ctypes.c_long
_ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_ole32.CoInitializeEx.restype = ctypes.c_long
_ole32.CoUninitialize.argtypes = []
_ole32.CoUninitialize.restype = None
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
_ole32.CoTaskMemFree.restype = None
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM]
_user32.SendMessageW.restype = wintypes.LPARAM


def _foreground_hwnd():
    try:
        return _user32.GetForegroundWindow() or 0
    except Exception:
        return 0


def _pidl_to_path(pidl):
    """PIDL（项目标识列表）→ 本地路径；网络位置等取不到路径时返回空串。"""
    if not pidl:
        return ""
    buf = ctypes.create_unicode_buffer(_MAX_PATH)
    if not _shell32.SHGetPathFromIDListW(ctypes.c_void_p(pidl), buf):
        return ""
    return buf.value


def path_to_pidl(path):
    """本地路径 → PIDL，用于把对话框直接打开到某个目录。调用方负责释放。"""
    pidl = ctypes.c_void_p()
    attr = wintypes.DWORD(0)
    hr = _shell32.SHParseDisplayName(
        wintypes.LPCWSTR(str(path)), None, ctypes.byref(pidl), 0, ctypes.byref(attr))
    if hr < 0:
        return 0
    return pidl.value or 0


def pick_folder(initial="", title="选择文件夹", owner=None):
    """弹出系统文件夹选择框，阻塞直到用户选完或取消。

    返回 {"ok", "path", "canceled", "error"}。
    """
    res = {"ok": False, "path": "", "canceled": False, "error": ""}

    hr_init = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    owns_com = hr_init in (0, 1)
    if not owns_com and (hr_init & 0xFFFFFFFF) != RPC_E_CHANGED_MODE:
        res["error"] = "COM 初始化失败（0x%08X）" % (hr_init & 0xFFFFFFFF)
        return res

    root = 0
    name = ctypes.create_unicode_buffer(260)
    keep = []          # 回调里用到的缓冲区必须活到对话框关闭
    try:
        # 上次用过的目录作为起点；拿不到就退回「我的电脑」
        start = str(initial or "").strip().strip('"')
        if start and os.path.isdir(start):
            root = path_to_pidl(start)

        callback = None
        if root:
            # 新式对话框会忽略 pidlRoot 的选中项，必须在 BFFM_INITIALIZED 时用
            # BFFM_SETSELECTION 把位置推过去（wParam=0 表示 lParam 是 PIDL）。
            # 注意 PIDL 要在进对话框之前就解析好：在回调里再调 SHParseDisplayName
            # 会因为重入而失败，返回空 PIDL，消息发过去等于没发。
            @_BFFCALLBACK
            def callback(hwnd, msg, lp, data, _pidl=root):
                if msg == _BFFM_INITIALIZED:
                    _user32.SendMessageW(hwnd, _BFFM_SETSELECTIONW, 0, _pidl)
                return 0

            keep.append(callback)

        bi = BROWSEINFOW()
        bi.hwndOwner = owner or _foreground_hwnd()
        bi.pidlRoot = ctypes.c_void_p(root) if root else None
        bi.pszDisplayName = ctypes.cast(name, wintypes.LPWSTR)
        bi.lpszTitle = title or "选择文件夹"
        bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_USENEWUI
        bi.lpfn = ctypes.cast(callback, ctypes.c_void_p) if callback else None
        bi.lParam = None
        bi.iImage = 0

        pidl = _shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            # 用户点取消，或者对话框没能创建（两种都当取消处理，前端提示重新选）
            res["canceled"] = True
            return res

        try:
            path = _pidl_to_path(pidl)
        finally:
            _ole32.CoTaskMemFree(ctypes.c_void_p(pidl))

        if not path:
            res["error"] = "这个位置不是本地文件夹（网络位置或压缩包内），请选本地目录"
            return res
        res["path"] = path
        res["ok"] = True
        return res
    except OSError as e:
        res["error"] = "调用系统对话框失败：%s" % (e,)
        return res
    finally:
        if root:
            _ole32.CoTaskMemFree(ctypes.c_void_p(root))
        if owns_com:
            _ole32.CoUninitialize()
