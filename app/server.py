# -*- coding: utf-8 -*-
"""本地 Web 服务：承载 UI、文件上传、任务调度与 SSE 进度推送。"""
import json
import mimetypes
import os
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config
import engine
import fspick
import jobs as jobs_mod
import pipeline
import shellopen
from engine import EngineError, get_engine

# pythonw 启动时没有控制台，sys.stdout 为 None，print 会直接抛异常退出
if sys.stdout is None or sys.stderr is None:
    _logf = open(config.LOGS / "ui.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _logf
    if sys.stderr is None:
        sys.stderr = _logf

WEB = config.APP / "web"
STATE = {"port": None}


def _json_default(o):
    return str(o)


class Handler(BaseHTTPRequestHandler):
    server_version = "HyMT-UI/1.0"
    protocol_version = "HTTP/1.1"

    # ---------------- 基础 ----------------
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, default=_json_default).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _query(self):
        q = urllib.parse.urlparse(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(q, keep_blank_values=True).items()}

    # ---------------- GET ----------------
    def do_GET(self):
        try:
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[8:])
            if path == "/api/state":
                cfg = config.load()
                eng = get_engine(cfg)
                langs = [{"code": k, "cn": v[0], "en": v[1], "label": v[2]}
                         for k, v in engine.LANGS.items()]
                return self._send(200, {"engine": eng.status(), "config": cfg,
                                        "port": STATE["port"],
                                        "supported": sorted(pipeline.SUPPORT_EXT),
                                        "langs": langs,
                                        "cn_family": sorted(engine.CN_FAMILY)})
            if path == "/api/events":
                return self._sse_job()
            if path == "/api/jobs":
                q = self._query()
                job = jobs_mod.MANAGER.get(q.get("job", ""))
                if not job:
                    return self._send(404, {"error": "job not found"})
                return self._send(200, job.snapshot())
            if path == "/api/preview":
                return self._preview()
            if path == "/api/list_output":
                return self._list_output()
            if path == "/favicon.ico":
                return self._send(204, b"")
            return self._send(404, {"error": "not found"})
        except Exception:
            traceback.print_exc()
            return self._send(500, {"error": traceback.format_exc()})

    def _static(self, rel):
        p = (WEB / rel).resolve()
        if not str(p).startswith(str(WEB.resolve())) or not p.exists():
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        return self._send(200, p.read_bytes(), ctype)

    def _preview(self):
        q = self._query()
        p = Path(q.get("path", ""))
        if not p.exists():
            return self._send(404, {"error": "文件不存在"})
        limit = int(q.get("limit", 20000))
        text = ""
        try:
            if p.suffix.lower() == ".docx" and pipeline._HAS_DOCX:
                import docx
                d = docx.Document(str(p))
                text = "\n".join(x.text for x in d.paragraphs if x.text.strip())
            elif p.suffix.lower() == ".epub":
                text = pipeline.epub_text(p)
            else:
                text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            text = f"（无法预览：{e}）"
        return self._send(200, {"name": p.name, "text": text[:limit],
                                "truncated": len(text) > limit})

    def _list_output(self):
        cfg = config.load()
        mode = cfg.get("out_dir_mode") or "app"
        if mode == "custom":
            base = Path(str(cfg.get("custom_dir") or "").strip()
                        or cfg.get("out_dir") or config.OUTPUT)
        else:
            base = Path(cfg.get("out_dir") or config.OUTPUT)
        items = []
        if base.exists():
            for p in sorted(base.rglob("*"), key=lambda x: -x.stat().st_mtime if x.exists() else 0):
                if p.is_file() and p.suffix.lower() in (".txt", ".srt", ".lrc", ".docx", ".pdf", ".epub"):
                    items.append({"path": str(p), "name": p.name, "dir": str(p.parent),
                                  "size": p.stat().st_size,
                                  "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))})
                if len(items) >= 200:
                    break
        return self._send(200, {"base": str(base), "items": items})

    def _sse_job(self):
        q = self._query()
        job = jobs_mod.MANAGER.get(q.get("job", ""))
        if not job:
            return self._send(404, {"error": "job not found"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        since = 0
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                with job.cond:
                    if len(job.events) <= since and not job.finished:
                        job.cond.wait(1.0)
                    evs = job.events[since:]
                    since = len(job.events)
                    finished = job.finished
                for e in evs:
                    data = json.dumps(e, ensure_ascii=False, default=_json_default)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                if evs:
                    self.wfile.flush()
                elif not finished:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                if finished and not evs:
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------------- POST ----------------
    def do_POST(self):
        try:
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/upload":
                return self._upload()
            if path == "/api/jobs":
                return self._new_job()
            if path == "/api/config":
                cfg = config.load()
                cfg.update(json.loads(self._body() or b"{}"))
                config.save(cfg)
                get_engine(cfg)
                return self._send(200, {"ok": True, "config": config.load()})
            if path == "/api/engine/start":
                cfg = config.load()
                body = self._body()
                if body:
                    cfg.update(json.loads(body))
                    config.save(cfg)
                eng = get_engine(cfg)
                eng.boot()
                return self._send(200, {"ok": True})
            if path == "/api/engine/stop":
                get_engine().stop()
                return self._send(200, {"ok": True})
            if path == "/api/jobs/cancel":
                d = json.loads(self._body() or b"{}")
                ok = jobs_mod.MANAGER.cancel(d.get("job", ""))
                return self._send(200, {"ok": ok})
            if path == "/api/pickdir":
                # 弹系统「选择文件夹」框。请求会一直挂到用户选完或取消，
                # ThreadingHTTPServer 是多线程的，不影响别的接口。
                d = json.loads(self._body() or b"{}")
                r = fspick.pick_folder(
                    initial=d.get("initial") or "",
                    title=d.get("title") or "选择译文输出目录")
                return self._send(200, r)
            if path == "/api/checkdir":
                d = json.loads(self._body() or b"{}")
                mode = d.get("mode") or "app"
                p = str(d.get("path") or "").strip()
                s = {"out_dir_mode": mode}
                if mode == "custom":
                    s["custom_dir"] = p
                else:
                    s["out_dir"] = p
                r = pipeline.check_out_dir(s, d.get("src") or None,
                                           create=bool(d.get("create", True)))
                return self._send(200, r)
            if path == "/api/scan_dir":
                # 「选择文件夹」走这条路：直接扫磁盘，文件不用再上传一遍
                d = json.loads(self._body() or b"{}")
                try:
                    limit = int(d.get("limit") or 400)
                except (TypeError, ValueError):
                    limit = 400
                r = pipeline.scan_dir(d.get("path") or "",
                                      recursive=bool(d.get("recursive", True)),
                                      limit=max(1, min(limit, 5000)))
                return self._send(200 if r.get("ok") else 400, r)
            if path == "/api/open":
                d = json.loads(self._body() or b"{}")
                target = Path(d.get("path", ""))
                # reveal：在资源管理器里定位并高亮，窗口顶到最前
                if d.get("reveal"):
                    r = shellopen.reveal(target)
                    return self._send(200 if r.get("ok") else 404, r)
                r = shellopen.open_path(target, folder=bool(d.get("folder")))
                return self._send(200 if r.get("ok") else 404, r)
            if path == "/api/test":
                return self._test_translate()
            if path == "/api/quit":
                get_engine().stop()
                self._send(200, {"ok": True})

                def _die():
                    time.sleep(0.4)
                    os._exit(0)
                threading.Thread(target=_die, daemon=True).start()
                return
            return self._send(404, {"error": "not found"})
        except Exception:
            traceback.print_exc()
            return self._send(500, {"error": traceback.format_exc()})

    def _upload(self):
        q = self._query()
        name = os.path.basename(urllib.parse.unquote(q.get("name", "file")))
        slug = f"{int(time.time()*1000):x}" + os.urandom(3).hex()
        dest = config.UPLOADS / slug / name       # 每份上传独立目录，保留原始文件名
        n = int(self.headers.get("Content-Length") or 0)
        if n > 400 * 1024 * 1024:
            return self._send(413, {"error": "文件过大（上限 400MB）"})
        got = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            while got < n:
                chunk = self.rfile.read(min(1 << 20, n - got))
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        ok = pipeline.is_supported(dest)
        reason = ""
        if got == 0:
            ok, reason = False, "文件是空的（0 字节），没有任何内容可翻译"
        elif ok and pipeline.looks_empty(dest):
            ok, reason = False, "文件里没有文字（可能是空文档或扫描件），请检查原件"
        return self._send(200, {"path": str(dest), "name": name, "size": got,
                                "supported": ok, "ext": dest.suffix.lower(),
                                "reason": reason})

    def _new_job(self):
        d = json.loads(self._body() or b"{}")
        paths = [Path(p) for p in d.get("paths") or []]
        paths = [p for p in paths if p.exists() and pipeline.is_supported(p)]
        if not paths:
            return self._send(400, {"error": "没有可处理的文件"})
        cfg = config.load()
        s = dict(cfg)
        s.update(d.get("settings") or {})
        # 关键参数以 UI 提交为准，其余沿用配置
        for k in ("model_path", "ctx", "ngl", "threads", "server_port"):
            s[k] = cfg.get(k)
        # 开跑前再自检一次输出目录：前端那关可能被绕过（比如直接调接口），
        # 目录建不出来就当场报错，别等翻完几千字才发现写不进去。
        chk = pipeline.check_out_dir(s, paths[0])
        if not chk.get("ok"):
            return self._send(400, {"error": chk.get("error") or "输出目录不可用", "dir": chk})
        job = jobs_mod.MANAGER.create(paths, s)
        return self._send(200, {"job": job.id, "count": len(paths), "dir": chk})

    def _test_translate(self):
        d = json.loads(self._body() or b"{}")
        text = (d.get("text") or "").strip()
        tgt = d.get("tgt") or "zh"
        src = d.get("src") or "auto"
        if not text:
            return self._send(400, {"error": "请输入文本"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        eng = get_engine()
        try:
            for piece in eng.translate_stream(text, src, tgt):
                self.wfile.write(f"data: {json.dumps({'d': piece}, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
        except Exception as e:
            self.wfile.write(f"data: {json.dumps({'e': str(e)}, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
        finally:
            try:
                self.wfile.write(b"data: {\"done\":1}\n\n")
                self.wfile.flush()
            except Exception:
                pass


def pick_port(start):
    for p in range(start, start + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def cleanup_uploads(days=3):
    try:
        now = time.time()
        for p in config.UPLOADS.iterdir():
            if not p.exists() or now - p.stat().st_mtime < days * 86400:
                continue
            if p.is_dir():
                import shutil
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
    except Exception:
        pass


def find_running(start, span=20):
    """已有实例在跑就直接复用，避免重复开进程。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for p in range(start, start + span):
        try:
            with op.open(f"http://127.0.0.1:{p}/api/state", timeout=0.35) as r:
                if b"engine" in r.read(200):
                    return p
        except Exception:
            continue
    return None


def main():
    import webbrowser
    cfg = config.load()
    start = int(cfg.get("app_port", 18081))
    alive = find_running(start)
    if alive:
        print(f"已有实例运行：http://127.0.0.1:{alive}", flush=True)
        if "--open" in sys.argv:
            webbrowser.open(f"http://127.0.0.1:{alive}/")
        return

    cleanup_uploads()
    port = pick_port(start)
    STATE["port"] = port
    (config.LOGS / "ui_port.txt").write_text(str(port), encoding="utf-8")
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    print(f"HY-MT 翻译器 UI: {url}", flush=True)
    if "--open" in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        get_engine().stop()


if __name__ == "__main__":
    main()
