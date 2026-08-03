"""
定向源预设 —— S8
====================================================================
**这是「自动排除虚假内容」里最省力、也最有效的一招**：与其搜回一堆
垃圾再去鉴别，不如一开始就只问权威站点。鉴伪是事后补救，定向是事前预防。

实现上就是给查询加 `site:` 限定。所有主流引擎都支持这个语法，
所以这一层**不需要任何引擎侧改动** —— 它只是改查询词。

**为什么不做成"过滤结果"**：搜回 20 条再滤掉 18 条，等于把召回额度
浪费掉了。加 `site:` 是让引擎在它那一侧就只返回这些站的内容，
同样的 20 条额度全花在你想要的站上。

**代价必须说清楚**：开了预设就搜不到预设之外的东西。所以每个预设都
带一句 `caveat`，界面直接显示 —— 用户得知道自己关掉了什么。
`site:` 一次能带几个站是有上限的（实测超过 8 个后多数引擎会忽略或报错），
所以每个预设的域名列表都刻意控制在 8 个以内。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourcePreset:
    id: str
    label: str
    sites: list[str] = field(default_factory=list)
    why: str = ""
    caveat: str = ""
    #: 建议配合哪几家引擎。空 = 不限制
    prefer_engines: list[str] = field(default_factory=list)

    def apply(self, query: str) -> str:
        if not self.sites:
            return query
        clause = " OR ".join(f"site:{s}" for s in self.sites[:8])
        return f"{query} ({clause})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "sites": self.sites,
            "why": self.why, "caveat": self.caveat,
            "preferEngines": self.prefer_engines,
        }


PRESETS: list[SourcePreset] = [
    SourcePreset(
        id="official-docs",
        label="官方文档",
        sites=[
            "docs.python.org", "developer.mozilla.org", "learn.microsoft.com",
            "docs.oracle.com", "kernel.org", "postgresql.org", "sqlite.org", "w3.org",
        ],
        why="一手资料，版本明确，不会有二手教程那种「照着做发现 API 早改了」",
        caveat="只覆盖内置的这几家官方站；你要的软件不在里面就搜不到东西",
    ),
    SourcePreset(
        id="academic",
        label="学术论文",
        sites=[
            "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "nature.com", "science.org",
            "ieee.org", "acm.org", "springer.com", "biorxiv.org",
        ],
        why="同行评审或预印本，有 DOI 可追溯",
        caveat="预印本没经过评审；且这条只搜网页，要结构化字段（DOI/被引数）请用「文献」模式",
    ),
    SourcePreset(
        id="gov",
        label="政府与标准",
        sites=["gov.cn", "gov.uk", "europa.eu", "who.int", "iso.org", "ietf.org", "nist.gov"],
        why="政策、法规、标准的原文出处，不经过媒体转述",
        caveat="更新慢；且政府站的站内搜索质量普遍不好，可能要换词多试几次",
    ),
    SourcePreset(
        id="code",
        label="代码与工程",
        sites=[
            "github.com", "gitlab.com", "stackoverflow.com",
            "raw.githubusercontent.com", "pypi.org", "npmjs.com", "crates.io",
        ],
        why="真实可运行的代码和 issue 讨论，比博客里的片段可信",
        caveat="issue 里的说法可能已被后续提交推翻，注意看日期",
    ),
    SourcePreset(
        id="cn-media",
        label="中文主流媒体",
        sites=[
            "xinhuanet.com", "people.com.cn", "thepaper.cn",
            "caixin.com", "yicai.com", "stcn.com",
        ],
        why="中文时事与产业信息，有编辑把关",
        caveat="媒体报道是二手信息，涉及数据时最好回到原始报告",
    ),
    SourcePreset(
        id="factcheck",
        label="事实核查",
        sites=[
            "snopes.com", "factcheck.org", "politifact.com",
            "fullfact.org", "piyao.org.cn", "reuters.com",
        ],
        why="专门做辟谣与核查的机构。**查一个说法是不是谣言时先来这里**",
        caveat="覆盖以英文和重大公共议题为主，小众话题多半没人核查过",
    ),
]

_BY_ID = {p.id: p for p in PRESETS}


def get_preset(preset_id: str | None) -> SourcePreset | None:
    return _BY_ID.get(preset_id or "")


def apply_preset(query: str, preset_id: str | None) -> tuple[str, SourcePreset | None]:
    """返回 `(改写后的查询, 用到的预设)`。预设不存在就原样返回，不报错。"""
    p = get_preset(preset_id)
    return (p.apply(query), p) if p else (query, None)


def describe_presets() -> list[dict[str, Any]]:
    return [p.to_dict() for p in PRESETS]
