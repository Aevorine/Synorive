package com.synorive.mobile

import androidx.compose.runtime.staticCompositionLocalOf

val LocalAppContainer = staticCompositionLocalOf<AppContainer> {
    error("AppContainer 没提供——检查 MainActivity 是不是忘了用 CompositionLocalProvider 包一层")
}
