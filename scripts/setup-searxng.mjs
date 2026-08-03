#!/usr/bin/env node
/**
 * 自建 SearXNG 一键部署 —— S2
 * ====================================================================
 * **为什么非要自建**：2026-08-02 实测把「多引擎」这件事的前提整个推翻了 ——
 * Google 强制 JavaScript、DuckDuckGo 的 html 端点改成 JS 落地页、
 * Yandex 直接给验证码、**七个 SearXNG 公共实例逐个试全部 429/403**
 * （它们普遍封代理与数据中心 IP）。
 *
 * 免费拿到 Google/DDG/Brave 结果，现实里只剩这一条路：自己跑一个 SearXNG，
 * 让它在服务端替你去问那几家。它是**你自己的 IP**，不会被当成爬虫农场。
 *
 * **这个脚本默认不动你的机器。** 不加 `--apply` 就只打印它打算做什么、
 * 检测到的环境是什么、以及每一步的命令原文 —— 装软件属于要先问你的操作。
 *
 * 两条路线，自动选：
 *   ① Docker（推荐）—— 一条命令起容器，升级和卸载都干净
 *   ② 本地 Python 源码 —— 没有 Docker 时的退路，装的东西多、卸得不干净
 *
 * 🔴 **最容易踩的坑，这里直接替你填掉**：SearXNG 默认**只开放 HTML 格式**，
 * `format=json` 会返回 403。必须在 settings.yml 里显式加上 json ——
 * 不加的话，引擎那边看到的现象是"实例明明起来了却一条结果都没有"，
 * 而 `engines.py` 会把它报成 BROKEN（解析失败），排查方向直接跑偏。
 *
 * 用法：
 *   node scripts/setup-searxng.mjs              # 只检测 + 打印计划（默认）
 *   node scripts/setup-searxng.mjs --apply      # 真的执行
 *   node scripts/setup-searxng.mjs --port 8899  # 换端口
 *   node scripts/setup-searxng.mjs --status     # 只看当前实例活着没
 *   node scripts/setup-searxng.mjs --stop       # 停掉容器
 */

import { execFileSync, spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const argv = process.argv.slice(2)
const has = (f) => argv.includes(f)
const val = (f, d) => {
  const i = argv.indexOf(f)
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d
}

const APPLY = has('--apply')
const PORT = Number.parseInt(val('--port', '8888'), 10)
const CONTAINER = 'synorive-searxng'
const DATA_DIR = join(ROOT, 'data', 'searxng')

const C = {
  dim: (s) => `\x1b[2m${s}\x1b[0m`,
  bold: (s) => `\x1b[1m${s}\x1b[0m`,
  ok: (s) => `\x1b[32m${s}\x1b[0m`,
  warn: (s) => `\x1b[33m${s}\x1b[0m`,
  err: (s) => `\x1b[31m${s}\x1b[0m`,
}

function which(cmd) {
  const r = spawnSync(process.platform === 'win32' ? 'where' : 'which', [cmd], {
    encoding: 'utf8',
    shell: false,
  })
  return r.status === 0 ? (r.stdout || '').split(/\r?\n/)[0].trim() : null
}

function run(cmd, args, { allowFail = false } = {}) {
  console.log(C.dim(`  $ ${cmd} ${args.join(' ')}`))
  if (!APPLY) return { skipped: true }
  const r = spawnSync(cmd, args, { encoding: 'utf8', stdio: 'inherit' })
  if (r.status !== 0 && !allowFail) {
    throw new Error(`命令失败（退出码 ${r.status}）：${cmd} ${args.join(' ')}`)
  }
  return r
}

/**
 * 探测宿主机的 HTTP 代理。
 *
 * 🔴 **这一步是这台机器上装 SearXNG 的成败关键，实测吃过亏**：
 * 容器起来了、`/search?format=json` 也通，但**一条结果都没有**，
 * 所有引擎报 timeout。原因是宿主出网走本机代理（127.0.0.1:10808），
 * 而**容器不继承宿主的代理设置** —— 容器里的 127.0.0.1 是容器自己。
 *
 * 症状特别有迷惑性：实例本身完全健康，健康检查、JSON 格式、端口全对，
 * 只是它问出去的每一家都超时。不知道这一条的话，很容易去改选择器、
 * 换引擎、怀疑镜像有问题 —— 全在错误的方向上。
 *
 * 容器里访问宿主要用 `host.docker.internal`（Docker Desktop 提供）。
 */
function detectHostProxy() {
  const fromEnv = process.env.HTTPS_PROXY || process.env.https_proxy ||
    process.env.HTTP_PROXY || process.env.http_proxy
  if (fromEnv) return normalizeProxy(fromEnv)
  if (process.platform !== 'win32') return null
  try {
    const out = execFileSync(
      'reg',
      ['query', 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings'],
      { encoding: 'utf8' },
    )
    if (!/ProxyEnable\s+REG_DWORD\s+0x1/i.test(out)) return null
    const m = out.match(/ProxyServer\s+REG_SZ\s+(\S+)/i)
    return m ? normalizeProxy(m[1]) : null
  } catch {
    return null
  }
}

/** 127.0.0.1:10808 → http://host.docker.internal:10808（容器视角） */
function normalizeProxy(raw) {
  let s = String(raw).trim()
  // ProxyServer 可能是 "http=host:port;https=host:port" 的形式
  if (s.includes('=')) {
    const part = s.split(';').find((p) => p.startsWith('http')) || s.split(';')[0]
    s = part.split('=')[1] || part
  }
  s = s.replace(/^https?:\/\//, '')
  s = s.replace(/^(127\.0\.0\.1|localhost|0\.0\.0\.0)(?=:)/, 'host.docker.internal')
  return `http://${s}`
}

/**
 * SearXNG 的最小配置。
 *
 * 只改**必须改的几处**，其余全用它的默认值 —— 抄一份几百行的完整
 * settings.yml 进来，等于把它以后每次升级的默认值变更全冻结在这里，
 * 那才是真正的维护负担。
 */
function settingsYaml(secret, proxy) {
  const outgoing = proxy
    ? `
# 🔴 容器不继承宿主的代理设置（容器里的 127.0.0.1 是容器自己）。
# 不配这一段的症状是：实例完全健康，但**每一家引擎都超时、一条结果都没有**——
# 特别容易被误判成"镜像有问题"或"选择器坏了"，其实只是出不了网。
  proxies:
    all://: "${proxy}"`
    : ''

  return `# Synorive 自建 SearXNG —— 由 scripts/setup-searxng.mjs 生成
# 只覆盖必须改的几处，其余沿用 SearXNG 自带默认值。
use_default_settings: true

server:
  secret_key: "${secret}"
  limiter: false          # 本机自用，不需要限流器（它要 redis）
  image_proxy: false

search:
  # 🔴 这一行是关键：SearXNG 默认只开放 html，不加 json 的话
  # /search?format=json 会返回 403，而现象是"实例起来了却一条结果都没有"
  formats:
    - html
    - json

outgoing:
  # 默认 3 秒对走代理的链路太紧，第一次握手经常就超了
  request_timeout: 8.0
  max_request_timeout: 15.0${outgoing}

# 只在本机监听，不对局域网开放 —— 一个能被别人用的搜索代理，
# 别人的搜索记录会算在你头上
general:
  instance_name: "Synorive Local"
`
}

function randomSecret() {
  return [...crypto.getRandomValues(new Uint8Array(24))]
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

async function probe(port) {
  const url = `http://127.0.0.1:${port}/search?q=test&format=json`
  try {
    const ctl = new AbortController()
    const t = setTimeout(() => ctl.abort(), 4000)
    const r = await fetch(url, { signal: ctl.signal })
    clearTimeout(t)
    if (!r.ok) return { ok: false, why: `HTTP ${r.status}` }
    const ct = r.headers.get('content-type') || ''
    if (!ct.includes('json')) {
      return {
        ok: false,
        why: 'JSON 格式没开 —— settings.yml 里 search.formats 要包含 json',
      }
    }
    const d = await r.json()
    const n = (d.results || []).length
    const dead = d.unresponsive_engines || []

    // 🔴 **「返回了 JSON」不等于「能用」。** 这个探针最早就写成
    // `r.ok → 可用`，结果实例每家引擎都超时、一条结果都没有，
    // 它照样报"✔ 可用"，还因此触发了"已经装好了，不用再装"的早退，
    // 把真正的问题（容器出不了网）整个盖住。
    // 这是静默失败最典型的一种：**把"没反应"判成"没问题"**。
    if (n === 0) {
      const why = dead.length
        ? `实例活着但每家引擎都失败：${dead.map((x) => (Array.isArray(x) ? x.join(' ') : x)).join('、')}` +
          '　—— 十有八九是容器出不了网（宿主走代理但容器不继承）'
        : '实例活着但一条结果都没有'
      return { ok: false, why, dead }
    }
    return { ok: true, results: n, dead }
  } catch (e) {
    return { ok: false, why: e.name === 'AbortError' ? '超时' : String(e.message || e) }
  }
}

async function status() {
  console.log(C.bold(`\n检测 http://127.0.0.1:${PORT}`))
  const p = await probe(PORT)
  if (p.ok) {
    console.log(C.ok(`  ✔ 实例可用，测试查询返回 ${p.results} 条`))
    console.log(
      C.dim(
        `  去 Synorive 设置 → 联网搜索，把 searxng 打开、地址填 http://127.0.0.1:${PORT}`,
      ),
    )
    return 0
  }
  console.log(C.warn(`  ✘ 用不了：${p.why}`))
  return 1
}

function stop() {
  const docker = which('docker')
  if (!docker) {
    console.log(C.warn('没找到 docker，没什么可停的'))
    return 0
  }
  run('docker', ['rm', '-f', CONTAINER], { allowFail: true })
  console.log(APPLY ? C.ok('已停止并删除容器') : C.dim('（干跑，没有真的执行）'))
  return 0
}

async function main() {
  console.log(C.bold('\nSearXNG 本地实例部署 —— Synorive S2'))
  console.log(
    C.dim(
      '实测：Google/DDG/Yandex 直连全部被挡，公共 SearXNG 实例全部 429/403。\n' +
        '自建是免费拿到这些引擎结果的唯一现实路径。\n',
    ),
  )

  if (has('--status')) return await status()
  if (has('--stop')) return stop()

  // 已经跑着就别重复装
  const already = await probe(PORT)
  if (already.ok) {
    console.log(C.ok(`端口 ${PORT} 上已经有一个可用的 SearXNG，不用再装`))
    return 0
  }

  const docker = which('docker')
  console.log(C.bold('环境检测'))
  console.log(`  Docker：${docker ? C.ok(docker) : C.warn('没有')}`)
  console.log(`  端口 ${PORT}：${already.ok ? '被占用（是可用实例）' : '空闲或不可用'}`)
  console.log(`  配置目录：${DATA_DIR}`)

  if (!docker) {
    console.log(C.warn('\n没有 Docker，给你两条路：'))
    console.log(
      [
        '  ① 装 Docker Desktop（推荐）：winget install Docker.DockerDesktop',
        '     装完重开一次终端，再跑一遍这个脚本。',
        '  ② 不装 Docker 走源码（依赖多、卸不干净，不推荐）：',
        '     git clone https://github.com/searxng/searxng.git',
        '     cd searxng && pip install -e .',
        `     python -m searx.webapp   # 默认 8888 端口`,
        '',
        '  ③ 或者干脆不用 SearXNG：设置里填一个 Serper/Brave 的 API Key，',
        '     同样能拿到 Google 结果，代价是要花钱。',
      ].join('\n'),
    )
    return 2
  }

  console.log(C.bold('\n打算做这三件事：'))
  console.log(`  1. 在 ${DATA_DIR} 写一份 settings.yml（开启 JSON 格式，关掉限流器）`)
  console.log(`  2. 拉取镜像 searxng/searxng`)
  console.log(`  3. 起一个只监听 127.0.0.1:${PORT} 的容器（名字 ${CONTAINER}）`)
  if (!APPLY) {
    console.log(
      C.warn('\n这是干跑，什么都没做。确认没问题的话加 --apply 再跑一次。'),
    )
  }

  console.log(C.bold('\n[1/3] 写配置'))
  // 🔴 代理**默认不配**，这一条是实测定下来的，不是想当然：
  // Docker Desktop 的容器走 NAT 出网，本机实测直连就能拿到
  // Google（google cse）和 DuckDuckGo 的结果 —— 而宿主的 HTTP 代理
  // 未必接受来自容器网段的连接。默认硬塞一个代理等于凭空多一个故障点。
  // 只有当你真的发现"每家引擎都超时"时，才用 `--proxy auto` 打开它。
  const proxyArg = val('--proxy', null)
  const proxy = has('--no-proxy')
    ? null
    : proxyArg === 'auto'
      ? detectHostProxy()
      : proxyArg
  if (proxy) {
    console.log(C.dim(`  容器出网走代理：${proxy}`))
  } else {
    console.log(
      C.dim('  容器直连出网（如果装完发现每家引擎都超时，用 --proxy auto 重跑）'),
    )
  }
  if (APPLY) {
    mkdirSync(DATA_DIR, { recursive: true })
    const f = join(DATA_DIR, 'settings.yml')
    if (existsSync(f) && !has('--force-config')) {
      // 默认不覆盖是对的（用户可能自己改过），但**必须说清楚怎么强制** ——
      // 否则改了脚本里的模板却发现一点效果都没有，会以为是模板写错了
      console.log(C.dim(`  已存在，不覆盖：${f}`))
      console.log(C.dim('  （想用新模板重写：加 --force-config）'))
    } else {
      writeFileSync(f, settingsYaml(randomSecret(), proxy), 'utf8')
      console.log(C.ok(`  已写入 ${f}`))
    }
  } else {
    console.log(C.dim(`  （会写 ${join(DATA_DIR, 'settings.yml')}）`))
  }

  console.log(C.bold('\n[2/3] 拉镜像'))
  run('docker', ['pull', 'searxng/searxng:latest'])

  console.log(C.bold('\n[3/3] 起容器'))
  run('docker', ['rm', '-f', CONTAINER], { allowFail: true })
  run('docker', [
    'run', '-d',
    '--name', CONTAINER,
    '--restart', 'unless-stopped',
    // 只绑本机回环 —— 绑 0.0.0.0 等于给局域网开了一个匿名搜索代理，
    // 别人用它搜的东西会算在你的 IP 头上
    '-p', `127.0.0.1:${PORT}:8080`,
    '-v', `${DATA_DIR}:/etc/searxng`,
    '-e', `SEARXNG_BASE_URL=http://127.0.0.1:${PORT}/`,
    'searxng/searxng:latest',
  ])

  if (!APPLY) {
    console.log(C.warn('\n干跑结束。加 --apply 才会真的执行上面这些。'))
    return 0
  }

  console.log(C.dim('\n等容器起来（最多 30 秒）…'))
  for (let i = 0; i < 15; i++) {
    await new Promise((r) => setTimeout(r, 2000))
    const p = await probe(PORT)
    if (p.ok) {
      console.log(C.ok(`\n✔ 起来了，测试查询返回 ${p.results} 条结果`))
      console.log(
        `\n接下来：Synorive 设置 → 联网搜索 → 打开 searxng，地址填 ` +
          C.bold(`http://127.0.0.1:${PORT}`),
      )
      console.log(C.dim(`停止：node scripts/setup-searxng.mjs --stop`))
      return 0
    }
  }
  // 起不来要给出**怎么自己看**，而不是一句"失败了"
  console.log(C.err('\n✘ 30 秒内没起来。看一眼日志：'))
  console.log(C.dim(`  docker logs ${CONTAINER} --tail 50`))
  console.log(C.dim('  常见原因：端口被占；或 settings.yml 语法错（容器会反复重启）'))
  try {
    execFileSync('docker', ['logs', CONTAINER, '--tail', '20'], { stdio: 'inherit' })
  } catch {
    /* 日志取不到就算了，上面已经告诉用户怎么自己看 */
  }
  return 1
}

main()
  .then((code) => process.exit(code || 0))
  .catch((e) => {
    console.error(C.err(`\n出错了：${e.message}`))
    process.exit(1)
  })
