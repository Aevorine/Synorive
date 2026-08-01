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

export function applyEyeComfort(level: AppSettings['eyeComfort']): void {
  const filter = eyeComfort.warmth[level] ?? 'none';
  document.documentElement.style.setProperty('--syn-eye-filter', filter);
}

export function applyDensity(density: AppSettings['density']): void {
  document.documentElement.setAttribute('data-density', density);
}

export function applyAll(settings: AppSettings, resolved: ResolvedTheme): void {
  applyTheme(resolved);
  applyFontScheme(settings.fontScheme);
  applyEyeComfort(settings.eyeComfort);
  applyDensity(settings.density);
}
