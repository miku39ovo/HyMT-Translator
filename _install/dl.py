# -*- coding: utf-8 -*-
"""多线程分片下载器（支持断点续传），用于从 hf-mirror 拉取大文件。"""
import os
import sys
import threading
import time
import urllib.request

URL = sys.argv[1]
OUT = sys.argv[2]
NTHREAD = int(sys.argv[3]) if len(sys.argv) > 3 else 8
PROXY = os.environ.get("DL_PROXY", "")

if PROXY:
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
else:
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def total_size():
    req = urllib.request.Request(URL, method="GET", headers={"Range": "bytes=0-0"})
    with op.open(req, timeout=30) as r:
        cr = r.headers.get("Content-Range")
        if cr:
            return int(cr.split("/")[-1])
        return int(r.headers.get("Content-Length") or 0)


def fetch(i, start, end, results):
    part = f"{OUT}.part{i}"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have >= end - start + 1:
        results[i] = have
        return
    headers = {"Range": f"bytes={start + have}-{end}"}
    for attempt in range(6):
        try:
            req = urllib.request.Request(URL, headers=headers)
            with op.open(req, timeout=120) as r, open(part, "ab") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    results[i] += len(chunk)
            if results[i] >= end - start + 1:
                return
        except Exception as e:
            print(f"  [t{i}] retry {attempt+1}: {e}", flush=True)
            time.sleep(2)
            have = os.path.getsize(part) if os.path.exists(part) else 0
            headers["Range"] = f"bytes={start + have}-{end}"
            results[i] = have


def main():
    size = total_size()
    if not size:
        print("无法获取文件大小", flush=True)
        sys.exit(1)
    print(f"总大小 {size/1e9:.2f} GB，分 {NTHREAD} 线程", flush=True)
    step = size // NTHREAD
    ranges = []
    for i in range(NTHREAD):
        s = i * step
        e = size - 1 if i == NTHREAD - 1 else (i + 1) * step - 1
        ranges.append((s, e))
    results = [0] * NTHREAD
    threads = []
    for i, (s, e) in enumerate(ranges):
        part = f"{OUT}.part{i}"
        results[i] = os.path.getsize(part) if os.path.exists(part) else 0
        t = threading.Thread(target=fetch, args=(i, s, e, results), daemon=True)
        t.start()
        threads.append(t)

    t0 = time.time()
    while any(t.is_alive() for t in threads):
        time.sleep(5)
        done = sum(results)
        sp = done / max(1, time.time() - t0)
        print(f"  {done/1e9:.2f}/{size/1e9:.2f} GB  {sp/1e6:.1f} MB/s", flush=True)
    done = sum(results)
    if done < size:
        print(f"不完整：{done} / {size}", flush=True)
        sys.exit(2)
    with open(OUT, "wb") as out:
        for i in range(NTHREAD):
            with open(f"{OUT}.part{i}", "rb") as f:
                while True:
                    b = f.read(1 << 22)
                    if not b:
                        break
                    out.write(b)
    for i in range(NTHREAD):
        try:
            os.remove(f"{OUT}.part{i}")
        except Exception:
            pass
    print(f"完成：{OUT}  {os.path.getsize(OUT)} bytes", flush=True)


if __name__ == "__main__":
    main()
