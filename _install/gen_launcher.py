# -*- coding: utf-8 -*-
"""生成 VBS 启动器（必须是 UTF-16LE + BOM，否则 WSH 按 ANSI 解析中文会报 800A0408）"""
import os

LINES = [
    "' 混元翻译工作台 · 启动器（无窗口运行）",
    "' 双击后在本机启动翻译服务，并自动打开浏览器界面",
    "Option Explicit",
    "Dim sh, fso, base, py, srv",
    'Set sh = CreateObject("WScript.Shell")',
    'Set fso = CreateObject("Scripting.FileSystemObject")',
    "",
    "base = fso.GetParentFolderName(WScript.ScriptFullName)",
    'py = base & "\\pyenv\\Scripts\\python.exe"',
    'srv = base & "\\app\\server.py"',
    "",
    "If Not fso.FileExists(py) Then",
    '  MsgBox "找不到 Python 运行环境：" & py, 16, "混元翻译工作台"',
    "  WScript.Quit 1",
    "End If",
    "If Not fso.FileExists(srv) Then",
    '  MsgBox "找不到主程序：" & srv, 16, "混元翻译工作台"',
    "  WScript.Quit 1",
    "End If",
    "",
    'sh.CurrentDirectory = base & "\\app"',
    'sh.Run """" & py & """ """ & srv & """ --open", 0, False',
    "",
]

out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "启动混元翻译.vbs")
data = "\r\n".join(LINES)
with open(out, "w", encoding="utf-16-le", newline="") as f:
    f.write("\ufeff" + data)
print("written:", out, os.path.getsize(out), "bytes")
with open(out, "rb") as f:
    head = f.read(4)
print("BOM:", head[:2].hex(), "expect fffe")
