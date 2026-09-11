package com.synorive.mobile.data.pairing

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.common.HybridBinarizer

/**
 * 配对二维码的载荷
 * ====================================================================
 * 桌面端出的码内容是：`synorive://pair?h=IP&p=端口&t=令牌`
 *
 * 治的是配对最大的摩擦点：原来要手抄 IP、端口、32 位十六进制令牌三样东西，
 * 抄错任意一个字符，手机端得到的都是同一句"连不上" ——
 * 而用户没法知道是抄错了、还是网不通、还是电脑上没开。
 */
data class QrPayload(val host: String, val port: Int, val token: String) {

    companion object {
        private const val SCHEME = "synorive"
        private const val HOST_PAIR = "pair"

        /**
         * 解析扫到的字符串。**认不出来一律返回 null，不做任何猜测。**
         *
         * 🔴 猜是最坏的选择：拿一个半解析出来的地址去连，用户看到的是
         *    "连不上"，而真正的原因是他扫了一张别的二维码。
         *    明确说"这不是配对码"才是有用的信息。
         */
        fun parse(raw: String?): QrPayload? {
            // 🔴 **自己解，不用 android.net.Uri。** 用 Uri 的话这段逻辑就只能在
            //    设备/Robolectric 上测 —— 而它是整条扫码链路上最容易出错、
            //    也最该被钉死的一段（少一个参数、端口越界、扫到别的码）。
            //    纯 Kotlin 之后一个普通 JVM 单测就能覆盖全部分支。
            val text = raw?.trim().orEmpty()
            val prefix = "$SCHEME://$HOST_PAIR?"
            if (!text.startsWith(prefix, ignoreCase = true)) return null

            val params = HashMap<String, String>()
            for (pair in text.substring(prefix.length).split("&")) {
                val i = pair.indexOf('=')
                if (i <= 0) continue
                val k = pair.substring(0, i)
                val v = runCatching {
                    java.net.URLDecoder.decode(pair.substring(i + 1), "UTF-8")
                }.getOrNull() ?: continue
                // 重复的键取第一个 —— 后面的多半是拼接出来的垃圾
                params.putIfAbsent(k, v)
            }

            val host = params["h"]?.trim().orEmpty()
            val port = params["p"]?.trim()?.toIntOrNull() ?: 0
            val token = params["t"]?.trim().orEmpty()
            if (host.isEmpty() || token.isEmpty() || port !in 1..65535) return null
            return QrPayload(host, port, token)
        }

        /**
         * 从一张拍下来的照片里把二维码读出来。
         *
         * 🔴 **必须缩图再解。** 现在手机随手一拍就是 4000x3000，整张丢给 ZXing
         *    要先分配 4800 万个 int（约 190 MB）—— 在低端机上直接 OOM，
         *    而 OOM 崩溃看起来就是"点了扫码 App 就闪退"，和二维码毫无关系。
         *    缩到长边 1600 足够解出一张手机屏幕上的码。
         *
         * 🔴 **两次机会：原图 + 反色。** 深色主题下有些桌面截图会是反色的码，
         *    ZXing 对反色码的识别率明显下降。多试一次几乎不花时间。
         */
        fun decodeFromImage(context: Context, uri: Uri): String? {
            val bmp = loadScaled(context, uri) ?: return null
            return decodeBitmap(bmp) ?: decodeBitmap(invert(bmp))
        }

        private fun loadScaled(context: Context, uri: Uri): Bitmap? = runCatching {
            // 先只读边界，算出缩放比，再真正解码 —— 不这么做的话
            // 光是 decodeStream 那一步就已经把整张原图读进内存了
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            context.contentResolver.openInputStream(uri)?.use {
                BitmapFactory.decodeStream(it, null, bounds)
            }
            val longest = maxOf(bounds.outWidth, bounds.outHeight)
            var sample = 1
            while (longest / sample > 1600) sample *= 2

            val opts = BitmapFactory.Options().apply {
                inSampleSize = sample
                inPreferredConfig = Bitmap.Config.ARGB_8888
            }
            context.contentResolver.openInputStream(uri)?.use {
                BitmapFactory.decodeStream(it, null, opts)
            }
        }.getOrNull()

        private fun decodeBitmap(bmp: Bitmap): String? = runCatching {
            val w = bmp.width
            val h = bmp.height
            val pixels = IntArray(w * h)
            bmp.getPixels(pixels, 0, w, 0, 0, w, h)
            val source = RGBLuminanceSource(w, h, pixels)
            val binary = BinaryBitmap(HybridBinarizer(source))
            val hints = mapOf(DecodeHintType.TRY_HARDER to true)
            MultiFormatReader().decode(binary, hints).text
        }.getOrNull()

        private fun invert(bmp: Bitmap): Bitmap {
            val w = bmp.width
            val h = bmp.height
            val px = IntArray(w * h)
            bmp.getPixels(px, 0, w, 0, 0, w, h)
            for (i in px.indices) {
                val p = px[i]
                px[i] = (p and 0xFF000000.toInt()) or (p.inv() and 0x00FFFFFF)
            }
            return Bitmap.createBitmap(px, w, h, Bitmap.Config.ARGB_8888)
        }
    }
}
