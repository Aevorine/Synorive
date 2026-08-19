/**
 * 主题与视觉偏好落到 DOM
 * ============================================================
 * 全部通过 <html> 上的 data-* 属性驱动 CSS，不在组件里改样式。
 * 这样切主题/切字体方案/开护眼是一次属性写入，不触发任何组件重渲染，
 * 也就不会掉帧。
 */

import { eyeComfort } from '@synorive/design-tokens';
import type { AppSettings } from '@synorive/shared-types';
import type { ResolvedTheme } from './store';

export function applyTheme(theme: ResolvedTheme): void {
  document.documentElement.setAttribute('data-theme', theme);
}

export function applyFontScheme(scheme: AppSettings['fontScheme']): void {
  document.documentElement.setAttribute('data-font-scheme', scheme);
}

/**
 * 护眼色温（在主题之上再叠一层暖色）
 *
 * 🔴 **这条路径有性能代价，而且代价完全不直观。** `filter` 挂在 <body> 上会把
 *    整个页面提成一个独立合成层，滚动时每一帧都要重做一遍像素级滤镜 ——
 *    表现是"滚长列表发涩"，而没有任何人会把它联想到护眼开关上。
 *
 *    所以 B4 之后：**想要暖色请先选 `paper` 主题**（那是一套静态色板，零渲染代价），
 *    这个滤镜只留给"纸感还不够暖"的极少数情况。
 *
 *    `off` 时必须**移除属性**而不是写 `filter: none` ——
 *    写 `none` 一样会创建合成层（浏览器不会因为值是 none 就撤销层提升），
 *    等于关了开关还在付性能账。这正是"看起来关掉了其实没关"的静默失败。
 */
export function applyEyeComfort(level: AppSettings['eyeComfort']): void {
  const root = document.documentElement;
  if (level === 'off') {
    root.style.removeProperty('--syn-eye-filter');
    root.removeAttribute('data-eye');
    return;
  }
  const filter = eyeComfort.warmth[level] ?? 'none';
  root.style.setProperty('--syn-eye-filter', filter);
  root.setAttribute('data-eye', level);
}

export function applyDensity(density: AppSettings['density']): void {
  document.documentElement.setAttribute('data-density', density);
}

/**
 * 界面整体缩放。
 *
 * 🔴 **第一版改的是 `<html>` 的 font-size，那是个彻底的空操作。**
 *    设计令牌里的字号、间距、控件高度**全是 px 字面量**（`--syn-fs-body: 16px`、
 *    `--syn-space-md: 12px`），没有一处用 rem —— 根字号改了谁也不看。
 *    测出来的现象是：`data-scale` 属性写上了、设置里也存了，
 *    而界面一个像素都没动。功能"看起来做完了"，实际零效果。
 *
 * 🔴 也不能用 CSS `transform: scale`：它不改变布局尺寸，右边和下边会空出一块。
 *
 * 现在走 Electron 的 `webContents.setZoomFactor` —— 就是浏览器 Ctrl+= 那个缩放。
 * 它在布局层生效，字、图标、间距、命中区一起等比变，`getBoundingClientRect`
 * 返回的仍是自洽的 CSS 像素，虚拟滚动的测量不受影响。
 *
 * `data-scale` 仍然写到 <html> 上，给需要按档位微调的样式留一个钩子。
 */
export function applyUiScale(scale: AppSettings['uiScale']): void {
  const root = document.documentElement;
  const pct = scale ?? 100;
  if (pct === 100) root.removeAttribute('data-scale');
  else root.setAttribute('data-scale', String(pct));
  void window.synorive.window.setZoom(pct / 100);
}

export function applyAll(settings: AppSettings, resolved: ResolvedTheme): void {
  applyTheme(resolved);
  applyFontScheme(settings.fontScheme);
  applyEyeComfort(settings.eyeComfort);
  applyDensity(settings.density);
  applyUiScale(settings.uiScale);
}
