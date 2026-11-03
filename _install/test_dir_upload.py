"""验证文件夹导入的后端侧：并发多文件上传、0 字节拦截、不支持格式拦截。"""
import json
import sys
import time
import threading
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18081"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SAMPLE = Path("D:/HyMT-Translator/示例文件")

results = {}
lock = threading.Lock()


def upload(path: Path):
    data = path.read_bytes()
    url = f"{BASE}/api/upload?name={urllib.parse.quote(path.name)}"
    req = urllib.request.Request(url, data=data, method="POST")
    t0 = time.time()
    try:
        with OPENER.open(req, timeout=60) as r:
            d = json.load(r)
        d["ms"] = round((time.time() - t0) * 1000)
        with lock:
            results[path.name] = d
    except Exception as e:
        with lock:
            results[path.name] = {"error": str(e), "ms": round((time.time() - t0) * 1000)}


def main():
    files = sorted(SAMPLE.iterdir())
    empty = Path("D:/HyMT-Translator/_install/_empty.txt")
    empty.write_bytes(b"")
    bad = Path("D:/HyMT-Translator/_install/_bad.xyz")
    bad.write_text("not a supported ext", encoding="utf-8")
    files += [empty, bad]

    print(f"并发上传 {len(files)} 个文件（含 1 个空文件、1 个不支持格式）…")
    t0 = time.time()
    threads = [threading.Thread(target=upload, args=(p,)) for p in files]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = time.time() - t0

    ok = bad_ok = empty_ok = 0
    for name, d in sorted(results.items()):
        if d.get("error"):
            print(f"  [网络错误] {name}: {d['error']}")
            continue
        sup = d.get("supported")
        if sup:
            ok += 1
            print(f"  [OK]   {name:<24} {d['size']:>8} B  {d['ms']} ms")
        else:
            reason = d.get("reason") or ""
            if "空的" in reason or "没有文字" in reason:
                empty_ok += 1
                print(f"  [空]   {name:<24} 拒绝: {reason}")
            else:
                bad_ok += 1
                print(f"  [跳过] {name:<24} 拒绝: {reason}")

    print(f"\n总耗时 {total:.2f}s ｜ 成功 {ok} ｜ 空文件拦截 {empty_ok} ｜ 格式拦截 {bad_ok}")
    expect_ok = len([p for p in files if p.suffix.lower() in
                     (".txt", ".md", ".srt", ".lrc", ".docx", ".pdf", ".epub") and p.stat().st_size > 0])
    passed = ok == expect_ok and empty_ok == 1 and bad_ok == 1
    print("RESULT:", "PASS" if passed else f"FAIL (expect ok={expect_ok})")

    empty.unlink(missing_ok=True)
    bad.unlink(missing_ok=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
