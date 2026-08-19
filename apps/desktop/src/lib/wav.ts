/**
 * 把浏览器录到的音频转成引擎认的 wav（16kHz 单声道 16 位）
 * ============================================================
 * 🔴 **必须在前端转好。** 引擎那边的 `read_wav_mono16k` 只收这一种格式，
 *    而 MediaRecorder 吐出来的是 webm/opus —— 直接传过去只会得到一句
 *    "音频格式不对"，用户看不懂，也不知道该怎么办。
 *
 * 🔴 **不引第三方编码库。** WebAudio 已经能解码并重采样，
 *    自己写 44 字节的 wav 头一共二十行 —— 为这点事多背一个依赖不划算，
 *    而且录音这条路上多一个第三方库就多一份"录音去了哪"的解释成本。
 */

/** 引擎那边写死的采样率，两边必须一致 */
export const TARGET_RATE = 16000;

/** 把任意浏览器录音 Blob 解码并重采样成 16k 单声道的 Float32 */
export async function decodeToMono16k(blob: Blob): Promise<Float32Array> {
  const bytes = await blob.arrayBuffer();
  // 先用默认采样率解码，再用 OfflineAudioContext 重采样 ——
  // 直接拿 16000 建 AudioContext 在部分浏览器上会被静默改回 48000
  const tmp = new AudioContext();
  let decoded: AudioBuffer;
  try {
    decoded = await tmp.decodeAudioData(bytes);
  } finally {
    void tmp.close();
  }

  const frames = Math.max(1, Math.ceil((decoded.duration * TARGET_RATE) | 0));
  const off = new OfflineAudioContext(1, frames, TARGET_RATE);
  const src = off.createBufferSource();
  src.buffer = decoded;
  src.connect(off.destination);
  src.start();
  const out = await off.startRendering();
  return out.getChannelData(0).slice();
}

/** Float32 [-1,1] → 16 位小端 wav（含 44 字节头） */
export function encodeWav(samples: Float32Array, rate = TARGET_RATE): Blob {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const ascii = (at: number, s: string) => {
    for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i));
  };

  ascii(0, 'RIFF');
  v.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, 'WAVE');
  ascii(12, 'fmt ');
  v.setUint32(16, 16, true); // PCM 子块长度
  v.setUint16(20, 1, true); // 1 = 无压缩 PCM
  v.setUint16(22, 1, true); // 单声道
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); // 字节率 = 采样率 × 声道 × 位深/8
  v.setUint16(32, 2, true); // 块对齐
  v.setUint16(34, 16, true); // 位深
  ascii(36, 'data');
  v.setUint32(40, samples.length * 2, true);

  for (let i = 0; i < samples.length; i++) {
    // 先夹再乘：不夹的话超过 1 的样点会绕回成刺耳的爆音，
    // 而爆音会让识别整句失败，表现为"说了话但识别是空的"
    const s = Math.max(-1, Math.min(1, samples[i]!));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: 'audio/wav' });
}

/** 一句话有没有声音。全是静音时提前告诉用户，别让他等转写转出个空字符串 */
export function isSilent(samples: Float32Array, threshold = 0.005): boolean {
  let peak = 0;
  for (let i = 0; i < samples.length; i++) {
    const a = Math.abs(samples[i]!);
    if (a > peak) peak = a;
  }
  return peak < threshold;
}
