# 造视频测试素材：已知场景切换点 + 已知台词
# 用 Windows 自带的中文语音合成生成语音，这样 ASR 有标准答案可对。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$work = Join-Path $env:TEMP "synorive_videotest"
New-Item -ItemType Directory -Force -Path $work | Out-Null

$ffmpeg = "D:\Files\VideoEditing\ffmpeg\bin\ffmpeg.exe"
if (-not (Test-Path $ffmpeg)) { $ffmpeg = (Get-Command ffmpeg).Source }

# ── ① 中文语音（5 句，每句一个场景）──────────────────────
$lines = @(
    "第一段讲的是中文分词与向量检索的选型。",
    "第二段讲断点续传和并发调度的实现。",
    "第三段讲知识图谱与实体抽取的取舍。",
    "第四段讲视频片段级定位是怎么做的。",
    "第五段讲预算是一万两千元，负责人张伟。"
)
$lines | Set-Content (Join-Path $work "truth.txt") -Encoding UTF8

Add-Type -AssemblyName System.Speech
$syn = New-Object System.Speech.Synthesis.SpeechSynthesizer
$zh = $syn.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq "zh-CN" } | Select-Object -First 1
if ($zh) { $syn.SelectVoice($zh.VoiceInfo.Name) }
$syn.Rate = 0

for ($i = 0; $i -lt $lines.Count; $i++) {
    $p = Join-Path $work ("line{0}.wav" -f $i)
    $syn.SetOutputToWaveFile($p)
    $syn.Speak($lines[$i])
    # 每句后面留 1 秒静音，VAD 才断得开
    $syn.Speak("     ")
}
$syn.SetOutputToNull(); $syn.Dispose()
Write-Output "生成 $($lines.Count) 段语音"

# 拼成一条 16kHz 单声道音轨
$listFile = Join-Path $work "concat.txt"
(0..($lines.Count - 1) | ForEach-Object { "file '" + (Join-Path $work ("line{0}.wav" -f $_)).Replace("\", "/") + "'" }) |
    Set-Content $listFile -Encoding UTF8
& $ffmpeg -hide_banner -loglevel error -f concat -safe 0 -i $listFile -ac 1 -ar 16000 -y (Join-Path $work "speech.wav")
$dur = [double](& "D:\Files\VideoEditing\ffmpeg\bin\ffprobe.exe" -v error -show_entries format=duration -of csv=p=0 (Join-Path $work "speech.wav"))
Write-Output ("语音总长 {0:F1}s" -f $dur)

# ── ② 五个明显不同的场景 ────────────────────────────────
# 每段 5 秒（总 25s）要长于语音总长（约 23.5s），
# 否则下面合成时 -shortest 会把最后一句话截掉，测出来的"转写不全"是假的。
$colors = @("0x1A4C8C", "0x1E9E76", "0xC8871B", "0xA8342A", "0x2B2B2B")
$seg = 5
for ($i = 0; $i -lt 5; $i++) {
    $out = Join-Path $work ("scene{0}.mp4" -f $i)
    & $ffmpeg -hide_banner -loglevel error -f lavfi `
        -i ("color=c={0}:s=960x540:d={1}:r=25" -f $colors[$i], $seg) `
        -vf ("drawtext=text='SCENE {0}':fontcolor=white:fontsize=72:x=(w-tw)/2:y=(h-th)/2" -f ($i + 1)) `
        -c:v libx264 -pix_fmt yuv420p -y $out
}
$vlist = Join-Path $work "vconcat.txt"
(0..4 | ForEach-Object { "file '" + (Join-Path $work ("scene{0}.mp4" -f $_)).Replace("\", "/") + "'" }) |
    Set-Content $vlist -Encoding UTF8
& $ffmpeg -hide_banner -loglevel error -f concat -safe 0 -i $vlist -c copy -y (Join-Path $work "video_only.mp4")

# ── ③ 合成最终视频（画面 + 语音）────────────────────────
$final = Join-Path $work "test_video.mp4"
& $ffmpeg -hide_banner -loglevel error -i (Join-Path $work "video_only.mp4") -i (Join-Path $work "speech.wav") `
    -c:v copy -c:a aac -shortest -y $final

$size = (Get-Item $final).Length
Write-Output ("产物 {0}  {1:N0} 字节" -f $final, $size)
Write-Output ("真实场景切换点：{0}s {1}s {2}s {3}s" -f $seg, ($seg*2), ($seg*3), ($seg*4))
