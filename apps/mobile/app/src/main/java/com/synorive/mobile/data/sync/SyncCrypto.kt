package com.synorive.mobile.data.sync

import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec
import javax.crypto.Mac
import com.synorive.mobile.data.model.SyncEnvelope

/**
 * E17 —— 手机侧的端到端加密，**必须和 `engine/synorive/sync/crypto.py` 逐字对上**。
 *
 * 🔴 这个文件里的每一个常量都是**协议的一部分**，不是调优参数：
 * 算法名、迭代数、密钥长度、nonce 长度、口令的 UTF-8 编码、信封的字段名。
 * 改动任何一个，两端就会派生出完全不同的密钥或者解析不了对方的信封 ——
 * 而症状是「配对显示成功，之后所有数据都解不开」。
 * 那种故障从任何一条日志上都看不出根因，只能靠两边源码逐行对。
 *
 * 🔴 **为什么是 PBKDF2 而不是 scrypt**（scrypt 抗暴力破解更强）：
 * Android 上**没有保证可用的 scrypt** —— `SecretKeyFactory` 不提供，
 * Conscrypt 不暴露，要拉 BouncyCastle 才有。而 `PBKDF2WithHmacSHA256`
 * 在 Android 8.0+ 和 Python stdlib 上都是标配。
 * **互操作的确定性在这里比算法强度更值钱。**
 */
object SyncCrypto {

    /** 和 Python 侧 `ENVELOPE_VERSION` 必须一致 */
    const val ENVELOPE_VERSION = 1

    /** 和 Python 侧 `_PBKDF2_ITERS` 必须一致 */
    private const val PBKDF2_ITERS = 600_000

    /** AES-256 */
    private const val KEY_BITS = 256

    /** GCM 标准 nonce 长度，别改 */
    private const val NONCE_LEN = 12

    /** GCM 认证标签长度（位）。Java 默认 128，Python 的 cryptography 也是 128 */
    private const val TAG_BITS = 128

    /** 和 Python 侧 `SALT_LEN` 必须一致 */
    const val SALT_LEN = 16

    /**
     * 和 Python 侧 `MIN_PASSPHRASE_LEN` 必须一致。
     * 🔴 两端门槛不一致的话，手机上能过的口令到桌面端被拒 ——
     * 用户看到的是"配对时好时坏"，最难查的一类不一致
     */
    const val MIN_PASSPHRASE_LEN = 8

    private val rng = SecureRandom()

    /**
     * 口令 + 盐 → 32 字节密钥。
     *
     * 🔴 `PBEKeySpec` 收的是 `CharArray` 而不是 `ByteArray`。
     * Java 内部按 UTF-8 编码它，和 Python 的 `passphrase.encode("utf-8")` 一致 ——
     * 但**只有 `PBKDF2WithHmacSHA256` 这个 provider 是这样**；
     * 老的 `PBKDF2WithHmacSHA1` 在某些实现上会把非 ASCII 截成低 8 位，
     * 中文口令会因此在两端算出不同的密钥。所以算法名不能改。
     */
    fun deriveKey(passphrase: String, saltHex: String): ByteArray {
        require(passphrase.isNotBlank()) { "配对口令不能为空（纯空格也不行）" }
        require(passphrase.length >= MIN_PASSPHRASE_LEN) {
            "配对口令太短，至少 $MIN_PASSPHRASE_LEN 个字符 —— 这是整套加密里唯一的秘密"
        }
        val salt = hexToBytes(saltHex)
        require(salt.size == SALT_LEN) { "盐长度不对：要 $SALT_LEN 字节，拿到 ${salt.size}" }
        val spec = PBEKeySpec(passphrase.toCharArray(), salt, PBKDF2_ITERS, KEY_BITS)
        return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
    }

    /**
     * 密钥指纹，给用户肉眼比对两台设备是不是同一把钥匙。
     * 前缀是域分离，和 Python 侧 `key_fingerprint` 一致。
     */
    fun fingerprint(key: ByteArray): String {
        val md = java.security.MessageDigest.getInstance("SHA-256")
        md.update("synorive-sync-fp-v1".toByteArray(Charsets.UTF_8))
        md.update(key)
        return bytesToHex(md.digest()).substring(0, 16)
    }

    /**
     * 封信封。`payloadJson` 是**已经序列化好的 JSON 文本**（一个数组）。
     * 返回的形状必须和 Python 的 `seal()` 一模一样。
     *
     * 🔴 **每次都现取随机 nonce，绝不用计数器。** 计数器要跨进程持久化，
     * 一次崩溃重来就会重用 nonce —— 在 GCM 下那是灾难性的
     * （泄露明文异或 + 可伪造消息）。
     */
    fun seal(key: ByteArray, payloadJson: String, aad: String = ""): SyncEnvelope {
        val nonce = ByteArray(NONCE_LEN).also { rng.nextBytes(it) }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.ENCRYPT_MODE,
            SecretKeySpec(key, "AES"),
            GCMParameterSpec(TAG_BITS, nonce),
        )
        if (aad.isNotEmpty()) cipher.updateAAD(aad.toByteArray(Charsets.UTF_8))
        // Python 侧用的是紧凑分隔符（无空格）。**这里不需要一致** ——
        // 密文里装的是字节，对端 json.loads 解出来一样。写下来是免得
        // 以后有人以为要逐字节对齐而去折腾序列化格式
        val ct = cipher.doFinal(payloadJson.toByteArray(Charsets.UTF_8))
        return SyncEnvelope(
            v = ENVELOPE_VERSION,
            nonce = bytesToHex(nonce),
            ct = bytesToHex(ct),
            aad = aad,
        )
    }

    /**
     * 拆信封，返回**明文 JSON 文本**（由调用方用 kotlinx 反序列化成 `List<SyncOpWire>`）。
     * **认证失败直接抛，绝不返回半截数据。**
     *
     * 🔴 版本不认识立刻拒绝，不要"试着按当前版本解一下" ——
     * 最好的情况是解密失败，最坏的情况是解出一堆看似合法的垃圾
     * 然后被当成真数据写进库里。
     */
    fun open(key: ByteArray, env: SyncEnvelope): String {
        if (env.v != ENVELOPE_VERSION) {
            throw IllegalArgumentException(
                "信封版本是 ${env.v}，这一端只认 $ENVELOPE_VERSION —— 两台设备版本对不上，先都升到同一版",
            )
        }
        val nonce = hexToBytes(env.nonce)
        require(nonce.size == NONCE_LEN) { "nonce 长度不对" }
        val ct = hexToBytes(env.ct)
        val aad = env.aad

        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            SecretKeySpec(key, "AES"),
            GCMParameterSpec(TAG_BITS, nonce),
        )
        if (aad.isNotEmpty()) cipher.updateAAD(aad.toByteArray(Charsets.UTF_8))
        // AEADBadTagException 会从这里抛出去。**不要 catch 成返回 null** ——
        // 认证失败意味着口令不对或数据被改过，两种都必须让上层知道
        val raw = cipher.doFinal(ct)
        return String(raw, Charsets.UTF_8)
    }

    /**
     * 校验配对挑战。和 Python 的 `verify_passphrase` 一致。
     *
     * 🔴 用**常量时间**比较，不用 `==`。后者是短路比较，
     * 耗时随匹配前缀长度变化，理论上能被计时攻击一个字节一个字节地问出来。
     */
    fun verifyChallenge(key: ByteArray, nonceHex: String, macHex: String): Boolean {
        return try {
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(key, "HmacSHA256"))
            mac.update("synorive-pair-v1".toByteArray(Charsets.UTF_8))
            mac.update(hexToBytes(nonceHex))
            constantTimeEquals(mac.doFinal(), hexToBytes(macHex))
        } catch (_: Exception) {
            false
        }
    }

    private fun constantTimeEquals(a: ByteArray, b: ByteArray): Boolean {
        if (a.size != b.size) return false
        var diff = 0
        for (i in a.indices) diff = diff or (a[i].toInt() xor b[i].toInt())
        return diff == 0
    }

    fun bytesToHex(b: ByteArray): String {
        // 🔴 显式 `and 0xFF`。Kotlin 的 Byte 是**有符号**的，0x80~0xFF 会是负数；
        // 虽然 Java 的 Formatter 对 Byte 做了正确处理，但只要有人顺手把它
        // 改成 `x.toInt()` 就会变成 "ffffff80" —— 而那是个**协议级**的错误：
        // 密文十六进制串多了六个字符，对端解出来完全是另一段数据。
        // 这里多写四个字符，换掉一整类没法从日志上看出来的故障
        val sb = StringBuilder(b.size * 2)
        for (x in b) sb.append("%02x".format(x.toInt() and 0xFF))
        return sb.toString()
    }

    fun hexToBytes(s: String): ByteArray {
        // 🔴 奇数长度必须拒绝。`step 2` 遇到奇数长度会在最后一组越界，
        // 而 substring 越界抛的是 StringIndexOutOfBounds —— 一个和
        // "十六进制串坏了"完全对不上号的错误信息
        require(s.length % 2 == 0) { "十六进制串长度必须是偶数，拿到 ${s.length}" }
        val out = ByteArray(s.length / 2)
        for (i in out.indices) {
            out[i] = s.substring(i * 2, i * 2 + 2).toInt(16).toByte()
        }
        return out
    }
}
