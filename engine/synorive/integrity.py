"""
引擎源码完整性自校验
====================================================================
引擎的源码是**随包分发的明文 .py**。装好之后往里塞一行代码，就能把用户的
资料悄悄发出去 —— 而且从界面上完全看不出来，功能一切正常。

启动时按打包时生成的清单核对一遍 SHA-256，对不上就说清是哪个文件。

## 说清楚它防什么、不防什么

**防**：装好之后被改。恶意软件、共用电脑的其他人、一个被掉包的更新包。

**不防**：能同时改清单的人。清单和源码放在一起，改了源码顺手改清单就绕过了。
要防那个需要**代码签名**（清单被签进 exe，改了签名就失效）——
所以这一条是和代码签名**配套的**，不是替代品。
把它说成"防篡改"而不加限定，就是在给用户一个假的安全感。

## 为什么默认只警告不拦

清单和源码在同一个目录里，一次不完整的更新、一个被杀软"修复"过的文件，
都会让它对不上。默认直接拒绝启动的话，用户遇到的是"软件打不开了"
而不是"有人动过它" —— 前者的处理方式是卸载重装，后者才是他该知道的事。
所以默认把结论报到 `/status` 和日志里，让界面显示出来；
要硬拦的场合用 `--integrity-strict`。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("synorive.integrity")

MANIFEST_NAME = "integrity.json"


@dataclass
class IntegrityReport:
    #: 有没有清单可核对。打包版有，源码跑的开发环境没有
    available: bool = False
    ok: bool = True
    #: 内容和清单对不上的文件
    modified: list[str] = field(default_factory=list)
    #: 清单里有、磁盘上没有的
    missing: list[str] = field(default_factory=list)
    checked: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "ok": self.ok,
            "checked": self.checked,
            "modified": self.modified[:20],
            "missing": self.missing[:20],
            "note": self.note(),
        }

    def note(self) -> str:
        if not self.available:
            return "没有完整性清单（从源码跑的开发环境本来就没有），这一项跳过"
        if self.ok:
            return f"引擎源码 {self.checked} 个文件与打包时一致"
        bits = []
        if self.modified:
            bits.append(f"{len(self.modified)} 个文件被改过")
        if self.missing:
            bits.append(f"{len(self.missing)} 个文件不见了")
        return (
            "、".join(bits)
            + "。可能是更新没完成、被杀毒软件动过，也可能是真的被人改了。"
            + "重装一次是最省事的处理。"
        )


def check(package_dir: Path | None = None) -> IntegrityReport:
    """核对一遍。**任何异常都不往上抛** —— 自检本身把引擎搞崩是最糟的结果。"""
    pkg = package_dir or Path(__file__).parent
    manifest_path = pkg / MANIFEST_NAME
    rep = IntegrityReport()
    if not manifest_path.exists():
        return rep

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        files: dict[str, str] = data.get("files") or {}
    except Exception as e:  # noqa: BLE001
        log.warning("完整性清单读不了，跳过自检：%s", e)
        return rep

    rep.available = True
    for rel, want in files.items():
        # 清单里的键是 posix 风格；Windows 上要转回本地分隔符
        p = pkg.joinpath(*rel.split("/"))
        if not p.exists():
            rep.missing.append(rel)
            continue
        try:
            got = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError as e:
            log.warning("算不了 %s 的哈希：%s", rel, e)
            continue
        rep.checked += 1
        if got != want:
            rep.modified.append(rel)

    rep.ok = not rep.modified and not rep.missing
    if not rep.ok:
        log.warning("引擎完整性自检没通过：%s", rep.note())
        for f in rep.modified[:10]:
            log.warning("  被改过：%s", f)
        for f in rep.missing[:10]:
            log.warning("  不见了：%s", f)
    else:
        log.info("引擎完整性自检通过（%d 个文件）", rep.checked)
    return rep
