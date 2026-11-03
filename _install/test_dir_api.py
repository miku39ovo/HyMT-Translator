# -*- coding: utf-8 -*-
"""接口级验证：/api/checkdir 各种情况 + /api/jobs 对坏目录的拦截。"""
import json
import shutil
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, r"D:\HyMT-Translator\app")
import config  # noqa: E402

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = Path(r"D:\HyMT-Translator\_api_probe")
PORT = None

state = json.load(OPENER.open("http://127.0.0.1:18081/api/state", timeout=10))
PORT = state.get("port") or 18081
print("ui port", PORT, "| engine", state["engine"].get("state"))

ok_all = True


def show(name, cond, extra=""):
    global ok_all
    ok_all = ok_all and cond
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))


def post(path, payload):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (PORT, path),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with OPENER.open(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


shutil.rmtree(BASE, ignore_errors=True)

# 1. 自定义目录不存在 → 建出来
code, d = post("/api/checkdir", {"mode": "custom", "path": str(BASE / "sub"), "create": True})
show("custom 自动创建", code == 200 and d["ok"] and d["created"] and (BASE / "sub").is_dir(),
     str(d))

# 2. 只体检不建
code, d = post("/api/checkdir", {"mode": "custom", "path": str(BASE / "nope"), "create": False})
show("create=false 不建目录", code == 200 and not d["ok"] and not (BASE / "nope").exists(),
     d.get("error", ""))

# 3. 指向文件
f = BASE / "a.txt"
f.write_text("x", encoding="utf-8")
code, d = post("/api/checkdir", {"mode": "custom", "path": str(f)})
show("指向文件报错", code == 200 and not d["ok"] and "文件" in d["error"], d.get("error", ""))

# 4. 空路径
code, d = post("/api/checkdir", {"mode": "custom", "path": ""})
show("空路径报错", code == 200 and not d["ok"], d.get("error", ""))

# 5. 统一目录
code, d = post("/api/checkdir", {"mode": "app", "path": str(BASE / "app")})
show("统一目录", code == 200 and d["ok"], d.get("path", ""))

# 6. 提交任务时目录不可用 → 400 拦截
sample = config.APP.parent / "示例文件" / "sample_en.txt"
if not sample.exists():
    sample = sorted((config.APP.parent / "示例文件").glob("*"))[0]
code, d = post("/api/jobs", {
    "paths": [str(sample)],
    "settings": {"out_dir_mode": "custom", "custom_dir": str(f), "tgt_lang": "zh"},
})
show("坏目录拦截建任务", code == 400 and "文件" in (d.get("error") or ""), str(d)[:120])

shutil.rmtree(BASE, ignore_errors=True)
print("\nRESULT:", "ALL PASS" if ok_all else "SOME FAILED")
