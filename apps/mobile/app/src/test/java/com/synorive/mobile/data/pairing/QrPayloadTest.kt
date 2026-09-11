package com.synorive.mobile.data.pairing

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 配对二维码载荷解析
 * ====================================================================
 * 扫码是为了**不让人手抄三串字符**。所以这段解析必须要么给出完全正确的三样
 * 东西，要么明确说"这不是配对码" —— 中间态（半解析出来的地址）最糟：
 * 用户看到的是"连不上"，而真正的原因是他扫了一张别的二维码。
 */
class QrPayloadTest {

    @Test
    fun `正常的码解得出三样东西`() {
        val p = QrPayload.parse("synorive://pair?h=192.168.1.23&p=51234&t=abc123")
        assertEquals("192.168.1.23", p?.host)
        assertEquals(51234, p?.port)
        assertEquals("abc123", p?.token)
    }

    @Test
    fun `参数顺序无所谓`() {
        val p = QrPayload.parse("synorive://pair?t=abc123&p=51234&h=192.168.1.23")
        assertEquals("192.168.1.23", p?.host)
        assertEquals(51234, p?.port)
    }

    @Test
    fun `百分号转义要解回来`() {
        val p = QrPayload.parse("synorive://pair?h=192.168.1.23&p=1&t=a%2Bb%2Fc")
        assertEquals("a+b/c", p?.token)
    }

    @Test
    fun `别的二维码一律返回 null`() {
        assertNull(QrPayload.parse("https://example.com"))
        assertNull(QrPayload.parse("WIFI:S:MyWifi;T:WPA;P:pw;;"))
        assertNull(QrPayload.parse("随便一段文字"))
        assertNull(QrPayload.parse(""))
        assertNull(QrPayload.parse(null))
    }

    @Test
    fun `缺任何一样都不认 —— 半解析出来的地址比没有更糟`() {
        assertNull(QrPayload.parse("synorive://pair?p=51234&t=abc"))          // 没地址
        assertNull(QrPayload.parse("synorive://pair?h=1.2.3.4&t=abc"))        // 没端口
        assertNull(QrPayload.parse("synorive://pair?h=1.2.3.4&p=51234"))      // 没令牌
    }

    @Test
    fun `端口越界不认`() {
        assertNull(QrPayload.parse("synorive://pair?h=1.2.3.4&p=0&t=abc"))
        assertNull(QrPayload.parse("synorive://pair?h=1.2.3.4&p=70000&t=abc"))
        assertNull(QrPayload.parse("synorive://pair?h=1.2.3.4&p=abc&t=abc"))
    }

    @Test
    fun `空白值不认`() {
        assertNull(QrPayload.parse("synorive://pair?h=%20&p=51234&t=abc"))
        assertNull(QrPayload.parse("synorive://pair?h=1.2.3.4&p=51234&t=%20"))
    }

    @Test
    fun `协议名大小写不敏感`() {
        assertEquals(51234, QrPayload.parse("SYNORIVE://PAIR?h=1.2.3.4&p=51234&t=abc")?.port)
    }
}
