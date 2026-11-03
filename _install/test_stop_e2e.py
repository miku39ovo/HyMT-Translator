# -*- coding: utf-8 -*-
"""端到端验证「停止 → 保存部分译文」：真实模型、真实任务、走 HTTP 接口。"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18081"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(path, data=None, timeout=120):
    req = urllib.request.Request(BASE + path, data=json.dumps(data or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with OP.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get(path):
    with OP.open(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode())


def upload(name, data):
    req = urllib.request.Request(BASE + "/api/upload?name=" + urllib.parse.quote(name),
                                 data=data, headers={"Content-Type": "application/octet-stream"})
    return json.loads(OP.open(req, timeout=120).read().decode())


src = Path(r"D:\HyMT-Translator\示例文件\long_article.txt")
up = upload(src.name, src.read_bytes())
print("上传:", up["supported"], up["size"], "字节")

settings = {"tgt_lang": "en", "src_lang": "zh", "out_mode": "interleave",
            "out_dir_mode": "app", "out_dir": r"D:\HyMT-Translator\output",
            "chunk_chars": 1200, "use_context": True, "glossary": []}
jid = post("/api/jobs", {"paths": [up["path"]], "settings": settings})["job"]
print("job:", jid)

deadline = time.time() + 12
while time.time() < deadline:
    time.sleep(1)
    f = get("/api/jobs?job=" + jid)["files"][0]
    print("  %2ds %-8s %3d%% %s | %s | eta=%s" % (
        int(time.time() - (deadline - 12)), f["status"], int(f["progress"] * 100),
        f["stage"], f.get("note", ""), f.get("eta")))

print("→ 点停止")
post("/api/jobs/cancel", {"job": jid})

for _ in range(120):
    time.sleep(1)
    snap = get("/api/jobs?job=" + jid)
    f = snap["files"][0]
    if snap["finished"]:
        break
print("job 状态:", snap["status"], "| finished:", snap["finished"])
print("文件:", f["status"], "|", f["stage"], "|", f.get("note"), "| error:", f.get("error"))
outs = f.get("outputs") or []
for p in outs:
    p = Path(p)
    txt = p.read_text(encoding="utf-8", errors="ignore")
    print("输出:", p.name, p.stat().st_size, "字节")
    print("  段落数:", len([x for x in txt.split("\n\n") if x.strip()]))
    print("  前 6 行:")
    for line in [l for l in txt.split("\n") if l.strip()][:6]:
        print("   ", line[:80])
assert outs, "停止后应当有产物"
assert f["status"] == "canceled"
print("\n通过")
