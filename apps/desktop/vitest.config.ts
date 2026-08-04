import { defineConfig } from 'vitest/config';

/**
 * 界面侧纯函数测试的最小配置
 * ====================================================================
 * **故意不复用 `electron.vite.config.ts`** —— 那份配置里带着 electron 主进程
 * 和 preload 两个额外的构建目标，vitest 跑起来会连带尝试解析 `electron` 模块，
 * 而 `electron` 在纯 Node 进程里 require 出来是一个字符串路径，不是模块。
 *
 * `environment: 'node'` 而不是 jsdom：这一批测的全是纯函数，
 * 一行 DOM 都不碰。**装 jsdom 只为了"看起来像前端测试"是纯浪费** ——
 * 而且 jsdom 会定义 `self`，那会让 `heavy.worker.ts` 末尾的 Worker 入口
 * 在 import 的那一刻就注册 `onmessage`，测的就不是纯函数本身了。
 *
 * `@synorive/*` 不用配 alias：npm workspaces 已经在根 `node_modules` 里
 * 建好了软链，Vite 的默认解析就能找到（走的是各包 package.json 的 exports）。
 */
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/__tests__/**/*.test.ts'],
    // 纯函数测得极快，报告开到 verbose 才看得见每条断言测的是什么
    reporters: ['verbose'],
  },
});
