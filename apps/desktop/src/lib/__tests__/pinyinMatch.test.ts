/**
 * 命令面板的拼音匹配
 * ====================================================================
 * 原来每条命令手写一个 `py: 'qlczt'`。问题不在于麻烦，在于**它会被忘**：
 * 新加一条命令忘了写 py，那条就永远搜不到 —— 不报错、不告警，
 * 只是用户打拼音时它不出现。这组测试钉住"从标签自动算"这条路。
 *
 * 另一条不能破的：**精确匹配全部排在拼音匹配之前**。混排会让候选第一条
 * 随输入飘忽，而那样用户就不敢闭眼按回车 —— 面板就白做了。
 */

import { describe, expect, it } from 'vitest';
import { fuzzyScore, pinyinKeys } from '../pinyinMatch';

describe('pinyinKeys', () => {
  it('算得出全拼和首字母（取每位第一个读音）', () => {
    const { full, initials } = pinyinKeys('清理重复图');
    // 「重」的第一个读音是 zhong；真正的匹配会把 chong 也试一遍，见下面那条
    expect(full).toBe('qinglizhongfutu');
    expect(initials).toBe('qlzft');
  });

  it('🔴 多音字的其余读音也在表里 —— 否则 chongfu 一条都中不了', () => {
    expect(fuzzyScore('chongfu', '重复的图')).not.toBeNull();
    expect(fuzzyScore('zhongfu', '重复的图')).not.toBeNull();
  });

  it('英文数字原样保留 —— 英文命令名照样能搜', () => {
    expect(pinyinKeys('Ctrl K').full).toBe('ctrlk');
  });

  it('标点空格一律丢掉 —— 用户打拼音时不会去打顿号', () => {
    expect(pinyinKeys('排序改为「均衡」').full).toBe('paixugaiweijunheng');
  });
});

describe('fuzzyScore', () => {
  it('全拼命中', () => {
    expect(fuzzyScore('qingli', '清理重复图')).not.toBeNull();
  });

  it('🔴 按直觉打的首字母命中（重=chong 那一读）', () => {
    expect(fuzzyScore('qlcft', '清理重复图')).not.toBeNull();
    // 另一个读音也认，用户不必知道哪个是"对的"
    expect(fuzzyScore('qlzft', '清理重复图')).not.toBeNull();
  });

  it('跳着打首字母也认', () => {
    expect(fuzzyScore('qct', '清理重复图')).not.toBeNull();
  });

  it('打半个音节也认 —— 用户不会每次都打完整', () => {
    expect(fuzzyScore('qing', '清理重复图')).not.toBeNull();
  });

  it('完全不沾边的返回 null', () => {
    expect(fuzzyScore('zzzzzz', '清理重复图')).toBeNull();
  });

  it('🔴 精确匹配排在拼音之前', () => {
    const exact = fuzzyScore('清理', '清理重复图')!;
    const py = fuzzyScore('qingli', '清理重复图')!;
    expect(exact).toBeLessThan(py);
  });

  it('🔴 前缀排在包含之前', () => {
    const prefix = fuzzyScore('清理', '清理重复图')!;
    const contains = fuzzyScore('重复', '清理重复图')!;
    expect(prefix).toBeLessThan(contains);
  });

  it('首字母排在全拼之前 —— 打首字母的人更清楚自己要什么', () => {
    expect(fuzzyScore('qlcft', '清理重复图')!).toBeLessThan(
      fuzzyScore('qinglichongfutu', '清理重复图')!,
    );
  });

  it('🔴 一条命令都不写 py 字段也能被拼音搜到 —— 这是这套东西存在的理由', () => {
    // 模拟"新加的命令，作者忘了写 py"：导入生成记录 -> dao ru sheng cheng ji lu
    expect(fuzzyScore('drscjl', '导入生成记录')).not.toBeNull();
    expect(fuzzyScore('daoru', '导入生成记录')).not.toBeNull();
  });

  it('空查询命中一切（面板刚打开时列全部命令）', () => {
    expect(fuzzyScore('', '任意命令')).toBe(0);
  });

  it('说明文字也参与匹配，但排在最后', () => {
    const byHint = fuzzyScore('缩略图', '清理重复图', '按缩略图找出重复的图片')!;
    const byLabel = fuzzyScore('清理', '清理重复图', '按缩略图找出重复的图片')!;
    expect(byLabel).toBeLessThan(byHint);
  });

  it('英文命令名直接匹配', () => {
    expect(fuzzyScore('ctrl', 'Ctrl K 打开输入区')).toBe(0);
  });
});
