"""网页抓取与存档 C11。不联网的部分先全测，联网的单独一段可跳过。"""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING)

from synorive.ingest.web import (  # noqa: E402
    _extract,
    extract_urls,
    fetch,
    is_safe_url,
    is_url,
    url_fingerprint,
)

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)


def main() -> int:
    print("=" * 74)
    print("① URL 识别与安全判定")
    print("=" * 74)
    for s, want in [
        ("https://example.com/a", True),
        ("http://example.com", True),
        ("ftp://example.com", False),
        ("不是链接", False),
        ("", False),
        ("https://example.com/带 空格", False),
    ]:
        got = is_url(s)
        mark = "✓" if got == want else "✗"
        print(f"  {mark} is_url({s!r:34}) = {got}")
        if got != want:
            failures.append(f"is_url({s!r}) 应为 {want}")

    print()
    print("  内网地址必须挡住（剪贴板哨兵是自动抓的，不挡会去访问路由器后台）：")
    for s, safe in [
        ("https://example.com", True),
        ("http://127.0.0.1:8080/admin", False),
        ("http://localhost/x", False),
        ("http://192.168.1.1/", False),
        ("http://10.0.0.5/", False),
        ("http://172.16.3.4/", False),
        ("http://169.254.1.1/", False),
    ]:
        ok, why = is_safe_url(s)
        mark = "✓" if ok == safe else "✗"
        print(f"  {mark} {s:34} {'放行' if ok else '拦下：' + why[:36]}")
        if ok != safe:
            failures.append(f"is_safe_url({s}) 应为 {safe}")

    print()
    print("=" * 74)
    print("② URL 指纹 —— 追踪参数不同的同一篇文章应算同一条")
    print("=" * 74)
    groups = [
        [
            "https://example.com/post/1",
            "https://example.com/post/1/",
            "https://example.com/post/1?utm_source=weixin",
            "https://example.com/post/1?utm_source=x&utm_campaign=y",
            "https://EXAMPLE.com/post/1",
        ],
        ["https://example.com/post/2", "https://example.com/post/2?page=3"],
    ]
    fps = [[url_fingerprint(u) for u in g] for g in groups]
    same0 = len(set(fps[0])) == 1
    print(f"  组1（同一篇 + 各种追踪参数）指纹一致：{same0}")
    for u, f in zip(groups[0], fps[0]):
        print(f"    {f[:12]}  {u}")
    check(same0, "追踪参数不同的同一篇文章指纹不一致")
    diff = fps[1][0] != fps[1][1]
    print(f"  组2（page 参数是真实差异）指纹不同：{diff}")
    check(diff, "有意义的查询参数被误删了")
    check(fps[0][0] != fps[1][0], "不同文章的指纹撞了")

    print()
    print("=" * 74)
    print("③ 正文提取 —— 导航/广告/页脚必须去掉")
    print("=" * 74)
    HTML = """<!doctype html><html lang="zh"><head>
<title>中文分词的选型 - 技术博客</title>
<meta name="author" content="张三"></head><body>
<nav>首页 归档 关于 订阅 登录 注册 搜索 标签云 友情链接</nav>
<header>技术博客 · 每周更新 · 已有 1024 篇文章</header>
<article>
<h1>中文分词的选型</h1>
<p>实测 SQLite 的 trigram 分词器对两字词命中率为零，因为它要求查询串至少三个字符。</p>
<p>最终方案是入库和查询两侧都过一遍 jieba 分词，再用 unicode61 建倒排索引。</p>
<p>实测 jieba 吞吐 326 千字每秒，加了 24 条领域词典。</p>
</article>
<aside>相关推荐：如何配置 Nginx / Docker 入门 / 十分钟学会 Vim</aside>
<footer>版权所有 2026 · 备案号 XXXXXX · 联系我们 · 隐私政策</footer>
</body></html>"""
    page = _extract(HTML, url="https://example.com/post/fenci")
    print(f"  标题：{page.title}")
    print(f"  正文（{len(page.text)} 字）：{page.text[:120]}")
    print(f"  作者：{page.author}　站点：{page.site}　语言：{page.lang}")

    check("中文分词" in page.title, f"标题没抽对：{page.title}")
    check("trigram" in page.text, "正文没抽到")
    for noise in ("友情链接", "备案号", "十分钟学会 Vim", "隐私政策"):
        if noise in page.text:
            failures.append(f"噪声「{noise}」没被去掉 —— 每个网页都带同样的导航文字会让语义检索废掉")
    print(f"  噪声清除：{'✓' if all(n not in page.text for n in ('友情链接', '备案号', '隐私政策')) else '✗'}")

    print()
    print("=" * 74)
    print("④ 从一段文字里抽链接（剪贴板哨兵 / 聊天记录导入要用）")
    print("=" * 74)
    text = """看看这个 https://example.com/a 还有 http://test.org/b?x=1，
    以及（https://foo.bar/c）。重复的 https://example.com/a 不该出现两次。"""
    urls = extract_urls(text)
    print(f"  抽出 {len(urls)} 条：{urls}")
    check(len(urls) == 3, f"应抽出 3 条不重复的链接，实得 {len(urls)}")
    check(all(not u.endswith(("，", "。", "）")) for u in urls), "链接尾部的中文标点没剥掉")

    print()
    print("=" * 74)
    print("⑤ 真实抓取 + 存档（要联网，失败不算测试失败）")
    print("=" * 74)
    work = Path(tempfile.gettempdir()) / "synorive_webtest"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    p = fetch("https://example.com", archive_dir=work)
    print(f"  状态 {p.status}　标题「{p.title}」　正文 {len(p.text)} 字")
    print(f"  存档 {p.archive_path}")
    print(f"  警告 {p.warnings or '无'}")
    if p.status == 200:
        check(bool(p.archive_path), "抓成功了但没存档")
        if p.archive_path:
            f = work / p.archive_path
            check(f.exists() and f.stat().st_size > 100, "存档文件是空的")
            print(f"  存档大小 {f.stat().st_size} 字节 —— 原网页删了这份还在")
    else:
        print("  （没联网或被墙，跳过这一段的断言）")

    print()
    print("  内网地址不该被抓：")
    bad = fetch("http://127.0.0.1:9/should-not-fetch")
    print(f"    状态 {bad.status}　警告 {bad.warnings}")
    check(bad.status == 0 and bool(bad.warnings), "内网地址没被挡住")

    print()
    print("=" * 74)
    if failures:
        for f in failures:
            print(f"✗ {f}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
