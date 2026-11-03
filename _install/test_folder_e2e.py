# -*- coding: utf-8 -*-
"""端到端：选择文件夹 → 扫描 → 确认导入（本地路径）→ 出译文。

模拟前端「确认导入」后提交的请求：paths 全部是磁盘真实路径，
输出指到临时目录，验证不经过 uploads 也能正常翻完并落盘。
"""
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 18081
BASE = f"http://127.0.0.1:{PORT}"
SRC = Path("D:/HyMT-Translator/_install/_扫描测试/短篇集")
OUT = Path("D:/HyMT-Translator/_install/_扫描输出")


def post(path, payload, timeout=60):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def get(path, timeout=15):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


shutil.rmtree(OUT, ignore_errors=True)
if not SRC.exists():                      # 自己准备素材，不依赖别的测试先跑
    (SRC / "第一卷").mkdir(parents=True, exist_ok=True)
    (SRC / "a.txt").write_text("Hello world. This is a test file for folder import.\n", encoding="utf-8")
    (SRC / "第一卷" / "b.txt").write_text("Second file in a sub folder.\n", encoding="utf-8")

code, scan = post("/api/scan_dir", {"path": str(SRC), "recursive": True})
print("scan:", code, scan.get("count"), scan.get("root"))
paths = [f["path"] for f in scan["files"]]

settings = {
    "out_dir_mode": "custom", "custom_dir": str(OUT),
    "rename_output": True, "tgt": "zh", "src": "en",
    "glossary": "", "ctx": 4096, "batch": 1,
}
code, r = post("/api/jobs", {"paths": paths, "settings": settings})
print("job:", code, r.get("count"), r.get("dir", {}).get("ok") if isinstance(r.get("dir"), dict) else "")
if code != 200:
    print("FAIL 建任务：", r)
    sys.exit(1)

job = r["job"]
t0 = time.time()
last = ""
while time.time() - t0 < 300:
    st = get("/api/jobs?job=" + job)
    files = st["files"]
    done = [f for f in files if f.get("status") in ("done", "error", "skipped")]
    line = f"{len(done)}/{len(files)} " + (files[0].get("stage") or "")
    if line != last:
        print(f"  [{time.time()-t0:5.1f}s] {line}")
        last = line
    if st.get("state") in ("done", "error", "canceled") or len(done) == len(files):
        break
    time.sleep(1.5)

st = get("/api/jobs?job=" + job)
print("state:", st.get("state"))
outs = []
for f in st["files"]:
    print(" -", f["name"], f["status"], f.get("outputs"), f.get("error") or "")
    outs += f.get("outputs") or []
ok = len(outs) == len(paths) and all(f["status"] == "done" for f in st["files"])
for o in outs:
    p = Path(o)
    print("   ->", p, p.exists(), p.stat().st_size if p.exists() else 0)
print("---- 结果 ----")
if ok and all(Path(o).exists() and Path(o).stat().st_size > 0 for o in outs):
    for o in outs:
        print("译文前 60 字：", Path(o).read_text(encoding="utf-8", errors="ignore")[:60].replace("\n", " "))
    print("PASS 端到端：本地文件夹 → 译文落盘")
    sys.exit(0)
print("FAIL 端到端")
sys.exit(1)
