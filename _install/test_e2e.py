# -*- coding: utf-8 -*-
"""端到端测试：上传 -> 建任务 -> 轮询快照 -> 校验输出"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18081"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SAMPLES = Path(r"D:\HyMT-Translator\_install\samples")


def post(path, data=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(data or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with OP.open(req, timeout=120) as r:
        return json.loads(r.read().decode())


def get(path, timeout=30):
    with OP.open(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def upload(p: Path):
    req = urllib.request.Request(f"{BASE}/api/upload?name={urllib.parse.quote(p.name)}",
                                 data=p.read_bytes(),
                                 headers={"Content-Type": "application/octet-stream"})
    with OP.open(req, timeout=600) as r:
        return json.loads(r.read().decode())


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "interleave"
    tgt = sys.argv[2] if len(sys.argv) > 2 else "zh"
    only = sys.argv[3] if len(sys.argv) > 3 else None

    files = sorted(SAMPLES.glob("*"))
    if only:
        files = [f for f in files if only in f.name]
    paths = []
    for p in files:
        d = upload(p)
        print(f"upload {p.name:<24} supported={d['supported']}", flush=True)
        if d["supported"]:
            paths.append(Path(d["path"]))

    settings = {
        "tgt_lang": tgt, "src_lang": "auto", "out_mode": mode,
        "out_dir_mode": "app", "out_dir": r"D:\HyMT-Translator\output",
        "chunk_chars": 1200, "use_context": True, "glossary": [],
    }
    print(f"\n=== 提交任务 mode={mode} target={tgt} files={len(paths)} ===", flush=True)
    t0 = time.time()
    jid = post("/api/jobs", {"paths": [str(p) for p in paths], "settings": settings})["job"]

    seen = {}
    while True:
        time.sleep(3)
        try:
            snap = get(f"/api/jobs?job={jid}")
        except Exception as e:
            print("轮询失败", e, flush=True)
            break
        for i, f in enumerate(snap["files"]):
            key = f["status"] + str(int(f["progress"] * 20))
            if seen.get(i) != key:
                seen[i] = key
                print(f"  {f['name']:<24} {f['status']:<8} {int(f['progress']*100):>3}% "
                      f"{f['stage'][:24]:<26} {f.get('chars',0):>5}字 {f.get('tps',0):>5}tok/s", flush=True)
        if snap["finished"]:
            break
        if time.time() - t0 > 1800:
            print("超时", flush=True)
            break

    outs = []
    for f in snap["files"]:
        if f["status"] == "done":
            outs += f.get("outputs", [])
        else:
            print("  失败:", f["name"], f.get("error"), flush=True)
    print(f"\n用时 {time.time()-t0:.0f}s，输出 {len(outs)} 个文件：", flush=True)
    for o in outs:
        p = Path(o)
        print(f"  {p}  {p.stat().st_size if p.exists() else 'MISSING'} bytes", flush=True)


if __name__ == "__main__":
    main()
