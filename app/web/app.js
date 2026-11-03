(() => {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const api = (p, opt = {}) => fetch(p, opt).then(async (r) => {
    const t = await r.text();
    let d = {};
    try { d = t ? JSON.parse(t) : {}; } catch (e) { d = { error: t }; }
    if (!r.ok) throw new Error(d.error || ("HTTP " + r.status));
    return d;
  });

  const state = {
    cfg: {},
    files: [],        // {path,name,size,ext,status,progress,stage,outputs,error,tps,chars,elapsed}
    job: null,
    running: false,
    es: null,
    mapping: [],      // job file index -> queue index
    tryTimer: null,
    engineNote: "",
    dirConfirmed: false,   // 输出目录是否已点「确认」并通过体检
    dirBusy: false,        // 正在等系统文件夹对话框
    totals: null,     // 本次任务汇总：tokens / gen_sec → 平均 token 速度
  };

  const EXT_CLASS = { ".docx": "doc", ".pdf": "pdf", ".epub": "epub", ".srt": "srt", ".lrc": "lrc", ".txt": "txt", ".md": "txt" };
  const MODE_HINT = {
    none: "只输出译文，文件更干净，适合直接用。",
    interleave: "每段原文下紧跟译文，适合阅读校对。",
    table: "左右两栏对照（Word/PDF 输出），适合论文精读。",
    separate: "同时生成「仅译文」和「对照」两个文件。",
  };

  function toast(msg, type = "") {
    const el = document.createElement("div");
    el.className = "toast " + type;
    el.textContent = msg;
    $("#toasts").appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 250); }, 4200);
  }

  const fmtSize = (b) => b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : (b / 1024).toFixed(0) + " KB";
  const fmtChars = (n) => n > 10000 ? (n / 10000).toFixed(1) + " 万字" : n + " 字";

  // 超过 60 秒一律用「分钟」，避免出现 300 秒这种不直观的数字
  function fmtDur(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    if (sec < 60) return sec + " 秒";
    const m = sec / 60;
    return (m < 10 ? Math.round(m * 10) / 10 : Math.round(m)) + " 分钟";
  }

  // 每个文件当前处在哪一步（显示在进度条上面的小字）
  function stageInfo(f) {
    if (f.status === "done") return { stg: "完成", note: "" };
    if (f.status === "error") return { stg: "失败", note: "" };
    if (f.status === "canceled") return { stg: "已停止", note: f.note || "" };
    if (f.status === "uploading") return { stg: "上传中", note: f.note || "" };
    if (f.status === "ready") return { stg: "待翻译", note: "" };
    return { stg: f.stage || "准备中", note: f.note || "" };
  }

  /* ---------------- 配置 ---------------- */
  const MAIN_LANGS = ["zh", "en", "ja"];
  let LANG_NAMES = { zh: "中文", en: "English", ja: "日本語", auto: "自动识别" };
  let CN_FAMILY = new Set(["zh", "zh-Hant", "yue", "bo", "ug", "kk", "mn"]);

  function langTitle(l) {
    return l.cn === l.label ? l.cn : `${l.cn} · ${l.label}`;
  }

  // 用 /api/state 返回的完整语种表构建下拉框
  function buildLangSelects(langs, cnFamily) {
    if (!Array.isArray(langs) || !langs.length) return;
    if (Array.isArray(cnFamily) && cnFamily.length) CN_FAMILY = new Set(cnFamily);

    const byCode = {};
    langs.forEach((l) => { byCode[l.code] = l; });
    LANG_NAMES = { auto: "自动识别" };
    langs.forEach((l) => { LANG_NAMES[l.code] = l.cn; });

    // 分组：常用 / 中文语族 / 其他
    const main = langs.filter((l) => MAIN_LANGS.includes(l.code));
    const cn = langs.filter((l) => !MAIN_LANGS.includes(l.code) && CN_FAMILY.has(l.code));
    const rest = langs.filter((l) => !MAIN_LANGS.includes(l.code) && !CN_FAMILY.has(l.code));

    const opt = (l) => `<option value="${l.code}">${langTitle(l)}</option>`;
    const groups = [
      ["常用语言", main.map(opt).join("")],
      ["中文语族 / 中国民族语言", cn.map(opt).join("")],
      ["其他语种", rest.map(opt).join("")],
    ].filter(([, html]) => html);

    const srcHtml = `<option value="auto">自动识别</option>` +
      groups.map(([g, html]) => `<optgroup label="${g}">${html}</optgroup>`).join("");
    const tgtHtml = groups.map(([g, html]) => `<optgroup label="${g}">${html}</optgroup>`).join("");

    const src = $("#srcLang"), tgt = $("#tgtLang");
    const keepSrc = src.value, keepTgt = tgt.value;
    src.innerHTML = srcHtml;
    tgt.innerHTML = tgtHtml;
    if (keepSrc && [...src.options].some((o) => o.value === keepSrc)) src.value = keepSrc;
    if (keepTgt && [...tgt.options].some((o) => o.value === keepTgt)) tgt.value = keepTgt;

    const cnt = $("#langCount");
    if (cnt) cnt.textContent = langs.length + " 种语言";
  }

  async function loadState() {
    try {
      const d = await api("/api/state");
      state.cfg = d.config || {};
      buildLangSelects(d.langs, d.cn_family);
      applyConfig(state.cfg);
      updateEngine(d.engine);
      $("#sbPort").textContent = "UI " + location.port + " · 引擎 " + (d.engine.port || "-");
    } catch (e) {
      $("#engineText").textContent = "服务未响应";
    }
  }

  function applyConfig(c) {
    // 配置里若存着当前列表没有的语种（旧版本留下的），回退到默认值，避免空选项
    const pick = (id, v, fb) => {
      const el = $(id);
      el.value = v || fb;
      if (!el.value) el.value = fb;
    };
    pick("#srcLang", c.src_lang, "auto");
    pick("#tgtLang", c.tgt_lang, "zh");
    $("#outDirMode").value = c.out_dir_mode || "app";
    $("#renameOutput").checked = c.rename_output !== false;
    applyDirMode();
    $("#chunkChars").value = c.chunk_chars || 1200;
    $("#ctx").value = String(c.ctx || 8192);
    $("#temperature").value = c.temperature ?? 0.1;
    $("#backend").value = c.backend || "auto";
    $("#ngl").value = c.ngl ?? 99;
    $("#useContext").checked = c.use_context !== false;
    $("#glossary").value = (c.glossary || []).map(([a, b]) => `${a} = ${b}`).join("\n");
    $("#glossCount").textContent = (c.glossary || []).length;
    setMode(c.out_mode || "interleave", false);
    $("#swapHint").textContent = hintText();
  }

  /* ---------------- 输出位置 / 文件命名 ---------------- */
  const dirMode = () => $("#outDirMode").value || "app";
  // 统一目录和自定义目录各存一份，切换模式时不互相覆盖
  const dirField = () => (dirMode() === "custom" ? "custom_dir" : "out_dir");

  function currentOutDir() {
    const c = state.cfg || {};
    return (dirMode() === "custom" ? c.custom_dir : c.out_dir) || "";
  }

  function setDirStatus(kind, text) {
    const el = $("#dirCheck");
    if (!el) return;
    el.className = "dir-check" + (kind ? " " + kind : "");
    el.textContent = text || "";
    el.title = text || "";
  }

  function markDirDirty(text) {
    state.dirConfirmed = false;
    setDirStatus("wait", text || "还没确认，点「确认使用此目录」");
    const b = $("#btnConfirmDir");
    if (b) b.textContent = "确认使用此目录";
  }

  // 拿一个真实文件路径，用于「源文件同目录」模式的检查
  function sampleSrc() {
    const f = state.files.find((x) => x.path && (x.status === "ready" || x.status === "done"));
    return f ? f.path : "";
  }

  async function checkDir(path, create) {
    return api("/api/checkdir", {
      method: "POST",
      body: JSON.stringify({
        mode: dirMode(), path, src: sampleSrc(), create: !!create,
      }),
    });
  }

  // 切换模式 / 打开页面时静默体检：只检查不建目录
  async function autoCheckDir() {
    if (dirMode() === "source") {
      setDirStatus("", "译文写在源文件旁边，开跑前会再检查一次");
      return;
    }
    const p = currentOutDir();
    if (!p) { markDirDirty("还没选目录"); return; }
    try {
      const r = await checkDir(p, false);
      if (r.ok) setDirStatus("ok", "✓ 目录可用 · 开跑前会再校验");
      else if (!r.exists) setDirStatus("wait", "目录还不存在，确认后会自动创建");
      else setDirStatus("err", "✗ " + (r.error || "目录不可用"));
    } catch (e) {
      setDirStatus("", "");
    }
  }

  async function browseDir() {
    if (state.dirBusy) return;
    state.dirBusy = true;
    const btn = $("#btnBrowseDir");
    btn.disabled = true;
    setDirStatus("wait", "请在弹窗里选择文件夹…");
    try {
      const r = await api("/api/pickdir", {
        method: "POST",
        body: JSON.stringify({
          initial: $("#outDir").value.trim() || currentOutDir(),
          title: "选择译文输出目录",
        }),
      });
      if (r.ok && r.path) {
        $("#outDir").value = r.path;
        $("#outDir").title = r.path;
        markDirDirty("已选择，点「确认使用此目录」生效");
      } else if (r.canceled) {
        autoCheckDir();
      } else {
        setDirStatus("err", "✗ " + (r.error || "打开对话框失败"));
        toast(r.error || "打开对话框失败", "err");
      }
    } catch (e) {
      setDirStatus("err", "✗ " + e.message);
      toast("打开对话框失败：" + e.message, "err");
    } finally {
      state.dirBusy = false;
      btn.disabled = false;
    }
  }

  async function confirmDir() {
    const p = $("#outDir").value.trim();
    if (!p) { toast("先点文件夹按钮选一个目录", "err"); return; }
    const btn = $("#btnConfirmDir");
    btn.disabled = true;
    setDirStatus("wait", "正在检查目录…");
    try {
      const r = await checkDir(p, true);
      if (!r.ok) {
        setDirStatus("err", "✗ " + (r.error || "目录不可用"));
        toast(r.error || "目录不可用", "err");
        return;
      }
      // 统一目录模式下 r.path 是「根目录/日期」这一层，别把它存成根目录
      const saved = dirMode() === "custom" ? (r.path || p) : p;
      state.cfg[dirField()] = saved;
      $("#outDir").value = saved;
      $("#outDir").title = saved;
      state.dirConfirmed = true;
      const extra = r.created ? " · 已新建" : " · 可写";
      setDirStatus("ok", "✓ 已确认" + extra);
      btn.textContent = "已确认";
      saveConfig();
      toast(r.created ? "目录已创建并确认" : "目录已确认可用", "ok");
    } catch (e) {
      setDirStatus("err", "✗ " + e.message);
      toast("检查失败：" + e.message, "err");
    } finally {
      btn.disabled = false;
    }
  }

  // 开跑前必做：目录不存在就建，建不出来/不可写就拦住
  async function ensureOutDir(s) {
    const mode = s.out_dir_mode || "app";
    if (mode === "source") return { ok: true };
    const p = mode === "custom" ? (s.custom_dir || "") : (s.out_dir || "");
    if (!p) {
      toast(mode === "custom" ? "请先选择自定义输出目录" : "请先设置输出目录", "err");
      return null;
    }
    try {
      const r = await api("/api/checkdir", {
        method: "POST",
        body: JSON.stringify({ mode, path: p, src: sampleSrc(), create: true }),
      });
      if (!r.ok) {
        setDirStatus("err", "✗ " + (r.error || "目录不可用"));
        toast("输出目录不可用：" + (r.error || ""), "err");
        return null;
      }
      // 统一目录模式下 r.path 是「根目录/日期」这一层，别把它存成根目录
      const saved = mode === "custom" ? (r.path || p) : p;
      state.cfg[dirField()] = saved;
      $("#outDir").value = saved;
      $("#outDir").title = saved;
      state.dirConfirmed = true;
      setDirStatus("ok", "✓ 已校验" + (r.created ? " · 已新建" : ""));
      return r;
    } catch (e) {
      toast("输出目录检查失败：" + e.message, "err");
      return null;
    }
  }

  function applyDirMode() {
    const mode = dirMode();
    const inp = $("#outDir");
    const show = mode !== "source";
    const box = $("#dirBox");
    if (box) box.classList.toggle("hidden", !show);
    const actions = $("#dirActions");
    if (actions) actions.classList.toggle("hidden", !show);
    inp.value = show ? currentOutDir() : "";
    inp.title = inp.value || "";
    inp.placeholder = mode === "custom" ? "D:\\译文\\2026" : "D:\\HyMT-Translator\\output";

    const hint = $("#dirHint");
    if (mode === "custom") {
      hint.textContent = "译文直接写进这个目录，不再按日期套子文件夹；目录不存在会自动创建。";
      hint.className = "dir-hint";
    } else if (mode === "source") {
      hint.textContent = "译文写在源文件旁边。从界面上传的文件会回退到统一输出目录——上传件先落在临时目录，写回去找不到。";
      hint.className = "dir-hint";
    } else {
      hint.textContent = "在这个目录里按日期建子文件夹，例如 output\\2026-11-03。";
      hint.className = "dir-hint";
    }
    updateNameHint();
    state.dirConfirmed = false;
    const cb = $("#btnConfirmDir");
    if (cb) cb.textContent = "确认使用此目录";
    autoCheckDir();
  }

  function updateNameHint() {
    const el = $("#nameHint");
    if (!el) return;
    el.className = "dir-hint";
    if ($("#renameOutput").checked) {
      el.textContent = "示例：报告.docx → 报告.译文.目标语言.docx。双文件模式下的对照稿会额外带 .对照，不会撞名。";
    } else {
      el.textContent = "示例：报告.docx → 报告.docx。同名译文会被覆盖，源文件本身不会被动。";
    }
  }

  function collectConfig() {
    const gloss = $("#glossary").value.split("\n").map((l) => l.trim()).filter(Boolean)
      .map((l) => {
        const m = l.split(/\s*=\s*|\s*=>\s*|\t/);
        return [m[0] || "", m[1] || ""];
      }).filter(([a, b]) => a && b);
    const mode = dirMode();
    const typed = $("#outDir").value.trim();
    const keep = state.cfg || {};
    return {
      src_lang: $("#srcLang").value,
      tgt_lang: $("#tgtLang").value,
      out_mode: $(".segmented button.active")?.dataset.v || "interleave",
      out_dir_mode: mode,
      out_dir: mode === "app" ? typed : (keep.out_dir || ""),
      custom_dir: mode === "custom" ? typed : (keep.custom_dir || ""),
      rename_output: $("#renameOutput").checked,
      chunk_chars: parseInt($("#chunkChars").value) || 1200,
      ctx: parseInt($("#ctx").value) || 8192,
      temperature: parseFloat($("#temperature").value) || 0.1,
      backend: $("#backend").value || "auto",
      ngl: parseInt($("#ngl").value) || 99,
      use_context: $("#useContext").checked,
      glossary: gloss,
    };
  }

  let saveTimer = null;
  function saveConfig() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      const cfg = collectConfig();
      state.cfg = { ...state.cfg, ...cfg };
      $("#glossCount").textContent = cfg.glossary.length;
      $("#swapHint").textContent = hintText();
      try { await api("/api/config", { method: "POST", body: JSON.stringify(cfg) }); } catch (e) { }
    }, 350);
  }

  function hintText() {
    const s = $("#srcLang").value, t = $("#tgtLang").value;
    const nm = (c) => LANG_NAMES[c] || c;
    if (s === "auto") {
      return CN_FAMILY.has(t)
        ? `自动识别 → ${nm(t)}，中文指令模板`
        : `自动识别 → ${nm(t)}，外↔外走英文指令模板`;
    }
    const cnSide = CN_FAMILY.has(s) || CN_FAMILY.has(t);
    return `${nm(s)} → ${nm(t)}，${cnSide ? "中文" : "英文"}指令模板`;
  }

  function setMode(v, save = true) {
    $$("#outMode button").forEach((b) => b.classList.toggle("active", b.dataset.v === v));
    $("#modeHint").textContent = MODE_HINT[v] || "";
    if (save) saveConfig();
  }

  /* ---------------- 引擎状态 ---------------- */
  // GPU 负载走势：保留最近 60 次采样，画在顶栏的小图里
  const SPARK_N = 60;
  const gpuHist = [];
  const num = (v) => (v === null || v === undefined || isNaN(v) ? null : Number(v));

  function pushSpark(util, watt) {
    gpuHist.push({ u: util == null ? 0 : util, w: watt });
    while (gpuHist.length > SPARK_N) gpuHist.shift();
    if (gpuHist.length < 2) drawSparkFlat();
    else drawSpark();
  }

  function sparkCanvas() {
    const c = $("#gpuSpark");
    if (!c) return null;
    const W = 46, H = 14;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(W * dpr)) {
      c.width = Math.round(W * dpr);
      c.height = Math.round(H * dpr);
    }
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    // 0% 基线：低负载时曲线贴着底边，有这条线才看得出是图表
    ctx.beginPath();
    ctx.moveTo(0, H - 0.5);
    ctx.lineTo(W, H - 0.5);
    ctx.strokeStyle = "rgba(255,255,255,.10)";
    ctx.lineWidth = 1;
    ctx.stroke();
    return { ctx, W, H };
  }

  const sparkY = (v, H) => H - 1.5 - (Math.min(100, Math.max(0, v)) / 100) * (H - 3);

  function drawSpark() {
    if (gpuHist.length < 2) return;
    const cv = sparkCanvas();
    if (!cv) return;
    const { ctx, W, H } = cv;
    // 采样不足时先铺满整宽，攒够 60 个点后自然变成滚动走势
    const step = W / Math.max(1, Math.min(SPARK_N, gpuHist.length) - 1);
    const pts = gpuHist.map((p, i) => [i * step, sparkY(p.u, H)]);

    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.strokeStyle = "rgba(45,212,191,.95)";
    ctx.lineWidth = 1.4;
    ctx.lineJoin = "round";
    ctx.stroke();

    ctx.lineTo(pts[pts.length - 1][0], H);
    ctx.lineTo(pts[0][0], H);
    ctx.closePath();
    ctx.fillStyle = "rgba(45,212,191,.18)";
    ctx.fill();
  }

  // 只有一个采样点时画一条横线，避免出现空白小方块
  function drawSparkFlat() {
    if (!gpuHist.length) return;
    const cv = sparkCanvas();
    if (!cv) return;
    const { ctx, W, H } = cv;
    const y = sparkY(gpuHist[0].u, H);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(W, y);
    ctx.strokeStyle = "rgba(45,212,191,.9)";
    ctx.lineWidth = 1.4;
    ctx.stroke();
  }

  function updateGpu(e) {
    const util = num(e.gpu_util);
    const vram = num(e.vram_used);
    const total = num(e.vram_total);
    const watt = num(e.gpu_power);
    const limit = num(e.gpu_power_limit);
    const clk = num(e.gpu_clock);
    const temp = num(e.gpu_temp);

    if (vram !== null && total) {
      const txt = `显存 ${(vram / 1024).toFixed(1)} / ${(total / 1024).toFixed(0)} GB`;
      $("#pillVram").textContent = txt;
      $("#sbVram").textContent = txt;
    }
    if (util === null) {
      $("#pillGpuText").textContent = "GPU —";
      $("#sbGpu").textContent = "GPU —";
      return;
    }

    $("#pillGpuText").textContent = `GPU ${Math.round(util)}%` + (watt !== null ? ` · ${Math.round(watt)}W` : "");
    $("#pillGpu").classList.toggle("hot", util >= 85 || (temp !== null && temp >= 78));

    const bits = [`GPU ${Math.round(util)}%`];
    if (watt !== null) bits.push(`${watt.toFixed(0)}${limit ? "/" + Math.round(limit) : ""} W`);
    if (clk !== null) bits.push(`${(clk / 1000).toFixed(2)} GHz`);
    $("#sbGpu").textContent = bits.join(" · ");

    const tip = [`负载 ${Math.round(util)}%`];
    if (watt !== null) tip.push(`功耗 ${watt.toFixed(1)}${limit ? " / " + Math.round(limit) : ""} W`);
    if (clk !== null) tip.push(`频率 ${(clk / 1000).toFixed(2)} GHz`);
    if (temp !== null) tip.push(`温度 ${Math.round(temp)} ℃`);
    if (vram !== null && total) tip.push(`显存 ${(vram / 1024).toFixed(1)} / ${(total / 1024).toFixed(0)} GB`);
    const tipTxt = tip.join(" · ");
    $("#sbGpu").title = tipTxt;
    $("#pillGpu").title = tipTxt;

    pushSpark(util, watt);
  }

  function updateEngine(e) {
    if (!e) return;
    const badge = $("#engineBadge");
    badge.className = "engine-badge " + (e.state === "ready" ? "ready" : e.state === "loading" ? "loading" : e.state === "error" ? "error" : "");
    $("#engineText").textContent = e.state === "ready" ? "模型就绪" : e.state === "loading" ? "加载中…" : e.state === "error" ? "引擎异常" : "未启动";
    $("#pillModel").textContent = e.model || "—";
    $("#pillModel").title = e.model || "";
    $("#sbModel").textContent = e.model || "—";
    if (e.backend) {
      const map = { cuda: "CUDA", vulkan: "Vulkan", cpu: "CPU" };
      const name = map[e.backend] || e.backend;
      $("#pillBackend").textContent = name;
      $("#pillBackend").title = e.backend === "cuda"
        ? "CUDA 后端，生成速度约 70 tok/s"
        : e.backend === "vulkan"
          ? "Vulkan 后端，生成速度约 12 tok/s（比 CUDA 慢约 6 倍）"
          : "CPU 推理，速度很慢";
      $("#pillBackend").className = "stat-pill" + (e.backend === "cuda" ? " accel" : "");
      $("#sbBackend").textContent = name;
    }
    updateGpu(e);
    $("#sbTps").textContent = (e.last_tps || 0) + " tok/s";
    if (e.log && e.log.length) $("#sbLog").textContent = e.log[e.log.length - 1];
    if (e.state === "loading" && e.message) $("#engineText").textContent = "加载中";
  }

  async function pollEngine() {
    try {
      const d = await api("/api/state");
      updateEngine(d.engine);
    } catch (e) { }
  }

  /* ---------------- 上传 ---------------- */
  const SUPPORT_RE = /\.(txt|md|srt|lrc|docx|pdf|epub)$/i;
  const MAX_IMPORT = 400;          // 单次导入上限，防止误拖整个盘符
  const MAX_DEPTH = 12;            // 文件夹递归层数上限
  const UPLOAD_CONCURRENCY = 3;    // 同时上传几个文件

  // 值不值得上传：扩展名要对，还要排掉 Office 锁文件和系统垃圾
  function wanted(name) {
    if (!SUPPORT_RE.test(name)) return false;
    if (name.startsWith("~$")) return false;      // Word 打开文档时生成的临时锁文件
    if (name.startsWith("._")) return false;      // macOS 资源分叉文件
    if (/^(thumbs\.db|desktop\.ini|\.ds_store)$/i.test(name)) return false;
    return true;
  }

  // 拖进来的可能是文件夹：把 DataTransfer 完整展开成 [{file, rel}]
  async function collectDrop(dt) {
    const items = dt.items ? Array.from(dt.items) : [];
    const flat = Array.from(dt.files || []);   // 必须在第一个 await 前取走，之后会被清空
    const entries = [];
    for (const it of items) {
      if (it.kind !== "file") continue;
      const ent = it.webkitGetAsEntry ? it.webkitGetAsEntry() : null;
      if (ent) entries.push(ent);
    }
    if (!entries.length) return flat.map((f) => ({ file: f, rel: f.name }));
    const out = [];
    for (const ent of entries) {
      await walkEntry(ent, "", out, 0);
      if (out.length >= MAX_IMPORT) break;
    }
    return out;
  }

  async function walkEntry(entry, base, out, depth) {
    if (out.length >= MAX_IMPORT || depth > MAX_DEPTH) return;
    if (entry.isFile) {
      try {
        const file = await new Promise((res, rej) => entry.file(res, rej));
        if (wanted(file.name)) out.push({ file, rel: base + file.name });
      } catch (e) { /* 单个文件读不出来就跳过，不影响其余 */ }
      return;
    }
    if (!entry.isDirectory) return;
    const reader = entry.createReader();
    const prefix = base + entry.name + "/";
    // readEntries 一次最多返回 100 条，必须一直读到空数组，否则大文件夹会漏文件
    for (;;) {
      let batch = [];
      try {
        batch = await new Promise((res, rej) => reader.readEntries(res, rej));
      } catch (e) { break; }
      if (!batch.length) break;
      for (const e of batch) {
        await walkEntry(e, prefix, out, depth + 1);
        if (out.length >= MAX_IMPORT) return;
      }
    }
  }

  function addFiles(list) {
    addPairs(Array.from(list || []).map((f) => ({ file: f, rel: f.name })));
  }

  // 队列里只显示最后一级目录，完整相对路径放 title
  function relDir(rel, name) {
    if (!rel || rel === name) return "";
    const cut = rel.lastIndexOf("/");
    return cut > 0 ? rel.slice(0, cut) : "";
  }

  function addPairs(pairs) {
    pairs = (pairs || []).filter((p) => p.file && wanted(p.file.name));
    if (!pairs.length) {
      toast("没有可识别的文件（支持 txt / docx / pdf / epub / srt / lrc）", "err");
      return;
    }
    if (pairs.length > MAX_IMPORT) toast(`文件太多，只导入前 ${MAX_IMPORT} 个`, "warn");
    const use = pairs.slice(0, MAX_IMPORT);
    const dirs = new Set();
    use.forEach((p) => {
      const dir = relDir(p.rel, p.file.name);
      if (dir) dirs.add(dir);
      const item = {
        name: p.file.name, rel: p.rel || p.file.name, dir,
        size: p.file.size, ext: "." + p.file.name.split(".").pop().toLowerCase(),
        status: "uploading", progress: 0, path: "", stage: "排队上传", note: "",
        outputs: [], error: "", eta: 0, chars: 0, tps: 0, elapsed: 0,
      };
      state.files.push(item);
      queueUpload(p.file, item);
    });
    renderQueue();
    updateStartBtn();
    if (dirs.size) {
      toast(`已导入 ${use.length} 个文件（来自 ${dirs.size > 1 ? dirs.size + " 个目录" : "文件夹"}）`, "ok");
    }
  }

  /* ---------------- 文件夹导入（走本地路径，不重复上传） ---------------- */
  const fp = { path: "", files: [], recursive: true, busy: false };

  function showFolderPanel(path, msg, kind, canConfirm) {
    const box = $("#folderPanel");
    if (!box) return;
    box.classList.remove("hidden");
    const p = $("#fpPath");
    p.textContent = path || "—";
    p.title = path || "";
    const meta = $("#fpMeta");
    meta.textContent = msg || "";
    meta.className = "fp-meta" + (kind ? " " + kind : "");
    const ok = $("#btnFpConfirm");
    ok.disabled = !canConfirm;
    ok.textContent = canConfirm ? `确认导入 ${fp.files.length} 个文件` : "确认导入";
  }

  function hideFolderPanel() {
    const box = $("#folderPanel");
    if (box) box.classList.add("hidden");
    fp.files = [];
  }

  function folderSummary(r) {
    const bits = [`找到 ${r.count} 个可翻译文件`];
    if (r.unsupported) bits.push(`跳过 ${r.unsupported} 个不支持的格式`);
    if (r.empty) bits.push(`${r.empty} 个空文件`);
    if (r.truncated) bits.push(`数量太多，只取前 ${r.count} 个`);
    return bits.join(" · ");
  }

  // 选完目录先扫一遍，把清单和数量摆出来，等用户按「确认导入」再进队列
  async function scanFolder(path, recursive) {
    fp.path = path || "";
    fp.recursive = !!recursive;
    fp.files = [];
    showFolderPanel(path, "正在扫描文件夹…", "wait", false);
    try {
      const r = await api("/api/scan_dir", {
        method: "POST",
        body: JSON.stringify({ path: path, recursive: fp.recursive, limit: MAX_IMPORT }),
      });
      if (!r.ok) {
        showFolderPanel(path, r.error || "扫描失败", "err", false);
        return;
      }
      const root = r.root || "";
      fp.files = (r.files || []).map((f) => ({ ...f, rel: root ? root + "/" + f.rel : f.rel }));
      showFolderPanel(r.path || path, folderSummary(r), "ok", true);
    } catch (e) {
      showFolderPanel(path, "扫描失败：" + e.message, "err", false);
    }
  }

  // 弹系统「选择文件夹」框；万一系统框调不出来，退回浏览器自带的目录选择
  async function browseImportFolder() {
    if (fp.busy) return;
    fp.busy = true;
    const btn = $("#btnPickDir");
    const old = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "请在弹窗中选择…"; }
    try {
      let r = null;
      try {
        r = await api("/api/pickdir", {
          method: "POST",
          body: JSON.stringify({ initial: fp.path || "", title: "选择要翻译的文件夹" }),
        });
      } catch (e) {
        r = null;
      }
      if (!r) { $("#dirInput").click(); return; }
      if (!r.ok) {
        if (!r.canceled) toast("打开文件夹选择框失败：" + (r.error || "未知原因"), "err");
        return;
      }
      await scanFolder(r.path, fp.recursive);
    } finally {
      fp.busy = false;
      if (btn) { btn.disabled = false; btn.textContent = old; }
    }
  }

  function addLocalFiles(list) {
    const have = new Set(state.files.map((f) => f.path).filter(Boolean));
    const all = list || [];
    const fresh = all.filter((p) => p.path && !have.has(p.path) && wanted(p.name));
    const dup = all.length - fresh.length;
    if (!fresh.length) {
      toast(dup ? "这些文件已经在队列里了" : "没有可导入的文件", "warn");
      return;
    }
    fresh.forEach((p) => {
      state.files.push({
        name: p.name, rel: p.rel || p.name, dir: relDir(p.rel || p.name, p.name),
        size: p.size, ext: p.ext || "." + p.name.split(".").pop().toLowerCase(),
        status: "ready", progress: 0, path: p.path, stage: "待翻译", note: "",
        outputs: [], error: "", eta: 0, chars: 0, tps: 0, elapsed: 0, avg_tps: 0, local: true,
      });
    });
    renderQueue();
    updateStartBtn();
    toast(`已导入 ${fresh.length} 个文件` + (dup ? `（${dup} 个重复已忽略）` : ""), "ok");
  }

  // 上传池：文件夹一次可能几百个文件，全并发会把浏览器和本地服务压死
  const upQueue = [];
  let upActive = 0;

  function queueUpload(file, item) {
    upQueue.push([file, item]);
    pumpUpload();
  }

  function pumpUpload() {
    while (upActive < UPLOAD_CONCURRENCY && upQueue.length) {
      const [file, item] = upQueue.shift();
      upActive++;
      uploadOne(file, item);
    }
  }

  // 清空队列时丢掉还没轮到的上传，已经在传的会自己跑完
  function dropUploadQueue() {
    upQueue.length = 0;
  }

  function uploadOne(file, item) {
    const finish = () => {
      upActive = Math.max(0, upActive - 1);
      renderQueue();
      updateStartBtn();
      pumpUpload();
    };
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload?name=" + encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        item.progress = e.loaded / e.total;
        item.stage = "上传中 " + Math.round(item.progress * 100) + "%";
        renderQueue();
      }
    };
    xhr.onload = () => {
      try {
        const d = JSON.parse(xhr.responseText);
        if (!d.supported) {
          item.status = "error";
          item.error = d.reason || "不支持的格式";
          item.stage = "跳过";
        } else {
          item.status = "ready"; item.path = d.path; item.stage = "待翻译"; item.progress = 0;
        }
      } catch (e) {
        item.status = "error"; item.error = "上传失败"; item.stage = "失败";
      }
      finish();
    };
    xhr.onerror = () => {
      item.status = "error"; item.error = "网络错误"; item.stage = "失败";
      finish();
    };
    try {
      xhr.send(file);
    } catch (e) {
      item.status = "error"; item.error = "上传失败"; item.stage = "失败";
      finish();
    }
  }

  /* ---------------- 队列渲染 ---------------- */
  function renderQueue() {
    const box = $("#queue");
    const has = state.files.length > 0;
    $("#queueEmpty").classList.toggle("hidden", has);
    const done = state.files.filter((f) => f.status === "done").length;
    $("#queueCount").textContent = `${state.files.length} 个文件` + (done ? ` · 已完成 ${done}` : "");

    $$(".item").forEach((n) => n.remove());
    state.files.forEach((f, i) => {
      const el = document.createElement("div");
      el.className = "item" + (f.status === "done" ? " done" : f.status === "error" ? " error"
        : f.status === "canceled" ? " canceled" : "");
      const pct = Math.round((f.progress || 0) * 100);
      const meta = [];
      if (f.dir) meta.push("📁 " + f.dir.split("/").slice(-1)[0]);
      if (f.size) meta.push(fmtSize(f.size));
      if (f.chars) meta.push(fmtChars(f.chars));
      if (f.status === "running" && f.tps) meta.push(f.tps + " tok/s");
      else if (f.avg_tps) meta.push("平均 " + f.avg_tps + " tok/s");
      else if (f.tps) meta.push(f.tps + " tok/s");
      if (f.elapsed) meta.push("用时 " + fmtDur(f.elapsed));

      const st = stageInfo(f);
      const etaLive = f.status === "running" && f.eta > 0
        ? Math.max(0, f.eta - (Date.now() - (f.etaAt || Date.now())) / 1000)
        : 0;
      const etaTxt = etaLive > 0 ? `剩余约 ${fmtDur(etaLive)}` : "";
      const canOut = (f.status === "done" || f.status === "canceled") && (f.outputs || []).length > 0;

      el.innerHTML = `
        <div class="ext ${EXT_CLASS[f.ext] || ""}">${(f.ext || "").replace(".", "").toUpperCase()}</div>
        <div class="item-main">
          <div class="item-name" title="${f.rel || f.name}">${f.name}</div>
          <div class="item-sub">
            <span>${meta.join(" · ") || "—"}</span>
            ${f.error ? `<span style="color:var(--bad)">${f.error}</span>` : ""}
          </div>
          <div class="item-stage">
            <span class="stg">${st.stg}</span>
            ${st.note ? `<span class="note">${st.note}</span>` : ""}
            ${etaTxt ? `<span class="eta">${etaTxt}</span>` : ""}
          </div>
          <div class="item-bar"><div style="width:${f.status === "done" ? 100 : pct}%"></div></div>
        </div>
        <div class="item-actions">
          <span class="chip ${f.status}">${statusText(f)}</span>
          ${canOut ? `<button class="icon-btn" data-act="open" data-i="${i}" title="${f.status === "canceled" ? "打开已保存的部分译文" : "打开译文"}">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5"/></svg>
            </button>` : ""}
          ${canOut ? `<button class="icon-btn" data-act="peek" data-i="${i}" title="预览">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="2.5"/></svg>
            </button>` : ""}
          ${f.status !== "running" ? `<button class="icon-btn" data-act="del" data-i="${i}" title="移除">✕</button>` : ""}
        </div>`;
      box.appendChild(el);
    });
    updateStartBtn();
    updateOverall();
  }

  function statusText(f) {
    if (f.status === "running") return Math.round((f.progress || 0) * 100) + "%";
    if (f.status === "done") return "完成";
    if (f.status === "error") return "失败";
    if (f.status === "canceled") return "已停止";
    if (f.status === "uploading") return "上传中";
    return f.stage || "待翻译";
  }

  function updateStartBtn() {
    const n = state.files.filter((f) => f.status === "ready").length;
    $("#btnStart").disabled = state.running || n === 0;
    $("#btnStart").innerHTML = state.running ? "翻译中…" : `<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M8 5l11 7-11 7z"/></svg>开始翻译（${n}）`;
  }

  // 剩余总时间：当前文件的 ETA + 后面排队的文件按平均耗时估算
  function totalEta() {
    const cur = state.files.find((f) => f.status === "running");
    if (!cur) return 0;
    let eta = cur.eta > 0 ? Math.max(0, cur.eta - (Date.now() - (cur.etaAt || Date.now())) / 1000) : 0;
    const queued = state.files.filter((f) => f.status === "ready").length;
    if (queued) {
      const times = state.files.filter((f) => f.status === "done" && f.elapsed > 0).map((f) => f.elapsed);
      const avg = times.length ? times.reduce((a, b) => a + b, 0) / times.length : (cur.elapsed || 0);
      eta += avg * queued;
    }
    return eta;
  }

  function updateOverall() {
    const show = state.running || state.files.some((f) => f.status === "done" || f.status === "canceled");
    $("#overall").classList.toggle("hidden", !show);
    if (!show) return;

    const counted = state.files.filter((f) => f.status !== "uploading");
    const total = counted.length || 1;
    const prog = state.files.reduce((a, f) => a + (f.status === "done" ? 1 : f.progress || 0), 0) / total;
    $("#overallBar").style.width = Math.min(100, prog * 100) + "%";

    const doneCount = state.files.filter((f) => f.status === "done").length;
    const errCount = state.files.filter((f) => f.status === "error").length;
    const cancelCount = state.files.filter((f) => f.status === "canceled").length;
    const cur = state.files.find((f) => f.status === "running");

    if (state.running) {
      const seq = Math.min(total, doneCount + cancelCount + errCount + 1);
      $("#overallText").textContent = cur
        ? `正在翻译 ${seq}/${total} · ${cur.name}`
        : "正在翻译…";
    } else if (cancelCount) {
      $("#overallText").textContent = `已停止：完成 ${doneCount}，停止 ${cancelCount}` + (errCount ? `，失败 ${errCount}` : "");
    } else {
      $("#overallText").textContent = errCount ? `完成 ${doneCount}，失败 ${errCount}` : `已全部完成 ${doneCount}`;
    }

    const chars = state.files.reduce((a, f) => a + (f.chars || 0), 0);
    const tps = state.files.filter((f) => f.tps).slice(-1)[0]?.tps || 0;
    const t = state.totals || {};
    const avg = (!state.running && t.tokens && t.gen_sec > 1)
      ? Math.round((t.tokens / t.gen_sec) * 10) / 10 : 0;
    const bits = [];
    if (chars) bits.push(fmtChars(chars));
    if (avg) bits.push(`平均 ${avg} tok/s`);
    else if (tps) bits.push(tps + " tok/s");
    if (avg && t.tokens) bits.push(`${t.tokens.toLocaleString()} tokens`);
    $("#overallStats").textContent = bits.join(" · ");

    // 小字：当前做到哪一步 + 剩余时间
    let note = "";
    if (state.running) {
      if (cur) {
        const st = stageInfo(cur);
        note = st.note ? `${st.stg} · ${st.note}` : st.stg;
        const eta = totalEta();
        if (eta > 0) note += `　剩余约 ${fmtDur(eta)}`;
      } else {
        note = state.engineNote || "正在准备…";
      }
    }
    $("#overallNote").textContent = note;
  }

  /* ---------------- 任务 ---------------- */
  async function startJob() {
    const ready = state.files.filter((f) => f.status === "ready");
    if (!ready.length) {
      const bad = state.files.filter((f) => f.status === "error");
      toast(bad.length ? `无法翻译：${bad[0].error}` : "队列里没有待翻译的文件", "err");
      return;
    }
    const s = collectConfig();
    const dir = await ensureOutDir(s);
    if (!dir) return;
    await api("/api/config", { method: "POST", body: JSON.stringify(s) });
    const idx = [];
    state.files.forEach((f, i) => { if (f.status === "ready") idx.push(i); });
    try {
      const r = await api("/api/jobs", {
        method: "POST",
        body: JSON.stringify({ paths: ready.map((f) => f.path), settings: s }),
      });
      state.job = r.job;
      state.mapping = idx;
      state.running = true;
      state.totals = null;
      ready.forEach((f) => { f.status = "running"; f.progress = 0; f.stage = "排队中"; f.avg_tps = 0; f.tps = 0; });
      renderQueue();
      $("#btnCancel").classList.remove("hidden");
      $("#btnCancel").disabled = false;
      $("#btnCancel").textContent = "停止";
      openStream(r.job);
      toast(`已提交 ${r.count} 个文件，开始翻译`);
    } catch (e) {
      toast("提交失败：" + e.message, "err");
    }
  }

  function openStream(jobId) {
    if (state.es) state.es.close();
    const es = new EventSource("/api/events?job=" + jobId);
    state.es = es;
    es.onmessage = (ev) => {
      let d;
      try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (d.type === "file" && typeof d.idx === "number") {
        const qi = state.mapping[d.idx];
        const f = state.files[qi];
        if (!f) return;
        ["status", "progress", "stage", "note", "chars", "tps", "avg_tps", "elapsed", "error", "outputs"].forEach((k) => {
          if (d[k] !== undefined) f[k] = d[k];
        });
        if (d.eta !== undefined) { f.eta = d.eta; f.etaAt = Date.now(); }
        if (d.status === "done") { f.progress = 1; toast(`${f.name} 翻译完成`, "ok"); }
        if (d.status === "canceled" && (f.outputs || []).length) {
          toast(`${f.name} 已停止，部分译文已保存`, "warn");
        }
        renderQueue();
      } else if (d.type === "engine") {
        $("#engineText").textContent = d.state === "ready" ? "模型就绪" : (d.message || "加载中…");
        state.engineNote = d.note || (d.state === "loading" ? "正在加载模型到显存…" : "");
        if (d.state === "ready") state.engineNote = "";
        updateOverall();
      } else if (d.type === "log") {
        $("#sbLog").textContent = d.message;
      } else if (d.type === "error") {
        toast(d.message, "err");
        $("#sbLog").textContent = d.message;
      } else if (d.type === "job") {
        if (d.totals) state.totals = d.totals;
        if (d.status !== "running") finishJob(d.status);
      }
    };
    es.addEventListener("end", () => finishJob("done"));
    es.onerror = () => { if (!state.running) es.close(); };
  }

  async function finishJob(status) {
    state.running = false;
    state.engineNote = "";
    $("#btnCancel").classList.add("hidden");
    state.files.forEach((f) => {
      if (f.status === "running") {
        f.status = status === "canceled" ? "canceled" : "done";
        f.eta = 0;
        if (f.status === "canceled" && !f.note) f.note = "已停止";
      }
    });
    if (state.es) { state.es.close(); state.es = null; }
    renderQueue();
    pollEngine();
    loadFiles();
    if (status === "done") toast("全部任务已完成", "ok");
    else if (status === "canceled") {
      const saved = state.files.filter((f) => f.status === "canceled" && (f.outputs || []).length).length;
      toast(saved ? `已停止，${saved} 个文件的部分译文已保存` : "已停止", "warn");
    }
    // 兜底：SSE 断线没收到汇总时补拉一次，保证平均 token 速度能显示出来
    if (!state.totals && state.job) {
      try {
        const d = await api("/api/jobs?job=" + state.job);
        if (d.totals) { state.totals = d.totals; updateOverall(); }
      } catch (e) { }
    }
  }

  async function cancelJob() {
    if (!state.job) return;
    $("#btnCancel").disabled = true;
    try { await api("/api/jobs/cancel", { method: "POST", body: JSON.stringify({ job: state.job }) }); } catch (e) { }
    $("#btnCancel").textContent = "停止中…";
    toast("已发送停止指令：当前这段译完就停，已翻好的部分会自动保存");
  }

  /* ---------------- 试译 ---------------- */
  async function tryTranslate() {
    const text = $("#tryInput").value.trim();
    if (!text) return;
    const out = $("#tryOutput");
    out.innerHTML = "";
    $("#btnTry").disabled = true;
    $("#tryMeta").textContent = "";
    const t0 = performance.now();
    let acc = "";
    try {
      const res = await fetch("/api/test", {
        method: "POST",
        body: JSON.stringify({ text, tgt: $("#tgtLang").value, src: $("#srcLang").value }),
      });
      if (!res.body) throw new Error("无流式响应");
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() || "";
        for (const p of parts) {
          const line = p.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          let d; try { d = JSON.parse(line.slice(6)); } catch (e) { continue; }
          if (d.e) throw new Error(d.e);
          if (d.d) { acc += d.d; out.textContent = acc; out.scrollTop = out.scrollHeight; }
          if (d.done) {
            const sec = ((performance.now() - t0) / 1000).toFixed(1);
            $("#tryMeta").textContent = `${acc.length} 字 · ${sec}s`;
          }
        }
      }
    } catch (e) {
      out.innerHTML = `<span style="color:var(--bad)">${e.message}</span>`;
    } finally {
      $("#btnTry").disabled = false;
    }
  }

  /* ---------------- 输出文件 ---------------- */
  // 在资源管理器里定位文件：选中高亮 + 窗口压到浏览器之上
  async function revealPath(p) {
    if (!p) return;
    try {
      const r = await api("/api/open", { method: "POST", body: JSON.stringify({ path: p, reveal: true }) });
      toast(r.hinted ? "已在文件夹中定位并高亮" : "已打开所在文件夹", "ok");
    } catch (e) {
      toast("打开失败：" + e.message, "err");
    }
  }

  async function loadFiles() {
    try {
      const d = await api("/api/list_output");
      $("#filesBase").textContent = "输出目录：" + d.base;
      const box = $("#filelist");
      box.innerHTML = "";
      if (!d.items.length) {
        box.innerHTML = `<div class="empty"><p>还没有译文</p><span>翻译完成的文件会出现在这里</span></div>`;
        return;
      }
      d.items.forEach((it) => {
        const el = document.createElement("div");
        el.className = "file-row";
        el.innerHTML = `<div><div class="fr-name">${it.name}</div>
          <div class="fr-meta">${it.mtime} · ${fmtSize(it.size)}</div></div>
          <div class="item-actions">
            <button class="icon-btn" data-open="${it.path}" title="打开文件">↗</button>
            <button class="icon-btn" data-reveal="${it.path}" title="在文件夹中定位并高亮">📁</button>
          </div>`;
        box.appendChild(el);
      });
    } catch (e) { }
  }

  /* ---------------- 事件绑定 ---------------- */
  function bind() {
    const dz = $("#dropzone");
    dz.addEventListener("click", () => $("#fileInput").click());
    $("#btnPickFiles").addEventListener("click", (e) => {
      e.stopPropagation();          // 别让冒泡触发 dropzone 的「选文件」
      $("#fileInput").click();
    });
    // 「选择文件夹」= 选目录 → 扫描 → 确认导入，整条链路一步到位
    $("#btnPickDir").addEventListener("click", (e) => {
      e.stopPropagation();
      browseImportFolder();
    });
    $("#btnFpConfirm").addEventListener("click", () => {
      if (!fp.files.length) return;
      addLocalFiles(fp.files);
      hideFolderPanel();
    });
    ["#btnFpCancel", "#btnFpClose"].forEach((sel) => {
      const b = $(sel);
      if (b) b.addEventListener("click", hideFolderPanel);
    });
    $("#fpRecursive").addEventListener("change", (e) => {
      fp.recursive = e.target.checked;
      if (fp.path) scanFolder(fp.path, fp.recursive);
    });
    $("#fileInput").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
    $("#dirInput").addEventListener("change", (e) => {
      const arr = Array.from(e.target.files || []);
      const pairs = arr.filter((f) => wanted(f.name))
        .map((f) => ({ file: f, rel: f.webkitRelativePath || f.name }));
      e.target.value = "";
      if (!pairs.length) {
        toast(arr.length ? `扫描了 ${arr.length} 个文件，没有能识别的格式` : "这个文件夹是空的", "err");
        return;
      }
      addPairs(pairs);
    });
    ["dragenter", "dragover"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
    dz.addEventListener("drop", async (e) => {
      const dt = e.dataTransfer;
      if (!dt) return;
      const title = $("#dzTitle");
      const old = title ? title.textContent : "";
      if (title) title.textContent = "正在读取…";
      dz.classList.add("scanning");
      try {
        const pairs = await collectDrop(dt);
        if (!pairs.length) toast("没有可识别的文件（支持 txt / docx / pdf / epub / srt / lrc）", "err");
        else addPairs(pairs);
      } catch (err) {
        toast("读取拖入内容失败，试试用「选择文件夹」按钮", "err");
      } finally {
        if (title) title.textContent = old;
        dz.classList.remove("scanning");
      }
    });
    document.addEventListener("dragover", (e) => e.preventDefault());
    document.addEventListener("drop", (e) => e.preventDefault());

    $("#btnStart").addEventListener("click", startJob);
    $("#btnCancel").addEventListener("click", cancelJob);
    $("#btnClear").addEventListener("click", () => {
      if (state.running) { toast("翻译进行中，先停止再清空", "err"); return; }
      dropUploadQueue();
      state.files = []; renderQueue();
    });

    $("#queue").addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-act]");
      if (!btn) return;
      const i = parseInt(btn.dataset.i);
      const f = state.files[i];
      if (!f) return;
      if (btn.dataset.act === "del") {
        state.files.splice(i, 1); renderQueue();
      } else if (btn.dataset.act === "open") {
        const p = (f.outputs || [])[0];
        if (p) revealPath(p);
      } else if (btn.dataset.act === "peek") {
        const p = (f.outputs || [])[0];
        if (!p) return;
        try {
          const d = await api("/api/preview?path=" + encodeURIComponent(p));
          $("#previewTitle").textContent = d.name;
          $("#previewBody").textContent = d.text + (d.truncated ? "\n\n…（已截断）" : "");
          $("#previewModal").classList.remove("hidden");
        } catch (err) { toast("预览失败：" + err.message, "err"); }
      }
    });

    $("#previewClose").addEventListener("click", () => $("#previewModal").classList.add("hidden"));
    $("#previewModal").addEventListener("click", (e) => { if (e.target.id === "previewModal") $("#previewModal").classList.add("hidden"); });

    $$("#outMode button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.v)));

    $("#outDirMode").addEventListener("change", () => applyDirMode());
    $("#outDir").addEventListener("input", () => markDirDirty("已修改，点「确认使用此目录」"));
    $("#btnBrowseDir").addEventListener("click", browseDir);
    $("#btnConfirmDir").addEventListener("click", confirmDir);
    $("#renameOutput").addEventListener("change", updateNameHint);

    ["#srcLang", "#tgtLang", "#outDirMode", "#chunkChars", "#ctx", "#temperature", "#backend", "#ngl", "#useContext", "#glossary"]
      .forEach((sel) => {
        const el = $(sel);
        el.addEventListener("change", () => { saveConfig(); $("#swapHint").textContent = hintText(); });
        if (el.tagName === "TEXTAREA" || el.type === "text") el.addEventListener("input", () => saveConfig());
      });
    $("#backend").addEventListener("change", () => {
      toast("后端已切换，下一次翻译会重新加载模型", "ok");
      pollEngine();
    });

    const openOutDir = () => {
      // 源文件同目录模式下输入框是空的，回退到统一输出目录
      const p = dirMode() === "source" ? (state.cfg.out_dir || "") : currentOutDir();
      if (p) revealPath(p);
    };
    $("#btnOpenOut").addEventListener("click", openOutDir);
    $("#btnOpenOutDir").addEventListener("click", openOutDir);
    $("#btnRefreshFiles").addEventListener("click", loadFiles);
    $("#filelist").addEventListener("click", (e) => {
      const o = e.target.closest("[data-open]");
      const r = e.target.closest("[data-reveal]");
      if (o) api("/api/open", { method: "POST", body: JSON.stringify({ path: o.dataset.open }) });
      if (r) revealPath(r.dataset.reveal);
    });

    $("#advToggle").addEventListener("click", () => $("#advToggle").closest(".card").classList.toggle("collapsed"));

    $$(".tab").forEach((t) => t.addEventListener("click", () => {
      $$(".tab").forEach((x) => x.classList.toggle("active", x === t));
      $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + t.dataset.tab));
      if (t.dataset.tab === "files") loadFiles();
    }));

    $("#btnTry").addEventListener("click", tryTranslate);
    $("#tryInput").addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") tryTranslate();
    });
    $("#btnCopy").addEventListener("click", () => {
      navigator.clipboard.writeText($("#tryOutput").textContent || "").then(() => toast("已复制", "ok"));
    });

    $("#btnStopEngine").addEventListener("click", async () => {
      await api("/api/engine/stop", { method: "POST" });
      toast("已释放显存，下次翻译会自动重新加载", "ok");
      pollEngine();
    });
    $("#btnQuit").addEventListener("click", async () => {
      if (!confirm("退出后模型与界面都会关闭，确认？")) return;
      await api("/api/quit", { method: "POST" });
      document.body.innerHTML = `<div style="display:grid;place-items:center;height:100vh;color:#8b93a3;font-size:14px">已退出，可关闭此页面</div>`;
    });

    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "o") { e.preventDefault(); $("#fileInput").click(); }
      if (e.key === "Escape") $("#previewModal").classList.add("hidden");
    });
  }

  /* ---------------- 启动 ---------------- */
  bind();
  loadState();
  setInterval(pollEngine, 2000);
  // 每秒重绘一次，让「剩余时间」跟着走
  setInterval(() => { if (state.running) { renderQueue(); updateOverall(); } }, 1000);
})();
