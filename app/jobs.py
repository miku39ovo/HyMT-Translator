# -*- coding: utf-8 -*-
"""批量任务队列：顺序调度、逐文件进度、SSE 事件广播、可取消。"""
import threading
import time
import traceback
import uuid
from pathlib import Path

import config
import pipeline
from engine import EngineError, get_engine


class Job:
    def __init__(self, file_paths, settings):
        self.id = uuid.uuid4().hex[:12]
        self.settings = dict(settings)
        self.files = []
        for p in file_paths:
            p = Path(p)
            self.files.append({
                "id": uuid.uuid4().hex[:8],
                "path": str(p),
                "name": p.name,
                "ext": p.suffix.lower(),
                "size": p.stat().st_size if p.exists() else 0,
                "status": "pending",     # pending|running|done|error|canceled
                "progress": 0.0,
                "stage": "",
                "note": "",
                "chars": 0,
                "tps": 0,
                "avg_tps": 0,
                "eta": 0,
                "outputs": [],
                "error": "",
                "elapsed": 0,
            })
        self.status = "pending"
        self.created = time.time()
        self.finished = False
        self.cancel_flag = False
        self.events = []
        self.cond = threading.Condition()
        self.totals = {"files": len(self.files), "done": 0, "chars": 0,
                       "tokens": 0, "gen_sec": 0.0,
                       "error": 0, "canceled": 0}

    # -------- 事件 --------
    def emit(self, **payload):
        payload.setdefault("t", round(time.time() - self.created, 2))
        with self.cond:
            self.events.append(payload)
            self.cond.notify_all()

    def snapshot(self):
        return {
            "id": self.id,
            "status": self.status,
            "finished": self.finished,
            "files": self.files,
            "totals": self.totals,
            "settings": {k: self.settings.get(k) for k in
                         ("tgt_lang", "src_lang", "out_mode", "out_dir", "out_dir_mode",
                          "custom_dir", "rename_output")},
        }


class Manager:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()
        self.queue = []
        self.worker = None

    def create(self, file_paths, settings):
        job = Job(file_paths, settings)
        with self.lock:
            self.jobs[job.id] = job
            self.queue.append(job)
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._run, daemon=True)
                self.worker.start()
        job.emit(type="created", job=job.id)
        return job

    def get(self, jid):
        return self.jobs.get(jid)

    def cancel(self, jid):
        job = self.jobs.get(jid)
        if not job:
            return False
        job.cancel_flag = True
        job.emit(type="canceling")
        return True

    # -------- 工作线程 --------
    def _run(self):
        while True:
            with self.lock:
                if not self.queue:
                    self.worker = None
                    return
                job = self.queue.pop(0)
            try:
                self._process(job)
            except Exception:
                pass

    def _process(self, job):
        job.status = "running"
        job.emit(type="job", status="running")
        engine = get_engine(dict(job.settings) | {"model_path": job.settings.get("model_path")})

        # 载入模型（首次会花十几秒到几十秒）
        try:
            if engine.state != "ready":
                job.emit(type="engine", state="loading", message="正在加载模型到显存…")
                engine.cfg = dict(job.settings)
                engine.boot()
                t0 = time.time()
                while engine.state != "ready":
                    if engine.state == "error":
                        raise EngineError(engine.message)
                    if job.cancel_flag:
                        raise pipeline.Cancelled()
                    time.sleep(0.6)
                    job.emit(type="engine", state=engine.state, message=engine.message,
                             wait=round(time.time() - t0, 1),
                             note=f"加载模型到显存 · 已用 {int(time.time() - t0)} 秒")
                    if time.time() - t0 > 420:
                        raise EngineError("模型加载超时")
                job.emit(type="engine", state="ready", message="模型已就绪")
        except pipeline.Cancelled:
            job.emit(type="engine", state="stopped", message="已停止")
            for f in job.files:
                if f["status"] == "pending":
                    f["status"] = "canceled"
                    f["stage"] = "已停止"
            job.status = "canceled"
            job.finished = True
            job.emit(type="job", status="canceled", totals=job.totals)
            return
        except EngineError as e:
            job.emit(type="error", message=str(e))
            for f in job.files:
                f["status"] = "error"
                f["error"] = str(e)
            job.status = "error"
            job.finished = True
            job.emit(type="job", status="error")
            return

        for idx, f in enumerate(job.files):
            if job.cancel_flag:
                f["status"] = "canceled"
                f["stage"] = "已停止"
                f["note"] = "未开始"
                job.emit(type="file", idx=idx, **{k: f[k] for k in ("status", "progress", "stage", "note")})
                continue
            self._run_file(job, idx, f, engine)

        job.status = "canceled" if job.cancel_flag else "done"
        job.finished = True
        job.emit(type="job", status=job.status, totals=job.totals)

    def _run_file(self, job, idx, f, engine):
        f.update(status="running", progress=0.0, stage="准备中", note="", error="",
                 outputs=[], eta=0, chars=0, tps=0, avg_tps=0)
        job.emit(type="file", idx=idx, **{k: f[k] for k in ("status", "progress", "stage", "note")})
        t0 = time.time()
        last = [0.0]
        keys = ("status", "progress", "stage", "note", "chars", "tps", "avg_tps", "elapsed", "eta")

        def report(done=None, total=None, stage=None, tps=None, used_chars=None,
                   tokens=None, gen_sec=None, note=None, force=False):
            if total:
                f["_total"] = total
            if done is not None and f.get("_total"):
                f["progress"] = min(0.995, done / max(1, f["_total"]))
            if stage:
                f["stage"] = stage
            if note is not None:
                f["note"] = note
            if tps:
                f["tps"] = tps
            if used_chars:
                f["chars"] = used_chars
            if tokens:
                f["_tokens"] = int(tokens)
            if gen_sec:
                f["_gen_sec"] = float(gen_sec)
            if f.get("_gen_sec") and f["_gen_sec"] > 0.5 and f.get("_tokens"):
                f["avg_tps"] = round(f["_tokens"] / f["_gen_sec"], 1)
            now = time.time()
            seg = now - t0
            # 预计剩余时间：按已完成比例外推，再做指数平滑，避免数字来回跳
            p = f["progress"]
            if p >= 0.02 and seg > 2.5:
                raw = seg * (1 - p) / p
                prev = f.get("_eta")
                f["_eta"] = raw if not prev else prev * 0.65 + raw * 0.35
                f["eta"] = max(0, round(f["_eta"]))
            if force or now - last[0] > 0.25:
                last[0] = now
                f["elapsed"] = round(seg, 1)
                job.emit(type="file", idx=idx, **{k: f.get(k) for k in keys})

        try:
            outs = pipeline.process(Path(f["path"]), job.settings, engine, report,
                                    lambda: job.cancel_flag)
            f["outputs"] = [str(p) for p in (outs or [])]
            f["progress"] = 1.0
            f["status"] = "done"
            f["stage"] = "已完成"
            f["note"] = f"生成 {len(f['outputs'])} 个文件" if f["outputs"] else ""
            f["eta"] = 0
            job.totals["done"] += 1
            job.totals["chars"] += f.get("chars", 0)
        except pipeline.Cancelled as e:
            # 停止前已经写出的部分照样交付，不丢进度
            outs = [str(p) for p in (getattr(e, "outputs", None) or [])]
            f["outputs"] = outs
            f["status"] = "canceled"
            f["stage"] = "已停止"
            f["eta"] = 0
            if outs:
                f["note"] = f"已保存部分译文（{len(outs)} 个文件）"
                job.totals["canceled"] += 1
            else:
                f["note"] = "还没翻完第一段，未生成文件"
        except Exception as e:
            f["status"] = "error"
            f["error"] = str(e)
            f["stage"] = "失败"
            f["note"] = ""
            job.totals["error"] += 1
            job.emit(type="log", message=f"{f['name']} 失败：{e}")
            traceback.print_exc()
        f["elapsed"] = round(time.time() - t0, 1)
        tok, gen = int(f.get("_tokens") or 0), float(f.get("_gen_sec") or 0)
        if tok and gen > 0.5:
            f["avg_tps"] = round(tok / gen, 1)
            job.totals["tokens"] += tok
            job.totals["gen_sec"] += gen
        for k in [k for k in f if k.startswith("_")]:
            f.pop(k, None)
        job.emit(type="file", idx=idx, **f)


MANAGER = Manager()
