/**
 * 出稿前打码
 * ====================================================================
 * 两条红线：
 *   ① 该遮的必须遮住 —— 漏一个身份证号的代价，比多遮一个订单号大得多。
 *   ② **打码之后原文的其余部分要一个字不差**。这是"只摘录不改写"这条
 *      产品约束的延伸：遮蔽是允许的，悄悄改写别的地方不是。
 */

import { describe, expect, it } from 'vitest';
import { applyRedactions, findSensitive, summarize } from '../redact';

describe('findSensitive', () => {
  it('身份证号', () => {
    const hits = findSensitive('身份证 110101199003078515 就是这个');
    expect(hits).toHaveLength(1);
    expect(hits[0]!.kind).toBe('身份证号');
  });

  it('手机号', () => {
    expect(findSensitive('联系我 13812345678')[0]!.kind).toBe('手机号');
  });

  it('银行卡号', () => {
    expect(findSensitive('卡号 6222021234567890123')[0]!.kind).toBe('银行卡号');
  });

  it('API 密钥（有明确前缀的那几类）', () => {
    const kinds = findSensitive(
      'sk-abcdefghijklmnopqrstuvwx 和 ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345',
    ).map((h) => h.kind);
    expect(kinds).toEqual(['API 密钥', 'API 密钥']);
  });

  it('私钥整块', () => {
    const t = '-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----';
    expect(findSensitive(t)[0]!.kind).toBe('私钥');
  });

  it('网址里的令牌', () => {
    const hits = findSensitive('https://x.com/a?token=SECRETVALUE&b=1');
    expect(hits[0]!.kind).toBe('网址里的令牌');
  });

  it('🔴 更具体的规则先赢 —— 说不出是什么的打码，用户没法判断该不该放回来', () => {
    // 18 位身份证号同时也符合"长数字串"，必须报成身份证而不是银行卡
    expect(findSensitive('110101199003078515')[0]!.kind).toBe('身份证号');
  });

  it('普通数字不误报', () => {
    expect(findSensitive('总共 12345 条，占比 67.8%')).toHaveLength(0);
    expect(findSensitive('2026-08-19 的会议')).toHaveLength(0);
  });

  it('反复扫同一段文字结果一样 —— 正则的 lastIndex 不许残留', () => {
    const t = '13812345678 和 13987654321';
    const a = findSensitive(t);
    const b = findSensitive(t);
    expect(a).toHaveLength(2);
    expect(b).toHaveLength(2);
  });
});

describe('applyRedactions', () => {
  it('遮住了，且保留头尾便于辨认', () => {
    const t = '手机 13812345678 结束';
    const out = applyRedactions(t, findSensitive(t));
    expect(out).toBe('手机 138****5678 结束');
  });

  it('🔴 除了被遮的那几段，其余一个字都不能变', () => {
    const t = '前面的话。身份证 110101199003078515，中间的话。手机 13812345678。后面的话。';
    const hits = findSensitive(t);
    const out = applyRedactions(t, hits);
    expect(out.startsWith('前面的话。身份证 ')).toBe(true);
    expect(out).toContain('，中间的话。手机 ');
    expect(out.endsWith('。后面的话。')).toBe(true);
    expect(out).not.toContain('110101199003078515');
    expect(out).not.toContain('13812345678');
  });

  it('用户说不用遮的那一处就原样留着', () => {
    const t = '13812345678 和 13987654321';
    const hits = findSensitive(t);
    const out = applyRedactions(t, hits, new Set([0]));
    expect(out).toContain('13812345678'); // 第 0 处被跳过
    expect(out).not.toContain('13987654321');
  });

  it('按下标跳过而不是按内容 —— 同样的号码在不同位置可能一个该遮一个不该', () => {
    const t = '13812345678 前 13812345678 后';
    const hits = findSensitive(t);
    expect(hits).toHaveLength(2);
    const out = applyRedactions(t, hits, new Set([1]));
    expect(out).toBe('138****5678 前 13812345678 后');
  });

  it('没有命中时原文原样返回', () => {
    const t = '这段话里什么敏感的都没有';
    expect(applyRedactions(t, findSensitive(t))).toBe(t);
  });
});

describe('summarize', () => {
  it('说人话', () => {
    const t = '13812345678 和 13987654321 还有 110101199003078515';
    // 命中是按**出现位置**排的，所以摘要也按位置先后 —— 稳定可预期
    expect(summarize(findSensitive(t))).toBe('手机号 2 处、身份证号 1 处');
  });

  it('没有命中时也说清楚', () => {
    expect(summarize([])).toBe('没扫到需要遮蔽的内容');
  });
});
