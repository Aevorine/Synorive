"""
敏感文件识别 —— 投喂目录时默认不把密钥/凭据这类东西送进搜索库
====================================================================
问题不是"这个文件解析不了"，是"这个文件解析得了"：`.env`、`credentials.json`
这类文件本身就是纯文本/JSON，能被正常解析、切块、写进 FTS 索引，甚至送去
embedding（如果开了云端推理，原文还会被发出去）。用户投喂一个项目目录时，
这些文件混在几百个正常文档里，肉眼很难在投喂前逐个排查。

这里不做"拦截 = 报错"，而是"默认跳过 = 走已有的 skipped 清单"——
和其它跳过原因（解析失败、格式不支持）走的是同一条路径，F2 驾驶舱本来就会
把跳过的文件和原因列出来，不需要新建一套 UI。用户要真想索引，把那个文件
单独投喂（不经过目录展开）就行，或者在设置里把这道闸整个关掉。
"""

from __future__ import annotations

import re
from pathlib import Path

#: 精确文件名匹配（大小写不敏感）。SSH 私钥默认没有扩展名，只能按名字认。
_SENSITIVE_NAMES = {
    ".env", ".npmrc", ".netrc", ".pgpass",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "credentials.json", "credentials.csv",
    "login data", "cookies", "web data",  # Chrome/Edge 浏览器profile 常见文件名
}

#: 扩展名匹配：私钥/证书/密码库/VPN 配置这类格式，内容本身就是密钥材料
_SENSITIVE_EXT = {
    ".pem", ".key", ".pfx", ".p12", ".kdbx", ".ovpn", ".ppk",
}

#: 文件名前缀匹配：`.env.local` / `.env.production` 这类变体
_SENSITIVE_PREFIXES = (".env.",)

#: 文件名里出现这些词，大概率是密钥/凭据类文件（宁可多跳过，不可漏判——
#: 跳过的代价是"用户手动加回来"，漏判的代价是"密钥被塞进搜索库甚至发去云端"）
_SENSITIVE_KEYWORDS = re.compile(
    r"(secret|password|passwd|api[_-]?key|access[_-]?token|private[_-]?key|"
    r"credential|凭据|密钥|密码)",
    re.IGNORECASE,
)


def sensitive_reason(path: Path) -> str | None:
    """
    这个文件是不是"默认不该被索引"的敏感文件。是的话返回一句人话原因，
    不是就返回 None。只看文件名/路径，不读文件内容——性能考虑，
    也因为"读内容判断是不是密钥"本身不可靠，容易两边都出错。
    """
    name = path.name.lower()
    stem = path.stem.lower()

    if name in _SENSITIVE_NAMES or stem in _SENSITIVE_NAMES:
        return f"文件名匹配已知的密钥/凭据文件（{path.name}）"

    if path.suffix.lower() in _SENSITIVE_EXT:
        return f"文件类型是密钥/证书/密码库（{path.suffix}）"

    if any(name.startswith(prefix) for prefix in _SENSITIVE_PREFIXES):
        return f"文件名是环境变量配置的变体（{path.name}）"

    if _SENSITIVE_KEYWORDS.search(path.stem):
        return f"文件名包含敏感关键词（{path.name}）"

    return None
