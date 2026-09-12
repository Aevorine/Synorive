#!/usr/bin/env node
/**
 * 把一份完整的 Python 运行时塞进安装包 —— 「独立可执行文件」的落地
 * ====================================================================
 *
 * ## 治的什么病
 *
 * 打包版原来的链路是：装完 → 首次运行 → 在系统里找 Python → 建 venv → pip install。
 * 这条链路有三个地方会断，而且**每一处断掉的表现都是同一句「引擎启动失败」**：
 *   ① 用户机器上根本没有 Python（最常见）
 *   ② 有 Python 但是 3.9 / 3.10，语法就过不去
 *   ③ 有 Python 也够新，但公司网 / 断网 / pip 源被墙，装不下来
 *
 * 所以「独立可执行文件」不是打包格式的问题，是**运行时归谁负责**的问题。
 * 这个脚本把答案改成「归我们」：构建时就把解释器和全部核心依赖备好，
 * 随安装包分发，装完即用，**不联网、不找系统 Python、不 pip**。
 *
 * ## 为什么用 embeddable 包而不是把 `engine/.venv` 拷过去
 *
 * 🔴 **venv 不可重定位。** `.venv/pyvenv.cfg` 和 `Scripts/*.exe` 里写死了
 * 创建时的绝对路径（`D:\...\engine\.venv`）。拷到用户的 `C:\Program Files\Synorive`
 * 之后，它仍然去找那个不存在的 D 盘路径 —— 而**这个失败不在 pip 阶段暴露，
 * 是在用户点开应用的那一刻才暴露**，报错还是一句 ModuleNotFoundError。
 *
 * python.org 的 **embeddable zip** 就是为这个场景做的：没有绝对路径、
 * 没有注册表依赖、解压到哪就在哪跑。代价是它默认不带 pip、
 * 且 `sys.path` 被 `._pth` 文件锁死（见下面 `patchPth`）。
 *
 * ## 产物
 *
 * `apps/desktop/resources/pyruntime/`（已进 .gitignore，别提交 —— 几百 MB）
 * 由 `electron-builder.yml` 的 extraResources 搬到 `resources/pyruntime`，
 * 再由 `engine.ts` 的 `pythonCandidates()` 第 ② 档优先选中。
 *
 * ## 用法
 *
 *   node scripts/bundle-python.mjs            # 有就跳过，没有才下
 *   node scripts/bundle-python.mjs --force    # 删掉重来
 *   node scripts/bundle-python.mjs --slim     # 不装 onnxruntime/numpy（省 ~300MB）
 */

import { execFileSync } from 'node:child_process';
import { createWriteStream, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { readdir, stat } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { pipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const OUT = join(ROOT, 'apps', 'desktop', 'resources', 'pyruntime');
const ENGINE = join(ROOT, 'engine');

/**
 * 🔴 **这个版本号必须和 `engine/pyproject.toml` 的 `requires-python` 对得上，
 * 而且必须是 embeddable 包真实存在的一个补丁号。**
 * python.org 只为**每个补丁号**发 embeddable zip，写 `3.13` 是 404。
 * 3.13.1 是本机 venv 实测跑通的那一支。
 */
const PY = '3.13.1';
const TAG = 'python313'; // ._pth 和内置 zip 的文件名前缀，跟着大版本走

/** slim 模式下跳过的包。它们加起来占整个运行时的 2/3 强 */
const HEAVY = ['onnxruntime', 'numpy'];

/**
 * 除核心依赖外，还要装进包里的 optional-dependencies。
 *
 * 🔴 **判据是「它是代码还是数据」，不是「它大不大」。**
 *
 * · `docs`（PDF/Word/Excel/PPT 解析）和 `sync`（AES-GCM）是**代码**。
 *   缺了它们，用户拖一个 PDF 进来会看到"必需依赖还缺"，而修复手段是
 *   pip 装包 —— 这正是「独立可执行文件」要消灭的那一步。
 *   把它们留给依赖医生，等于承认这个包并不独立。
 * · `media`（rapidocr / av）同理是代码，OCR 和视频解码都是主打功能。
 * · 模型权重（BGE-small-zh 等）是**数据**，几百 MB，且用户未必用得上
 *   全部语言/模态 —— 那些继续按需下载，这个不算破坏独立性。
 * · `gpu` / `face` 是**可选加速与可选功能**，默认关，
 *   而且 `gpu` 和 `onnxruntime` 互斥（同一个命名空间），
 *   打进同一个包里必然冲突。这两个必须留给依赖医生。
 *
 * · `ann`（usearch）2026-08-19 从"留给依赖医生"改成**随包带**。
 *   🔴 原来把它和 gpu/face 归成一类，但它既不大也不冲突：**实测 1.0 MB**，
 *      纯 wheel，没有命名空间竞争。而不带它的后果是——
 *      `search/engine.py` 里那条"超过 15 万块自动切近似检索"的路径，
 *      在**任何一台装机版机器上都走不到**（`import usearch` 直接失败，
 *      外层当成"索引不可用"静默退回暴力扫描）。
 *      也就是说这个功能只在开发机上活着，用户从来没享受过，且完全无感。
 *      1 MB 换回一整条大库提速路径，这笔账没什么好算的。
 *
 * 用 `--extras=` 可以覆盖（`--extras=` 空串表示一个都不装）。
 */
const DEFAULT_EXTRAS = ['docs', 'sync', 'media', 'ann'];

const argv = process.argv.slice(2);
const args = new Set(argv);
const FORCE = args.has('--force');
const SLIM = args.has('--slim');
const extrasArg = argv.find((a) => a.startsWith('--extras='));
const EXTRAS = extrasArg === undefined
  ? DEFAULT_EXTRAS
  : extrasArg.slice('--extras='.length).split(',').map((s) => s.trim()).filter(Boolean);

function log(s) {
  process.stdout.write(`[bundle-python] ${s}\n`);
}

/** 跑一条命令，**把退出码当真**。 */
/**
 * 🔴 **每一次调随包 python 都必须带 `PYTHONNOUSERSITE=1`。**
 *
 * embeddable 版的 `._pth` 里有 `import site`（pip 要靠它才跑得起来），
 * 于是 `%APPDATA%\Python\Python313\site-packages` —— 也就是开发者历史上
 * `pip install --user` 装的一大堆东西 —— 会被挂进 sys.path。
 *
 * 而 engine.ts 起引擎时是**设了**这个变量的（那边是对的：独立应用只该用
 * 自己带的东西）。两边口径不一致，后果不是"稍微不严谨"，是发出去一个
 * 装完打不开的包：
 *
 *   ① pip 看到 click 在用户目录里已经有了 → **判定已满足，不装进包里**
 *   ② 下面那个「必须真的 import 一遍」的自检，也从用户目录 import 到了
 *      → **自检照样通过**
 *   ③ 用户机器上没有那个目录，引擎启动 `import uvicorn` → `import click`
 *      → ModuleNotFoundError，进程退出码 1，无限重启
 *
 * 症状是"应用能开、托盘有图标、就是永远连不上引擎"，而真正的原因
 * 在打包机的一个跟本项目毫不相干的全局目录里。2026-09-12 实测踩到，
 * 已发的 0.1.9 / 0.1.10 两个版本都中招。
 *
 * 所以：**打包时的 sys.path 必须和运行时的 sys.path 一模一样**，
 * 否则自检验的就不是用户将要跑的那个环境。
 */
function run(exe, argv, opts = {}) {
  return execFileSync(exe, argv, {
    stdio: opts.quiet ? ['ignore', 'pipe', 'pipe'] : 'inherit',
    encoding: 'utf8',
    windowsHide: true,
    ...opts,
    env: { ...process.env, PYTHONNOUSERSITE: '1', ...(opts.env || {}) },
  });
}

async function download(url, dest, tries = 4) {
  log(`下载 ${url}`);
  // 🔴 **必须重试。** 实测这一步会以 `fetch failed`（TypeError，连
  // HTTP 状态码都没有）随机失败 —— 而失败时上一步已经把旧运行时删了，
  // 于是一次网络抖动就让构建目录处于"新的没下来、旧的没了"的状态。
  // 不重试的话，构建的成功率直接挂钩当下的网络运气
  let res;
  for (let i = 1; ; i++) {
    try {
      res = await fetch(url, { redirect: 'follow' });
      if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
      break;
    } catch (e) {
      if (i >= tries) throw new Error(`下载失败（试了 ${tries} 次）：${e?.message || e} —— ${url}`);
      const wait = i * 3;
      log(`  第 ${i} 次失败（${e?.message || e}），${wait}s 后重试…`);
      await new Promise((r) => setTimeout(r, wait * 1000));
    }
  }
  await pipeline(Readable.fromWeb(res.body), createWriteStream(dest));
  const { size } = await stat(dest);
  // 🔴 只看 HTTP 200 不够：代理和门户会用 200 回一个几 KB 的登录页。
  // 不查大小的话，下一步解压才报错，而那时候错误信息是「zip 损坏」，
  // 完全指不到根因（其实是网络被劫持了）
  if (size < 1024 * 512) {
    throw new Error(`下下来的文件只有 ${size} 字节，不像是真的 —— 多半是被代理/门户拦了`);
  }
  log(`  → ${(size / 1048576).toFixed(1)} MB`);
  return dest;
}

/**
 * embeddable 版的 `sys.path` 由 `pythonXYZ._pth` 完全接管，
 * **`PYTHONPATH` 和当前工作目录都不生效**（这是它和普通 Python 最大的区别，
 * 也是最容易踩的一脚：脚本明明在旁边，就是 ModuleNotFoundError）。
 *
 * 所以这里必须手写三件事：
 *   · `Lib\site-packages` —— 否则 pip 装的东西一个都 import 不到
 *   · `..\engine` —— 引擎源码在**同级的另一个目录**，只有写进来才找得到
 *   · `import site` —— 默认被注释掉，不打开的话 pip 自己都跑不起来
 */
function patchPth() {
  const pth = join(OUT, `${TAG}._pth`);
  if (!existsSync(pth)) throw new Error(`没找到 ${TAG}._pth —— 版本号 ${PY} 和 TAG ${TAG} 对不上？`);
  const body = [
    `${TAG}.zip`,
    '.',
    'Lib\\site-packages',
    // 相对 python.exe 所在目录。打包后的实际布局是
    //   resources/pyruntime/python.exe
    //   resources/engine/synorive/...
    // 所以 `..\engine` 正好落在引擎源码根上。**布局变了这里要跟着改。**
    '..\\engine',
    '',
    'import site',
    '',
  ].join('\r\n');
  writeFileSync(pth, body, 'utf8');
  log(`已重写 ${TAG}._pth（site-packages + ..\\engine + import site）`);
}

/**
 * 真的 import 一遍，确认这个运行时能跑。
 *
 * 🔴 **这个清单漏一个，就会发出去一个"装完打开才炸"的包。**
 *
 * 2026-08-03 实测教训：`tokenizers` 和 `lxml` 一直没被声明在 pyproject 里
 * （开发机上作为传递依赖凑巧装着），而这个探针清单也没有它们 ——
 * 于是**自检通过、包发出去、语义检索在装机版里整个是死的**，
 * 界面只显示「向量模型不可用」，谁也看不出是打包漏了一个包。
 *
 * 判据不是"我觉得重要的都写上"，而是：**凡是引擎代码里硬 import 的
 * 第三方模块，都必须在这里出现**。改依赖时同步改这里。
 */
function probeModules(slim, extras) {
  const mods = [
    'fastapi', 'uvicorn', 'pydantic', 'httpx', 'jieba', 'PIL', 'sqlite_vec',
    // uvicorn 的硬依赖。**单列出来是有原因的**：它是传递依赖，pip 只要
    // 觉得"已经有了"就不装，而 import uvicorn 的报错指向 uvicorn/main.py，
    // 没人会想到是 click 没打进来。直接点名，缺了就说缺 click。
    'click', 'h11',
    // 语义检索的地基：embedder / reranker 的 load() 里是硬导入，缺了它
    // 向量模型加载不了，检索静默退化成只有关键词
    'tokenizers',
    // 多引擎搜索的 HTML 解析（websearch/engines.py 的 _doc()），硬导入无兜底
    'lxml',
    // 2026-09-12 补。判据不是拍脑袋，是拿 ast 把 engine/synorive/**.py 里
    // 所有第三方顶层 import 扫了一遍，再和这张清单对差集扫出来的五个。
    // watchdog 尤其要紧：ingest/watcher.py 第 27 行是**模块顶层**的
    // `from watchdog.observers import Observer`，没有任何兜底 —— 缺了它
    // 整个 watcher 模块 import 失败，目录自动监控静默消失。
    'watchdog',
    // 拼音纠错（search/recovery.py）、EPUB 解析（ingest/parsers.py）、
    // 整库加密（store/db.py）、内存水位（analyze/embedder.py 等）。
    // 后四个源码里都带 try 兜底，但"有兜底"只意味着不崩，不意味着功能还在 ——
    // 它们全是声明过的依赖，本来就该在包里，缺了就是打包出了问题。
    'pypinyin', 'ebooklib', 'sqlcipher3', 'psutil',
  ];
  if (!slim) mods.push('numpy', 'onnxruntime');
  // extra 装了就必须 import 得到 —— 每个 extra 挑一个最有代表性的模块名。
  // **模块名和包名经常不一样**（python-docx→docx、python-pptx→pptx、
  // opencv-python→cv2），只查包名等于没查。
  // pymupdf 的老别名 `fitz` 别再用了 —— 1.28 起它每次导入都往 stdout
  // 打一行 warning，会把"输出必须正好是 ok"的自检直接判死
  const EXTRA_PROBE = {
    docs: ['pymupdf', 'docx', 'openpyxl', 'pptx', 'trafilatura'],
    sync: ['cryptography'],
    // sherpa_onnx 是语音转写。2026-08-03 才被声明进 media —— 在那之前
    // 它一直只是"碰巧装在开发机上"，探针里当然也没有
    media: ['rapidocr', 'av', 'sherpa_onnx'],
    ann: ['usearch'],
    face: ['insightface', 'cv2'],
  };
  for (const e of extras) mods.push(...(EXTRA_PROBE[e] || []));
  return mods;
}

/**
 * 逐个 import，缺哪个就报哪个。
 *
 * 🔴 **一次性 `import a,b,c` 只会告诉你第一个失败的**，而缺包往往不止一个，
 *    于是变成"修一个、重打一次包、再发现下一个"的循环。逐个试，一次列全。
 *
 * run() 已经统一带上 PYTHONNOUSERSITE=1，所以这里验的就是用户机器上
 * 真实的 sys.path，不会再从打包机的全局目录里蒙混过关。
 */
function verifyRuntime(pyExe, slim, extras) {
  const mods = probeModules(slim, extras);
  const code = [
    'import importlib,json,sys',
    `mods=${JSON.stringify(mods)}`,
    'bad=[]',
    'for m in mods:',
    '    try: importlib.import_module(m)',
    '    except Exception as e: bad.append(f"{m}: {type(e).__name__}: {e}")',
    'print(json.dumps({"bad":bad,"paths":[p for p in sys.path if "Roaming" in p or "AppData" in p]}))',
  ].join('\n');
  const out = run(pyExe, ['-c', code], { quiet: true, stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  let res;
  try {
    res = JSON.parse(out);
  } catch {
    throw new Error(`自检脚本没返回 JSON，原始输出：${out}`);
  }
  if (res.paths.length) {
    // 到这一步还能看到用户目录，说明 PYTHONNOUSERSITE 没生效 ——
    // 那么下面就算全绿也不算数，验的不是用户将要跑的环境
    throw new Error(
      `sys.path 里仍然有打包机的用户目录，自检不可信：\n  ${res.paths.join('\n  ')}`,
    );
  }
  if (res.bad.length) {
    throw new Error(
      `运行时缺 ${res.bad.length} 个模块（用户机器上会直接起不来）：\n  ${res.bad.join('\n  ')}`,
    );
  }
  log(`自检：${mods.length} 个模块全部 import 成功，且没有用户目录混入 ✅`);
}

/**
 * 从 pyproject 里读依赖 —— 手抄一份清单迟早和真实依赖脱节。
 * 返回 `{ core, extras: {名字: [...]} }`。
 */
function readDeps(pyExe) {
  const code = [
    'import tomllib,json',
    `d=tomllib.load(open(r"${join(ENGINE, 'pyproject.toml')}","rb"))`,
    'print(json.dumps({"core":d["project"]["dependencies"],'
      + '"extras":d["project"].get("optional-dependencies",{})}))',
  ].join(';');
  const out = run(pyExe, ['-c', code], { quiet: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const parsed = JSON.parse(out.trim());
  if (!Array.isArray(parsed.core) || !parsed.core.length) {
    throw new Error('从 pyproject.toml 里读出来的依赖是空的 —— 结构变了？');
  }
  return parsed;
}

/** 删掉纯粹占地方的东西。**不删 .dist-info** —— 依赖医生后续装扩展包要靠它判断已装什么 */
async function prune(dir) {
  let freed = 0;
  const KILL_DIR = new Set(['__pycache__', 'tests', 'test', '.pytest_cache']);
  const walk = async (d) => {
    let ents;
    try {
      ents = await readdir(d, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of ents) {
      const p = join(d, e.name);
      if (e.isDirectory()) {
        if (KILL_DIR.has(e.name)) {
          try {
            freed += await dirSize(p);
            rmSync(p, { recursive: true, force: true });
          } catch {
            /* 占用中就算了，不值得为几 MB 让构建失败 */
          }
          continue;
        }
        await walk(p);
      } else if (e.name.endsWith('.pdb') || e.name.endsWith('.pyc')) {
        try {
          freed += (await stat(p)).size;
          rmSync(p, { force: true });
        } catch {
          /* 同上 */
        }
      }
    }
  };
  await walk(dir);
  return freed;
}

async function dirSize(d) {
  let total = 0;
  const ents = await readdir(d, { withFileTypes: true });
  for (const e of ents) {
    const p = join(d, e.name);
    if (e.isDirectory()) total += await dirSize(p);
    else total += (await stat(p)).size.valueOf();
  }
  return total;
}

async function main() {
  if (process.platform !== 'win32') {
    log('这个脚本只做 Windows 的 embeddable 运行时。其他平台请另写一份，直接退出。');
    return;
  }

  const pyExe = join(OUT, 'python.exe');
  const stamp = join(OUT, '.synorive-bundle.json');

  if (existsSync(stamp) && !FORCE) {
    const meta = JSON.parse(readFileSync(stamp, 'utf8'));
    const sameExtras = JSON.stringify(meta.extras || []) === JSON.stringify(EXTRAS);
    if (meta.python === PY && meta.slim === SLIM && sameExtras) {
      /**
       * 🔴 **跳过安装可以，跳过自检不行。**
       *
       * 戳文件只说明"当时装完了"，不说明现在这个目录还是好的。它可能被
       * 手工 pip uninstall 过、被清理脚本扫过、或者当初就是在一个
       * sys.path 被污染的环境下通过的自检（见 run() 上面那段）。
       * 原来这里直接 return —— 于是一个坏掉的运行时会被**无限次跳过**，
       * 每次打包都原样打进安装包，谁也不会再看它一眼。
       *
       * 验一遍只要一秒多，换的是"绝不会把起不来的引擎发出去"。
       * 验不过就当场重建，不需要人来判断。
       */
      try {
        verifyRuntime(join(OUT, 'python.exe'), SLIM, EXTRAS);
        log(`已有 ${PY}${SLIM ? '（slim）' : ''} + [${EXTRAS.join(',')}] 运行时，自检通过，跳过重建`);
        return;
      } catch (e) {
        log(`⚠ 已有运行时没通过自检 → 重建。原因：${e?.message || e}`);
      }
    }
    log(`已有的是 ${meta.python}${meta.slim ? '（slim）' : ''} + [${(meta.extras || []).join(',')}]，和这次要的不一样 → 重建`);
  }

  if (existsSync(OUT)) {
    log('清掉旧的运行时目录');
    rmSync(OUT, { recursive: true, force: true });
  }
  mkdirSync(OUT, { recursive: true });

  // ── ① 解释器本体 ────────────────────────────────────────────
  const zip = join(OUT, '_embed.zip');
  await download(
    `https://www.python.org/ftp/python/${PY}/python-${PY}-embed-amd64.zip`,
    zip,
  );
  log('解压…');
  // PowerShell 自带解压，不引第三方依赖。`-Force` 覆盖，目录是空的所以无所谓
  run('powershell', [
    '-NoProfile', '-NonInteractive', '-Command',
    `Expand-Archive -LiteralPath '${zip}' -DestinationPath '${OUT}' -Force`,
  ]);
  rmSync(zip, { force: true });
  if (!existsSync(pyExe)) throw new Error('解压完没有 python.exe —— zip 结构和预期不符');

  patchPth();

  // ── ② pip ───────────────────────────────────────────────────
  // embeddable 包**不带 pip**，得用 get-pip 引导一次
  const getpip = join(OUT, '_get-pip.py');
  await download('https://bootstrap.pypa.io/get-pip.py', getpip);
  log('装 pip…');
  run(pyExe, [getpip, '--no-warn-script-location']);
  rmSync(getpip, { force: true });

  // 🔴 **get-pip 只给 pip，不给 setuptools。**
  // 大部分包有 wheel 可以直接解压，但依赖里**有几个只发源码包**
  // （jieba 就是，PyPI 上从来只有 .tar.gz）。装 sdist 要现场 build，
  // build 要 `setuptools.build_meta` —— 于是报
  // `BackendUnavailable: Cannot import 'setuptools.build_meta'`。
  //
  // 这个错在整条日志的最后一行，前面刷了几十行 pip 的正常输出，
  // 很容易被当成"网络问题重试一下"。**根因是运行时缺构建后端，重试一万次也一样。**
  log('装构建后端（setuptools/wheel）—— 依赖里有只发源码包的，不然 build 不起来…');
  run(pyExe, ['-m', 'pip', 'install', '--no-warn-script-location', 'setuptools>=75', 'wheel']);

  // ── ③ 核心依赖 ──────────────────────────────────────────────
  const all = readDeps(pyExe);
  let deps = [...all.core];
  for (const name of EXTRAS) {
    const got = all.extras[name];
    if (!got) {
      // 🔴 拼错的 extra 名字必须**报错退出**，不能只是少装几个包。
      // 静默跳过的话，构建照常成功，缺失要等用户拖一个 PDF 进来才暴露
      throw new Error(
        `pyproject.toml 里没有名为 "${name}" 的 optional-dependencies。`
        + `现有的是：${Object.keys(all.extras).join(' / ')}`,
      );
    }
    deps.push(...got);
  }
  if (SLIM) {
    deps = deps.filter((d) => !HEAVY.some((h) => d.toLowerCase().startsWith(h)));
    log(`slim 模式：跳过 ${HEAVY.join(' / ')}（语义检索会退化成只有关键词匹配）`);
  }
  log(`装 ${deps.length} 个包（核心 ${all.core.length} + extras [${EXTRAS.join(',') || '无'}]）`);
  run(pyExe, [
    '-m', 'pip', 'install',
    '--no-warn-script-location',
    '--no-compile', // .pyc 首次运行自己会生成，打进包里纯属double
    ...deps,
  ]);

  // ── ④ 瘦身 + 自检 ───────────────────────────────────────────
  const freed = await prune(join(OUT, 'Lib', 'site-packages'));
  log(`清掉 ${(freed / 1048576).toFixed(1)} MB 的缓存/测试/调试符号`);

  // 🔴 **必须真的 import 一遍才算装好。**
  // pip 报 Successfully installed 只说明文件落地了，不说明这个解释器
  // 能不能 import 到它 —— 而 `._pth` 写错正是「装完了但 import 不到」
  // 的典型，且这种失败要等到用户点开应用才暴露。
  // 清单和判据见 verifyRuntime() / probeModules()。
  verifyRuntime(pyExe, SLIM, EXTRAS);

  const total = await dirSize(OUT);
  writeFileSync(
    stamp,
    JSON.stringify({ python: PY, slim: SLIM, extras: EXTRAS, bytes: total, deps }, null, 2),
    'utf8',
  );
  log(`完成 —— ${(total / 1048576).toFixed(0)} MB @ ${OUT}`);
}

main().catch((e) => {
  process.stderr.write(`[bundle-python] ❌ ${e?.message || e}\n`);
  process.exit(1);
});
