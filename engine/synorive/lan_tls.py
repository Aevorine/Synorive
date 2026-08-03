"""
局域网 TLS —— 4.22b H1 的补丁（**默认关，opt-in**）
====================================================================
要治的病：局域网配对走的是明文 HTTP（`usesCleartextTraffic=true`）。
同一个 Wi-Fi 下的其他设备能嗅到手机和电脑之间来回的检索内容 ——
查询词、结果标题、正文片段。按「对外发布」档这是一个真缺口。

**为什么默认关**：现在的明文配对是能用的、用户已经在用的功能。
一个**没法在这台机器上端到端验证**的 TLS 改造，如果默认打开，
最坏的结果是"更新了一下，手机连不上电脑了"，而用户完全不知道为什么。
安全改进不该以"把能用的功能弄坏"为代价 —— 所以它是一个显式开关，
用户自己决定什么时候切过去。

**为什么是自签名而不是买证书**：局域网地址是 `192.168.x.x` 这种，
没有任何 CA 会给内网 IP 签证书。自签 + **指纹固定**（pinning）是这个
场景唯一可行的做法，而且安全性并不比 CA 差 —— 手机认的不是"某个 CA 说它可信"，
而是"这就是我配对过的那台电脑"。

🔴 **指纹必须走带外通道给手机**，不能让手机"第一次连上就信任"。
   那叫 TOFU（trust on first use），第一次连接如果就被中间人劫持，
   之后每一次都会信任攻击者。这里复用已有的配对流程：
   用户本来就要手动抄一个 token，证书指纹跟它一起显示、一起抄。

用法（引擎侧）：
    python -m synorive.main --lan-tls --pairing-token xxx
证书放在 `<data-dir>/lan-cert.pem` / `lan-key.pem`，不存在就自动生成。
指纹通过 `/status` 报出来（那是 `_UNGUARDED_PATHS` 里的免鉴权路径，
手机在配对前就能读到它）。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import ipaddress
import logging
import socket
from pathlib import Path
from typing import Any

log = logging.getLogger("synorive.lantls")

CERT_NAME = "lan-cert.pem"
KEY_NAME = "lan-key.pem"
#: 自签证书有效期。够长到不用年年换，又不至于长到"泄露了也一直有效"
VALID_DAYS = 825


def _local_ips() -> list[str]:
    """本机所有 IPv4。证书要把它们都写进 SAN，否则换个网段就连不上。"""
    out: list[str] = ["127.0.0.1"]
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out:
                out.append(ip)
    except OSError as e:
        log.debug("列本机 IP 失败（只写 127.0.0.1）：%s", e)
    return out


def ensure_cert(data_dir: Path) -> tuple[Path, Path] | None:
    """
    保证证书存在，返回 (cert, key)。生成不了就返回 None。

    🔴 **返回 None 时调用方必须退回明文 HTTP 并把原因喊出来**，
    绝不能"静默地以为开了 TLS"—— 那是最坏的一种结果：
    用户以为加密了，实际是明文。
    """
    cert = data_dir / CERT_NAME
    key = data_dir / KEY_NAME
    if cert.exists() and key.exists():
        return cert, key

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        log.warning(
            "要开局域网 TLS 得装 cryptography（pip install cryptography）—— "
            "现在退回明文 HTTP"
        )
        return None

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "Synorive LAN"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Synorive"),
        ])
        san: list[Any] = [x509.DNSName("localhost")]
        for ip in _local_ips():
            try:
                san.append(x509.IPAddress(ipaddress.ip_address(ip)))
            except ValueError:
                continue

        now = _dt.datetime.now(_dt.UTC)
        crt = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(k.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _dt.timedelta(days=1))
            .not_valid_after(now + _dt.timedelta(days=VALID_DAYS))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(k, hashes.SHA256())
        )

        key.write_bytes(
            k.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        cert.write_bytes(crt.public_bytes(serialization.Encoding.PEM))
        # 私钥别让同机其他用户读到。Windows 上 chmod 基本没效果，
        # 但在 Linux/macOS 上是实打实的一道
        with __import__("contextlib").suppress(OSError, NotImplementedError):
            key.chmod(0o600)

        log.info("已生成局域网自签证书：%s（有效期 %d 天）", cert, VALID_DAYS)
        return cert, key
    except Exception as e:  # noqa: BLE001
        log.warning("生成局域网证书失败，退回明文 HTTP：%s", e)
        return None


def fingerprint(cert_path: Path) -> str | None:
    """
    证书的 SHA-256 指纹，形如 `AB:CD:...`。

    这是手机端要固定（pin）的那个值。**它必须和 token 一起、
    通过用户手抄的方式传过去** —— 让手机"第一次连上就信任"等于没有防护。
    """
    try:
        from cryptography import x509

        der = x509.load_pem_x509_certificate(cert_path.read_bytes()).public_bytes(
            __import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.DER
        )
        h = hashlib.sha256(der).hexdigest().upper()
        return ":".join(h[i : i + 2] for i in range(0, len(h), 2))
    except Exception as e:  # noqa: BLE001
        log.debug("读证书指纹失败：%s", e)
        return None
