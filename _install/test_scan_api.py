# -*- coding: utf-8 -*-
"""验收：/api/scan_dir 接口（HTTP 层）与确认导入后的路径可用性。"""
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

PORT = 18081
BASE = f"http://127.0.0.1:{PORT}"
fails = []


def ck(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  -> " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


def post(path, payload, timeout=30):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def get(path, timeout=10):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


root = Path("D:/HyMT-Translator/_install/_扫描测试") / "短篇集"
shutil.rmtree(root.parent, ignore_errors=True)
(root / "第一卷").mkdir(parents=True)
(root / "第一卷" / "插图").mkdir()
(root / "a.txt").write_text("Hello world. This is a test file for folder import.\n", encoding="utf-8")
(root / "第一卷" / "b.txt").write_text("Second file in a sub folder.\n", encoding="utf-8")
(root / "第一卷" / "封面.jpg").write_bytes(b"\xff\xd8\xff")
(root / "~$tmp.txt").write_text("x", encoding="utf-8")

code, r = post("/api/scan_dir", {"path": str(root), "recursive": True, "limit": 400})
print("scan:", code, json.dumps({k: r.get(k) for k in ("ok", "count", "unsupported", "root", "truncated")}, ensure_ascii=False))
ck("接口 200", code == 200)
ck("ok=True", r.get("ok") is True)
ck("扫到 2 个", r.get("count") == 2, r.get("count"))
ck("root 为文件夹名", r.get("root") == "短篇集", r.get("root"))
ck("跳过 1 个不支持", r.get("unsupported") == 1, r.get("unsupported"))
ck("锁文件不计入", not any("~$" in f["name"] for f in r["files"]))

code2, r2 = post("/api/scan_dir", {"path": str(root), "recursive": False})
ck("不包含子文件夹时 1 个", r2.get("count") == 1, r2.get("count"))

code3, r3 = post("/api/scan_dir", {"path": str(root / "不存在")})
ck("不存在时 400", code3 == 400, code3)
ck("不存在时有 error", bool(r3.get("error")), r3.get("error"))

code4, r4 = post("/api/scan_dir", {"path": ""})
ck("空路径 400", code4 == 400, code4)

# 确认导入后的路径能被建任务接口接受（只看校验，不真跑整篇）
code5, r5 = post("/api/jobs", {"paths": [], "settings": {}})
ck("空路径列表被拒", code5 == 400, code5)
paths = [f["path"] for f in r["files"]]
ck("返回的是真实路径", all(os.path.exists(p) for p in paths), paths)

st = get("/api/state")
print("engine:", st["engine"].get("state"), st["engine"].get("backend"))
ck("引擎状态可读", "state" in st["engine"])

print()
print("FAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
