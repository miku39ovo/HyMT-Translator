# -*- coding: utf-8 -*-
"""确认新加的目录自检不会误伤正常任务：合法目录能正常建任务。"""
import json
import shutil
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, r"D:\HyMT-Translator\app")
import config  # noqa: E402

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = Path(r"D:\HyMT-Translator\_job_dir_probe")
PORT = json.load(OPENER.open("http://127.0.0.1:18081/api/state", timeout=10)).get("port") or 18081


def post(path, payload):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (PORT, path),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with OPENER.open(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


sample = config.APP.parent / "示例文件" / "sample_en.txt"
if not sample.exists():
    sample = sorted((config.APP.parent / "示例文件").glob("*"))[0]

shutil.rmtree(BASE, ignore_errors=True)
ok = True

# 1. 自定义目录（不存在）→ 建任务时应自动创建并放行
code, d = post("/api/jobs", {
    "paths": [str(sample)],
    "settings": {"out_dir_mode": "custom", "custom_dir": str(BASE / "new"), "tgt_lang": "zh"},
})
cond = code == 200 and (BASE / "new").is_dir()
ok = ok and cond
print(("PASS " if cond else "FAIL ") + "自定义目录自动创建后放行", code, d.get("dir", {}).get("created"))
if d.get("job"):
    post("/api/jobs/cancel", {"job": d["job"]})

# 2. 统一输出目录
code, d = post("/api/jobs", {
    "paths": [str(sample)],
    "settings": {"out_dir_mode": "app", "out_dir": str(BASE / "app"), "tgt_lang": "zh"},
})
cond = code == 200
ok = ok and cond
print(("PASS " if cond else "FAIL ") + "统一目录放行", code, d.get("dir", {}).get("path"))
if d.get("job"):
    post("/api/jobs/cancel", {"job": d["job"]})

shutil.rmtree(BASE, ignore_errors=True)
print("\nRESULT:", "ALL PASS" if ok else "SOME FAILED")
