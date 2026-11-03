# -*- coding: utf-8 -*-
"""创建桌面快捷方式（PowerShell COM 被策略拦截，改用 pywin32）"""
import os
import subprocess
import sys

import win32com.client

BASE = r"D:\HyMT-Translator"
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
LNK = os.path.join(DESKTOP, "混元翻译.lnk")
TARGET = os.path.join(BASE, "启动混元翻译.vbs")
ICON = os.path.join(BASE, "_install", "icon.ico")

shell = win32com.client.Dispatch("WScript.Shell")
sc = shell.CreateShortcut(LNK)
sc.TargetPath = TARGET
sc.WorkingDirectory = BASE
sc.IconLocation = ICON + ",0"
sc.Description = "混元 HY-MT1.5-7B 本地翻译工作台（中日英互译 · 批量文件）"
sc.WindowStyle = 7  # 最小化启动（VBS 本身无窗口）
sc.Save()

# 同时放一份到「开始」菜单，便于搜索
start_menu = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs")
sm_lnk = os.path.join(start_menu, "混元翻译工作台.lnk")
sc2 = shell.CreateShortcut(sm_lnk)
sc2.TargetPath = TARGET
sc2.WorkingDirectory = BASE
sc2.IconLocation = ICON + ",0"
sc2.Description = "混元 HY-MT1.5-7B 本地翻译工作台"
sc2.Save()

print("desktop:", LNK, os.path.exists(LNK))
print("startmenu:", sm_lnk, os.path.exists(sm_lnk))
print("target exists:", os.path.exists(TARGET), "icon exists:", os.path.exists(ICON))
