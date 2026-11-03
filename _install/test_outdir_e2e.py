"""端到端：自定义输出目录 + 关闭改名，走真实上传/job 流程。"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18081"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SRC = Path("D:/HyMT-Translator/示例文件/sample_en.txt")
OUTDIR = Path("D:/HyMT-Translator/_test输出")


def post(path, data=None):
    body = json.dumps(data).encode() if data is not None else b""
    req = urllib.request.Request(BASE + path, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with OP.open(req, timeout=60) as r:
        return json.load(r)


def upload(p: Path):
    url = f"{BASE}/api/upload?name={urllib.parse.quote(p.name)}"
    req = urllib.request.Request(url, data=p.read_bytes(), method="POST")
    with OP.open(req, timeout=60) as r:
        return json.load(r)


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    for f in OUTDIR.iterdir():
        f.unlink()

    up = upload(SRC)
    print("上传:", up["path"], "supported=", up["supported"])

    settings = {
        "src_lang": "en", "tgt_lang": "zh", "out_mode": "none",
        "out_dir_mode": "custom", "custom_dir": str(OUTDIR),
        "rename_output": False, "use_context": False, "chunk_chars": 600,
        "glossary": [],
    }
    j = post("/api/jobs", {"paths": [up["path"]], "settings": settings})
    jid = j["job"]
    print("任务:", jid)

    t0 = time.time()
    while time.time() - t0 < 300:
        with OP.open(f"{BASE}/api/jobs?job={jid}", timeout=30) as r:
            d = json.load(r)
        if d.get("finished"):
            break
        time.sleep(2)

    f = d["files"][0]
    print("状态:", d["status"], "| 阶段:", f.get("stage"))
    print("输出:", f.get("outputs"))
    print("用时:", round(time.time() - t0, 1), "s")

    files = sorted(p.name for p in OUTDIR.iterdir())
    print("目录内容:", files)

    ok = (d["status"] == "done"
          and files == ["sample_en.txt"]
          and (OUTDIR / "sample_en.txt").stat().st_size > 0)
    print("RESULT:", "PASS" if ok else "FAIL")

    if (OUTDIR / "sample_en.txt").exists():
        txt = (OUTDIR / "sample_en.txt").read_text(encoding="utf-8", errors="ignore")
        print("正文前 80 字:", txt[:80].replace("\n", " "))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
