package com.synorive.mobile.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * 桌面端的字体规矩是"中文宋体/思源宋体 + 英文 Times New Roman"（专业出版感）。
 * 手机端不跟着塞同一批字体文件——那几款是为大屏、长时间阅读调的衬线字体，
 * 搬到手机小屏上反而伤可读性，而且捆进 APK 会显著增大体积。
 * 用系统的通用衬线族（中文走系统自带的衬线中文字体，英文走系统 serif）
 * 是"两边气质呼应、但不是像素级复刻"的折中——真要像素级一致，
 * 需要拿到具体字体授权后作为下一版单独评估。
 */
private val SynSerif = FontFamily.Serif

val SynoriveTypography = Typography(
    headlineSmall = TextStyle(
        fontFamily = SynSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 22.sp,
        lineHeight = 28.sp,
    ),
    titleLarge = TextStyle(
        fontFamily = SynSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 18.sp,
        lineHeight = 24.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = SynSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 16.sp,
        lineHeight = 22.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = SynSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
        lineHeight = 24.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = SynSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 20.sp,
    ),
    labelLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 14.sp,
        lineHeight = 20.sp,
    ),
    bodySmall = TextStyle(
        fontFamily = SynSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 12.sp,
        lineHeight = 16.sp,
    ),
)
