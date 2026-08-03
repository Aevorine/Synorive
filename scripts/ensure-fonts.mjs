#!/usr/bin/env node
/**
 * 字体预检 —— 让一次全新克隆能直接 `npm run dev`
 * ====================================================================
 *
 * ## 治的什么病
 *
 * `apps/desktop/src/styles/global.css` 里有一行 `@import './fonts.css'`，
 * 而 `fonts.css` 和 `fonts/` 是 `python scripts/build_fonts.py` 生成的产物，
 * 两者都在 `.gitignore` 里（那 6 MB 字体切片不该进仓库）。
 *
 * 于是**一次全新克隆的第一条命令就会失败**：Vite 解析不到那个 @import，
 * 报的是一句 `Failed to resolve import "./fonts.css"` —— 它不会告诉你
 * "去跑 build_fonts.py"，看到的人只会以为项目本身是坏的。
 * README 里确实写了"必须自己生成一次"，但**能被跳过的步骤就一定会被跳过**，
 * 而代价是别人对这个项目的第一印象。
 *
 * ## 怎么治
 *
 * 缺文件就先写一份**兜底 fonts.css**：不含任何 @font-face，
 * 让标题回退到系统衬线字体。界面照常能跑、能构建、能打包，
 * 只是标题少了那套打包的思源宋体。
 *
 * 🔴 **兜底不能是静默的。** 静默生成一个空文件，等于把
 * "字体没生成" 这件事永久藏起来 —— 用户会以为界面本来就长这样。
 * 所以这里往控制台打一行明确的提示，说清缺了什么、跑哪条命令能补上。
 *
 * 🔴 **绝不覆盖真产物。** 只在文件不存在时写。`build_fonts.py` 跑过之后
 * 这个脚本必须完全无动作，否则真字体会被兜底版顶掉，
 * 而那是一个"昨天还好好的，今天字体没了"的谜题。
 */

import { existsSync, writeFileSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const CSS = join(ROOT, 'apps', 'desktop', 'src', 'styles', 'fonts.css');

/** 真产物的第一行标记。用它区分「已生成」和「上一次的兜底」 */
const FALLBACK_MARK = 'SYNORIVE_FONT_FALLBACK';

const FALLBACK = `/* ============================================================
 * ${FALLBACK_MARK} —— 这是兜底占位，不是真的字体产物
 *
 * 真产物由 \`python scripts/build_fonts.py\` 生成：一套按 unicode-range
 * 切片的思源宋体 woff2（约 6 MB），标题（≥24px）用它。
 * 那 6 MB 不进仓库，所以全新克隆时这个文件不存在，
 * 而 global.css 又 @import 了它 —— 不补一份的话 Vite 直接解析失败。
 *
 * 现在这份里**没有任何 @font-face**，效果是：
 *   · 正文、西文、数字：不受影响（走设计令牌里的 Times New Roman + 宋体）
 *   · 标题：回退到系统衬线字体，比思源宋体略糙，但完全可用
 *
 * 要补齐：python scripts/build_fonts.py
 * 补齐之后这个文件会被真产物覆盖，这段注释也就跟着消失了。
 * ============================================================ */
`;

if (existsSync(CSS)) {
  const head = readFileSync(CSS, 'utf8').slice(0, 400);
  if (!head.includes(FALLBACK_MARK)) {
    // 真产物在，什么都不做。**这条路径必须完全安静** ——
    // 正常情况下每次 dev/build 都会走到这里，多打一行就是每次都刷屏
    process.exit(0);
  }
  console.log(
    '[fonts] 当前用的是兜底字体（标题走系统衬线）。要那套打包的思源宋体：python scripts/build_fonts.py',
  );
  process.exit(0);
}

writeFileSync(CSS, FALLBACK, 'utf8');
console.log(
  `[fonts] 没找到 ${CSS.replace(ROOT + '\\', '').replace(ROOT + '/', '')}，已写入兜底版，构建可以继续。\n` +
    '[fonts] 标题会回退到系统衬线字体。要那套打包的思源宋体，跑一次：python scripts/build_fonts.py',
);
