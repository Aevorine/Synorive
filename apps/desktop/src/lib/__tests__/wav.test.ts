/**
 * 录音转 wav
 * ====================================================================
 * 这段代码错了的表现是**引擎那边一句"音频格式不对"**，或者更糟 ——
 * 格式头蒙对了但样点错了，转写返回一串乱码，而用户只会觉得
 * "这个语音功能不准"。所以头里每一个字段都得单独钉死。
 */

import { describe, expect, it } from 'vitest';
import { encodeWav, isSilent, TARGET_RATE } from '../wav';

async function bytesOf(b: Blob): Promise<DataView> {
  return new DataView(await b.arrayBuffer());
}

const ascii = (v: DataView, at: number, n: number) =>
  Array.from({ length: n }, (_, i) => String.fromCharCode(v.getUint8(at + i))).join('');

describe('encodeWav', () => {
  it('44 字节头 + 每个样点 2 字节', async () => {
    const v = await bytesOf(encodeWav(new Float32Array(100)));
    expect(v.byteLength).toBe(44 + 200);
  });

  it('🔴 头里的每个字段都要对 —— 错一个引擎就只会回一句"格式不对"', async () => {
    const v = await bytesOf(encodeWav(new Float32Array(8)));
    expect(ascii(v, 0, 4)).toBe('RIFF');
    expect(v.getUint32(4, true)).toBe(36 + 16); // 文件长度 - 8
    expect(ascii(v, 8, 4)).toBe('WAVE');
    expect(ascii(v, 12, 4)).toBe('fmt ');
    expect(v.getUint32(16, true)).toBe(16); // PCM 子块长度
    expect(v.getUint16(20, true)).toBe(1); // 1 = 无压缩
    expect(v.getUint16(22, true)).toBe(1); // 单声道
    expect(v.getUint32(24, true)).toBe(TARGET_RATE);
    expect(v.getUint32(28, true)).toBe(TARGET_RATE * 2); // 字节率
    expect(v.getUint16(32, true)).toBe(2); // 块对齐
    expect(v.getUint16(34, true)).toBe(16); // 位深
    expect(ascii(v, 36, 4)).toBe('data');
    expect(v.getUint32(40, true)).toBe(16);
  });

  it('采样率必须是 16000 —— 引擎那边写死了，两边不一致就直接报错', async () => {
    expect(TARGET_RATE).toBe(16000);
    const v = await bytesOf(encodeWav(new Float32Array(4)));
    expect(v.getUint32(24, true)).toBe(16000);
  });

  it('样点是 16 位小端，正负都按各自的满量程换算', async () => {
    const v = await bytesOf(encodeWav(new Float32Array([0, 1, -1])));
    expect(v.getInt16(44, true)).toBe(0);
    expect(v.getInt16(46, true)).toBe(32767);
    expect(v.getInt16(48, true)).toBe(-32768);
  });

  it('🔴 超出 ±1 的样点要先夹住 —— 不夹会绕回成刺耳的爆音，整句识别失败', async () => {
    const v = await bytesOf(encodeWav(new Float32Array([2.5, -3.7])));
    expect(v.getInt16(44, true)).toBe(32767);
    expect(v.getInt16(46, true)).toBe(-32768);
  });

  it('空录音也能编出一个合法的 wav，不抛异常', async () => {
    const v = await bytesOf(encodeWav(new Float32Array(0)));
    expect(v.byteLength).toBe(44);
    expect(v.getUint32(40, true)).toBe(0);
  });
});

describe('isSilent', () => {
  it('全零算静音', () => {
    expect(isSilent(new Float32Array(1000))).toBe(true);
  });

  it('只要有一个样点够响就不算静音 —— 按峰值判，不按平均', () => {
    const s = new Float32Array(1000);
    s[500] = 0.4;
    expect(isSilent(s)).toBe(false);
  });

  it('极轻微的底噪仍算静音，别让用户等一次注定为空的转写', () => {
    const s = new Float32Array(1000).fill(0.001);
    expect(isSilent(s)).toBe(true);
  });

  it('负半周也要算进去 —— 只看正值会把半数波形当成静音', () => {
    const s = new Float32Array(10);
    s[3] = -0.9;
    expect(isSilent(s)).toBe(false);
  });
});
