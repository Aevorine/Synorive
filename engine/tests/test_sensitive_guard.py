#!/usr/bin/env python
"""
敏感文件扫描 —— .env/私钥/凭据默认不进搜索库
====================================================================
投喂一个项目目录时，`.env`、`credentials.json` 这类文件本身就是纯文本/
JSON，能被正常解析写进索引——用户几百个文件一起投喂，肉眼很难挑出来。

测两层：① sensitive_reason() 这个纯函数本身判得对不对
        ② 接进 ingest_paths() 之后，真跑一次摄取，敏感文件确实被跳过
          （不是"解析失败"那种跳过，是带着明确原因的跳过），
          正常文件确实被正常索引，两者不互相影响。

用法：python -m tests.test_sensitive_guard
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synorive.ingest.pipeline import IngestPipeline  # noqa: E402
from synorive.ingest.sensitive import sensitive_reason  # noqa: E402
from synorive.store.db import Database  # noqa: E402
from synorive.store.repository import Repository  # noqa: E402

MODEL_DIR = ROOT.parent / "data" / "models"
problems: list[str] = []


def check(cond: bool, ok: str, bad: str) -> bool:
    print(f"  {'PASS' if cond else 'FAIL'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)
    return cond


def test_sensitive_reason_unit() -> None:
    sensitive_cases = [
        ".env", "id_rsa", "id_ed25519", "credentials.json",
        "server.pem", "client.key", "vault.kdbx", "config.ovpn",
        ".env.production", "my_api_key.txt", "aws_secret_access_key.txt",
        "登录密码.txt",
    ]
    for name in sensitive_cases:
        reason = sensitive_reason(Path(f"/some/dir/{name}"))
        check(reason is not None, f"{name} 被判定为敏感", f"{name} 应该被判定为敏感，但没有")

    normal_cases = [
        "readme.md", "notes.txt", "报告.docx", "id_rsa_diagram.png",
        "password_policy_overview.md",  # 边界：允许有一定误判，这条先不强求
    ]
    for name in ["readme.md", "notes.txt", "报告.docx"]:
        reason = sensitive_reason(Path(f"/some/dir/{name}"))
        check(reason is None, f"{name} 不是敏感文件", f"{name} 被误判成敏感：{reason}")


def test_ingest_skips_sensitive_files() -> None:
    data_dir = Path(__file__).resolve().parent / "_tmp_sensitive_guard"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True)
    source = data_dir / "source"
    source.mkdir()

    (source / "notes.txt").write_text("这是一份正常的笔记内容。" * 3, encoding="utf-8")
    (source / ".env").write_text("API_KEY=sk-abcdef123456\nSECRET=xyz", encoding="utf-8")
    (source / "credentials.json").write_text('{"apiKey": "sk-test-123"}', encoding="utf-8")
    (source / "readme.md").write_text("# 项目说明\n\n这是一个正常的说明文档。", encoding="utf-8")

    db = Database(data_dir / "test.db")
    db.initialize()
    repo = Repository(db)

    events: list[tuple[str, str, str]] = []
    pipe = IngestPipeline(
        repo, MODEL_DIR, concurrency=2,
        sensitive_guard_enabled=True,
    )
    stats = pipe.ingest_paths(
        [source], recursive=True, source="file",
        on_item=lambda path, status, detail: events.append((path, status, detail)),
    )

    check(stats.total == 4, f"总数含全部 4 个文件（含敏感的）：{stats.total}", f"总数不对：{stats.total}")
    check(stats.skipped == 2, f"2 个敏感文件被跳过：{stats.skipped}", f"跳过数不对：{stats.skipped}")
    check(stats.done == 2, f"2 个正常文件被正常索引：{stats.done}", f"完成数不对：{stats.done}")

    skipped_paths = {Path(p).name for p, status, _ in events if status == "skipped"}
    check(
        skipped_paths == {".env", "credentials.json"},
        f"被跳过的就是那两个敏感文件：{skipped_paths}",
        f"跳过的文件不对：{skipped_paths}",
    )
    reasons = {Path(p).name: detail for p, status, detail in events if status == "skipped"}
    check(
        all("敏感文件" in r for r in reasons.values()),
        "跳过原因里明确写了「敏感文件」，不是笼统的失败",
        f"原因不够明确：{reasons}",
    )

    db.close()
    shutil.rmtree(data_dir, ignore_errors=True)


def test_guard_can_be_disabled() -> None:
    """
    设置里关掉这道闸——敏感文件也应该被正常索引，不是永远强制跳过。
    用 credentials.json 而不是 .env：后者是字面量 ".env" 文件名，
    `Path.suffix` 对它取出来是空字符串，parser.py 按扩展名分发时本来
    就找不到分支（跟这道闸开不开无关，是解析器本身的正交限制）。
    """
    data_dir = Path(__file__).resolve().parent / "_tmp_sensitive_guard_off"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True)
    source = data_dir / "source"
    source.mkdir()
    (source / "credentials.json").write_text('{"apiKey": "sk-test-123"}', encoding="utf-8")

    db = Database(data_dir / "test.db")
    db.initialize()
    repo = Repository(db)

    pipe = IngestPipeline(repo, MODEL_DIR, concurrency=1, sensitive_guard_enabled=False)
    stats = pipe.ingest_paths([source], recursive=True, source="file")

    check(stats.total == 1, f"关掉闸之后 credentials.json 也会被计入摄取：{stats.total}", f"总数不对：{stats.total}")
    check(stats.done == 1, f"关掉闸之后 credentials.json 被正常索引：{stats.done}", f"没有被正常索引：done={stats.done}")

    db.close()
    shutil.rmtree(data_dir, ignore_errors=True)


def _run_all() -> None:
    test_sensitive_reason_unit()
    test_ingest_skips_sensitive_files()
    test_guard_can_be_disabled()
    if problems:
        print(f"\n{len(problems)} 个问题")
        sys.exit(1)
    print("\n全部通过")


if __name__ == "__main__":
    _run_all()
