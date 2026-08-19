import { useEffect, useRef, useState } from 'react';
import { Loader2, Mic, Square } from 'lucide-react';
import { api } from '../lib/api';
import { decodeToMono16k, encodeWav, isSilent } from '../lib/wav';

/**
 * 语音提问（提案 38）
 * ============================================================
 * 按住说一句，转成文字填进搜索框。
 *
 * 🔴 **只用本机模型，录音一个字节都不出这台电脑。**
 *    浏览器自带的 SpeechRecognition 走的是厂商云服务 —— 对一个
 *    "东西全在你自己电脑上"的软件来说，把用户说的话传出去是原则性问题。
 *    模型没装就把按钮灰掉并说明，**绝不偷偷退回云端**。
 *
 * 🔴 **识别结果只填进搜索框，不自动发起搜索。**
 *    识别难免出错，直接拿错的词去搜，用户会以为是搜索坏了 ——
 *    而他根本没机会看到自己被识别成了什么。
 *
 * 🔴 **录音时界面上必须有明显的、动着的提示。** 一个悄悄开着麦克风的软件
 *    是不能接受的，哪怕录音确实没往外发。
 */
const MAX_SECONDS = 60;

export function VoiceButton({ onText }: { onText: (text: string) => void }) {
  const [ready, setReady] = useState<boolean | null>(null);
  const [why, setWhy] = useState<string | null>(null);
  const [rec, setRec] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [secs, setSecs] = useState(0);

  const chunks = useRef<Blob[]>([]);
  const recorder = useRef<MediaRecorder | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    api.voice
      .status()
      .then((s) => {
        if (!alive) return;
        setReady(s.available);
        setWhy(s.reason);
      })
      .catch(() => alive && setReady(false));
    return () => {
      alive = false;
      // 组件被卸载时必须把麦克风放掉，否则系统托盘上的录音指示会一直亮着
      recorder.current?.stream.getTracks().forEach((t) => t.stop());
      if (timer.current) window.clearInterval(timer.current);
    };
  }, []);

  const stop = () => {
    recorder.current?.state === 'recording' && recorder.current.stop();
  };

  const start = async () => {
    setErr(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setErr('拿不到麦克风。系统设置里可能没给这个软件麦克风权限。');
      return;
    }

    const mr = new MediaRecorder(stream);
    recorder.current = mr;
    chunks.current = [];
    mr.ondataavailable = (e) => e.data.size > 0 && chunks.current.push(e.data);
    mr.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      if (timer.current) window.clearInterval(timer.current);
      setRec(false);
      setSecs(0);
      setBusy(true);
      try {
        const raw = new Blob(chunks.current, { type: mr.mimeType });
        const pcm = await decodeToMono16k(raw);
        if (isSilent(pcm)) {
          setErr('没录到声音，麦克风可能被静音了。');
          return;
        }
        const r = await api.voice.transcribe(encodeWav(pcm));
        if (r.empty) {
          setErr('这段没识别出文字，再说一次试试。');
          return;
        }
        onText(r.text); // 只填进去，不替用户按下搜索
      } catch (e) {
        setErr(e instanceof Error ? e.message : '转写没成功');
      } finally {
        setBusy(false);
      }
    };

    mr.start();
    setRec(true);
    setSecs(0);
    timer.current = window.setInterval(() => {
      setSecs((n) => {
        if (n + 1 >= MAX_SECONDS) stop(); // 到点自动收，别让它一直开着
        return n + 1;
      });
    }, 1000);
  };

  if (ready === null) return null;

  return (
    <>
      <button
        className={rec ? 'btn btn--sm btn--primary' : 'btn btn--sm'}
        onClick={() => (rec ? stop() : void start())}
        disabled={!ready || busy}
        title={
          ready
            ? rec
              ? `正在录音（${secs}/${MAX_SECONDS} 秒）。点一下停止并转写`
              : '说一句话，转成文字填进搜索框。录音只在本机转写，不上传'
            : (why ?? '本地语音模型还没装')
        }
        aria-label="语音提问"
      >
        {busy ? (
          <Loader2 size={13} strokeWidth={1.8} className="spin" />
        ) : rec ? (
          <Square size={13} strokeWidth={1.8} />
        ) : (
          <Mic size={13} strokeWidth={1.8} />
        )}
        {rec ? `${secs}s` : ''}
      </button>
      {rec && <span className="voice__live" aria-live="polite">正在录音</span>}
      {err && <span className="voice__err">{err}</span>}
    </>
  );
}
