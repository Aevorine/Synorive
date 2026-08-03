"""
E17 —— 端到端加密的信封
====================================================================

## 这套东西**保护什么、不保护什么**（先说清楚，别让人误以为它更强）

**保护**：同步载荷在离开一台设备之后、进入另一台之前，任何中间环节
（局域网上的其他机器、路由器、抓包的人、以及**磁盘上的队列文件**）
拿到的都是密文。密钥只由配对口令派生，两台设备各自算，**从不在网络上传输**。

**不保护**：
  · 已经被攻破的设备。密钥在内存里，能读内存就能读明文 —— 任何 E2E 都一样。
  · 元数据。条目**数量**、**大小**、**同步时间**是看得见的，只有内容看不见。
  · 弱口令。口令是唯一的秘密，`123456` 派生出来的密钥就是弱的。
    PBKDF2 迭代拉到 60 万（见 `_PBKDF2_ITERS`）是为了让暴力破解变慢，不是为了让弱口令变强。
    另外强制了最短 8 位（`MIN_PASSPHRASE_LEN`）—— 那只是个下限，不是"够安全了"的意思。

## 🔴 三条不许违反的

1. **不自己实现密码算法。** AES-GCM 走 `cryptography` 库；没装就**拒绝工作**，
   不降级、不"先用个简单办法顶一下"。自己拼的算法跑起来完全正常、
   不报错、密文看着也像密文，而它根本不安全 —— 这类失败用户永远发现不了。
2. **nonce 绝不重用。** 同一个密钥下重用 nonce，GCM 会**直接泄露明文异或**
   并让攻击者能伪造消息。这里每次 `seal()` 都现取 12 字节随机数，
   不用计数器（计数器要跨进程持久化，一次回滚就毁掉整个密钥）。
3. **认证失败一律当攻击处理**，不去"试试能不能解出点什么"。
   `open_envelope()` 失败就是失败，不返回半截数据。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from typing import Any

#: 信封格式版本。**变了就必须换号** —— 两端版本不一致时要能立刻发现，
#: 而不是解出一堆乱码再猜哪里不对
ENVELOPE_VERSION = 1

#: KDF 迭代数。PBKDF2-HMAC-SHA256，OWASP 2023 建议的量级。
#:
#: 🔴 **为什么是 PBKDF2 而不是 scrypt**（scrypt 抗暴力破解更强，本来是更好的选择）：
#: 对端是 Android。Android 上**没有保证可用的 scrypt 实现** ——
#: `SecretKeyFactory` 不提供，Conscrypt 不暴露，要拉 BouncyCastle 才有。
#: 而 `PBKDF2WithHmacSHA256` 在 Android 8.0+ 和 Python stdlib 上**都是标配**。
#:
#: 选错的后果不是"慢一点"，是**两端派生出完全不同的密钥** ——
#: 表现为"配对显示成功，指纹也对不上（或者更糟：对得上但数据全解不开）"，
#: 而这种故障从任何一条日志上都看不出根因。
#: **互操作的确定性在这里比算法强度更值钱。**
_PBKDF2_ITERS = 600_000
_KEY_LEN = 32  # AES-256

#: 盐长度。盐**不是秘密**，可以明文存、明文传，但必须每对设备各不相同 ——
#: 共用一个盐等于让攻击者可以预先算好一张彩虹表通吃所有用户
SALT_LEN = 16
NONCE_LEN = 12  # GCM 标准 nonce 长度，别改

#: 口令最短长度。8 个字符配上 60 万次 PBKDF2 已经不好暴力破解了；
#: 再低就只是让用户"感觉安全"而已
MIN_PASSPHRASE_LEN = 8


class CryptoUnavailable(RuntimeError):
    """没装 `cryptography`。**这是拒绝而不是降级。**"""


def _aesgcm() -> Any:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:  # pragma: no cover - 取决于装没装
        raise CryptoUnavailable(
            "端到端加密同步要 cryptography 库，现在没装，所以**同步整个不可用**。"
            "装一下：pip install \"synorive[sync]\"　"
            "（这里不提供任何「不加密先用着」的选项 —— 那等于把你的资料明文发到局域网上）"
        ) from e
    return AESGCM


def new_salt() -> str:
    """给一次配对生成盐。十六进制串，方便塞进 JSON 和二维码。"""
    return secrets.token_hex(SALT_LEN)


def derive_key(passphrase: str, salt_hex: str) -> bytes:
    """
    口令 + 盐 → 32 字节密钥。

    用 `hashlib.pbkdf2_hmac`（stdlib，不需要额外依赖）。

    🔴 **两端必须用完全一样的参数**：算法、迭代数、盐、密钥长度、
    以及**口令的编码方式（UTF-8）**。差任何一样都会派生出完全不同的密钥，
    而症状是"配对看起来成功了，之后所有数据都解不开" ——
    所以这些全部写死成常量，一个都不做成可配置项。

    Android 侧对应写法（必须逐字对上）：
        SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
            .generateSecret(PBEKeySpec(pass.toCharArray(), salt, 600_000, 256))
    """
    # 🔴 **口令是这套加密里唯一的秘密**，必须有最低门槛。
    # 只查 `not passphrase` 的话，`"1"` 和 `"    "` 都会通过 ——
    # 而那样派生出来的密钥，暴力破解只要几秒。加密全链路照常工作、
    # 一个错都不报，用户以为自己有端到端加密，实际上等于没有。
    # 这是「运行正常但功能无效」在安全上最贵的一种。
    if not passphrase.strip():
        raise ValueError("配对口令不能为空（纯空格也不行）")
    if len(passphrase) < MIN_PASSPHRASE_LEN:
        raise ValueError(
            f"配对口令太短，至少 {MIN_PASSPHRASE_LEN} 个字符。"
            "这是整套加密里唯一的秘密 —— 短口令能在几秒内被暴力破解，"
            "而那时候加密看起来仍然一切正常"
        )
    salt = bytes.fromhex(salt_hex)
    if len(salt) != SALT_LEN:
        raise ValueError(f"盐长度不对：要 {SALT_LEN} 字节，拿到 {len(salt)}")
    return hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        salt,
        _PBKDF2_ITERS,
        dklen=_KEY_LEN,
    )


def key_fingerprint(key: bytes) -> str:
    """
    密钥指纹，给界面显示"两台设备是不是同一把钥匙"。

    🔴 **是密钥的哈希，不是密钥本身**，可以安全地显示和传输。
    加固定前缀是为了让它和别处的哈希不可能撞上（域分离）。
    """
    return hashlib.sha256(b"synorive-sync-fp-v1" + key).hexdigest()[:16]


def seal(key: bytes, payload: Any, *, aad: bytes = b"") -> dict[str, Any]:
    """
    把任意可 JSON 序列化的东西封成信封。

    `aad` 是"附加认证数据"：不加密但参与认证。把设备 id、版本号这类
    **不该被篡改也不用保密**的东西放进去，攻击者改了它解密就会失败。
    """
    AESGCM = _aesgcm()
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # 🔴 每次都现取随机 nonce。绝不用计数器 —— 计数器要跨进程持久化，
    # 一次文件回滚或者一次进程崩溃重来，就会重用 nonce，而重用 nonce
    # 在 GCM 下是灾难性的（泄露明文异或 + 可伪造）
    nonce = os.urandom(NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, raw, aad or None)
    return {
        "v": ENVELOPE_VERSION,
        "nonce": nonce.hex(),
        "ct": ct.hex(),
        "aad": aad.decode("utf-8") if aad else "",
    }


def open_envelope(key: bytes, env: dict[str, Any]) -> Any:
    """
    拆信封。**认证失败就抛异常，绝不返回半截数据。**

    🔴 版本不认识要**立刻拒绝**，不要"试着按当前版本解一下" ——
    那样最好的情况是解密失败，最坏的情况是解出一堆看似合法的垃圾
    然后被当成真数据写进库里。
    """
    AESGCM = _aesgcm()
    if not isinstance(env, dict):
        raise ValueError("信封不是一个对象")
    v = env.get("v")
    if v != ENVELOPE_VERSION:
        raise ValueError(
            f"信封版本是 {v}，这一端只认 {ENVELOPE_VERSION} —— 两台设备的版本对不上，"
            "先把两边都升到同一版再同步"
        )
    try:
        nonce = bytes.fromhex(str(env["nonce"]))
        ct = bytes.fromhex(str(env["ct"]))
    except (KeyError, ValueError) as e:
        raise ValueError(f"信封字段坏了：{e}") from e
    if len(nonce) != NONCE_LEN:
        raise ValueError("nonce 长度不对")
    aad = str(env.get("aad") or "").encode("utf-8")
    # AESGCM.decrypt 认证失败会抛 InvalidTag。**不要 catch 成 None** ——
    # 认证失败意味着数据被改过或者密钥不对，两种都必须让调用方知道
    raw = AESGCM(key).decrypt(nonce, ct, aad or None)
    return json.loads(raw.decode("utf-8"))


def verify_passphrase(key: bytes, challenge: dict[str, Any]) -> bool:
    """
    配对时验一下两端是不是同一把钥匙，**不泄露口令也不泄露密钥**。

    做法：一端发一个随机数 + 用密钥算的 HMAC，另一端用自己的密钥重算比对。
    🔴 用 `hmac.compare_digest` 而不是 `==` —— 后者是短路比较，
    比较耗时会随匹配前缀长度变化，理论上能被计时攻击一个字节一个字节地问出来。
    """
    try:
        nonce = bytes.fromhex(str(challenge["nonce"]))
        mac = bytes.fromhex(str(challenge["mac"]))
    except (KeyError, ValueError):
        return False
    expect = hmac.new(key, b"synorive-pair-v1" + nonce, hashlib.sha256).digest()
    return hmac.compare_digest(expect, mac)


def make_challenge(key: bytes) -> dict[str, str]:
    """生成一个配对挑战。给对端用 `verify_passphrase` 校验。"""
    nonce = os.urandom(16)
    mac = hmac.new(key, b"synorive-pair-v1" + nonce, hashlib.sha256).digest()
    return {"nonce": nonce.hex(), "mac": mac.hex()}


def crypto_available() -> bool:
    """给状态接口用：不抛异常地问一句"能不能加密"。"""
    try:
        _aesgcm()
        return True
    except CryptoUnavailable:
        return False
