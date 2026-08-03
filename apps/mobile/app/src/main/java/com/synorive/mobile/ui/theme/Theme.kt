package com.synorive.mobile.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext

private val LightColors = lightColorScheme(
    primary = SynIndigo500,
    onPrimary = SynGraphite050,
    primaryContainer = SynIndigo400,
    background = SynGraphite050,
    surface = SynGraphite050,
    surfaceVariant = SynGraphite100,
    onBackground = SynGraphite900,
    onSurface = SynGraphite900,
    error = SynDanger,
)

private val DarkColors = darkColorScheme(
    primary = SynIndigo400,
    onPrimary = SynGraphite900,
    primaryContainer = SynIndigo600,
    background = SynGraphite900,
    surface = SynGraphite800,
    surfaceVariant = SynGraphite700,
    onBackground = SynGraphite100,
    onSurface = SynGraphite100,
    error = SynDanger,
)

@Composable
fun SynoriveTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    // 动态取色（Android 12+ 壁纸配色）默认关——桌面端强调"界面统一"的专业调性，
    // 跟着壁纸变色会跟桌面端的视觉语言对不上
    dynamicColor: Boolean = false,
    content: @Composable () -> Unit,
) {
    val context = LocalContext.current
    val colorScheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
            if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        darkTheme -> DarkColors
        else -> LightColors
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = SynoriveTypography,
        content = content,
    )
}
