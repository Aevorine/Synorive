/**
 * 引擎地址的唯一来源
 * ====================================================================
 * 端口是每次启动动态挑的，**协议也不是固定的**：开了局域网配对就会带
 * `--lan-tls` 起 uvicorn，那一刻整个监听口都是 HTTPS —— 回环也不例外。
 *
 * 🔴 **这正是 2026-09-12 实测踩到的那个坑。**
 *    把 `--lan-tls` 改成"开配对就自动开"的那一轮，没有任何一个客户端跟着改：
 *    桌面主进程、渲染进程、MCP、CLI 四边全把 `http://127.0.0.1:${port}` 写死。
 *    后果不是某个功能不好使，是**整个应用不可用** —— 探活用 HTTP 打 HTTPS 口，
 *    永远拿不到 200，45 秒后按"启动超时"把引擎 SIGKILL 掉，然后重启、再超时，
 *    无限循环。而引擎日志里明明白白写着「引擎就绪 · 库里已有 490 条内容」，
 *    两边各说各话，谁看日志都会以为是引擎的问题。
 *
 *    所以地址不能再散落在十几个模板字符串里，必须只有一处。
 *
 * 🔴 **用 Electron 的 `net.fetch`，不用全局 fetch。**
 *    `net.fetch` 走 Chromium 的网络栈，也就是走**会话的证书校验**——
 *    于是"信任我们自己那张自签证书"这件事只需要配一次
 *    （见 index.ts 的 setCertificateVerifyProc），主进程和渲染进程同时生效。
 *    全局 fetch 走的是 Node 的 undici，得另外塞 CA，而 undici 在本项目里
 *    只是个传递依赖 —— 依赖"碰巧装着"的东西，正是这一轮刚修掉的那类坑。
 */

import { net } from 'electron';

export interface EngineEndpoint {
  port: number;
  /** 开了局域网 TLS 时为 true —— 那时候连回环口也是 HTTPS */
  secure: boolean;
}

let current: EngineEndpoint | null = null;

export function setEngineEndpoint(ep: EngineEndpoint | null): void {
  current = ep;
}

export function engineEndpoint(): EngineEndpoint | null {
  return current;
}

/** `https://127.0.0.1:44954` 这种。端口显式传是为了让启动期探活也能用上。 */
export function engineOrigin(port?: number, secure?: boolean): string {
  const p = port ?? current?.port;
  const s = secure ?? current?.secure ?? false;
  if (p == null) throw new Error('引擎端口未知');
  return `${s ? 'https' : 'http'}://127.0.0.1:${p}`;
}

export function engineWsOrigin(port?: number, secure?: boolean): string {
  const p = port ?? current?.port;
  const s = secure ?? current?.secure ?? false;
  if (p == null) throw new Error('引擎端口未知');
  return `${s ? 'wss' : 'ws'}://127.0.0.1:${p}`;
}

/**
 * 主进程里所有打引擎的请求都走这里。
 * 传 port/secure 是给启动期探活用的 —— 那时候 endpoint 还没定下来。
 */
export function engineFetch(
  path: string,
  init?: RequestInit,
  port?: number,
  secure?: boolean,
): Promise<Response> {
  return net.fetch(`${engineOrigin(port, secure)}${path}`, init);
}
