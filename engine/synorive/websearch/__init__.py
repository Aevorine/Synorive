"""
联网搜索层 —— 锚点 10
====================================================================
前面所有功能搜的是「用户自己的资料」：来源可信、内容不会骗人，
唯一的问题是找不着。这一层搜的是**全网**，问题完全反过来 ——
东西一搜一大把，难的是判断哪些能信。

所以这个包分成三块，职责刻意分开：

  engines.py   把各家搜索引擎抹平成同一个接口（**只管拿回来**）
  meta.py      并发调度、熔断、去重折叠、融合排序（**只管排好**）
  trust.py     来源信誉、内容农场、时效、孤证判定（**只管标可信度**）

`trust` 绝不删东西，只打分和打标 —— 是否隐藏由上层按用户设置决定，
且被隐藏的一律进「已排除」抽屉可查可放回（R11）。
这是刻意的：一个悄悄丢结果的搜索工具，比一个啰嗦的搜索工具危险得多。
"""

from .engines import WebResult, all_engines, get_engine
from .meta import MetaSearch, MetaSearchResult

__all__ = ["MetaSearch", "MetaSearchResult", "WebResult", "all_engines", "get_engine"]
