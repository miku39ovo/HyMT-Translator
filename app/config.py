# -*- coding: utf-8 -*-
"""配置管理：所有设置持久化到 D:\\HyMT-Translator\\app\\config.json"""
import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent          # D:\HyMT-Translator
APP = BASE / "app"
MODELS = BASE / "models"
RUNTIME = BASE / "runtime" / "llama"              # Vulkan 版（通用兜底）
RUNTIME_CUDA = BASE / "runtime" / "llama-cuda"    # CUDA 版（NVIDIA 卡首选，快 6 倍）
OUTPUT = BASE / "output"
LOGS = BASE / "logs"
UPLOADS = BASE / "uploads"
for _d in (MODELS, RUNTIME, RUNTIME_CUDA, OUTPUT, LOGS, UPLOADS):
    _d.mkdir(parents=True, exist_ok=True)


def runtime_dir(backend: str = "auto") -> Path:
    """按后端选运行时目录。auto = 有 CUDA 版就用 CUDA（N 卡上快约 6 倍），否则回落 Vulkan。"""
    cuda_ok = (RUNTIME_CUDA / "llama-server.exe").exists() and (RUNTIME_CUDA / "ggml-cuda.dll").exists()
    if backend == "cuda":
        return RUNTIME_CUDA
    if backend in ("vulkan", "cpu"):
        return RUNTIME
    return RUNTIME_CUDA if cuda_ok else RUNTIME

CONFIG_FILE = APP / "config.json"

DEFAULTS = {
    "model_path": str(MODELS / "HY-MT1.5-7B-Q4_K_M.gguf"),
    "server_port": 18080,
    "app_port": 18081,
    "ctx": 8192,
    "ngl": 99,              # 卸载到 GPU 的层数，99 = 全部
    "threads": 8,
    "backend": "auto",      # auto | cuda | vulkan  （CUDA 生成速度快约 6 倍）
    "temperature": 0.1,
    "top_p": 0.6,
    "repeat_penalty": 1.05,
    "src_lang": "auto",     # auto | 见 engine.LANGS（38 种语种代码）
    "tgt_lang": "zh",       # 同上；常用 zh / en / ja
    "out_mode": "interleave",   # none(仅译文) | interleave(段段对照) | table(左右对照) | separate(双文件)
    "out_dir_mode": "app",      # app(统一输出目录) | source(源文件同目录) | custom(自定义目录)
    "out_dir": str(OUTPUT),
    "custom_dir": str(OUTPUT),  # 输出位置选「自定义目录」时用这个，和 out_dir 各存一份互不覆盖
    "rename_output": True,      # 译文文件名加「.译文.目标语言」标记；关掉则沿用原文件名
    "chunk_chars": 1200,        # 长文本按此字符数切块
    "use_context": True,        # 长文上下文衔接（提升长文一致性）
    "concurrency": 1,
    "glossary": [],             # [["source", "target"], ...]
    "keep_srt_time": True,
    "pdf_layout": "flow",       # flow(段落流，适合论文) | simple
    "open_after_finish": False,
    "last_open_dir": str(OUTPUT),
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
        except Exception:
            pass
    return cfg


def save(cfg: dict) -> dict:
    data = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data
