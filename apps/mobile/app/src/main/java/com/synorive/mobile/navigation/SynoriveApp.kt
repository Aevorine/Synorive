package com.synorive.mobile.navigation

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.SettingsEthernet
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.foundation.layout.padding
import androidx.compose.ui.Modifier
import androidx.navigation.NavController
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.synorive.mobile.ui.camera.CameraSearchScreen
import com.synorive.mobile.ui.pairing.PairingScreen
import com.synorive.mobile.ui.reverse.ReverseResultScreen
import com.synorive.mobile.ui.search.SearchScreen
import com.synorive.mobile.ui.share.ShareInboxViewModel
import com.synorive.mobile.ui.share.ShareIntakeScreen

private object Routes {
    const val SEARCH = "search"
    const val PAIRING = "pairing"
    const val CAMERA_SEARCH = "camera_search"
    const val SHARE_INTAKE = "share_intake"
    const val REVERSE = "reverse/{itemId}/{isVideo}"

    fun reverse(itemId: String, isVideo: Boolean) = "reverse/$itemId/$isVideo"
}

/**
 * 顶层导航。底部两个常驻入口（搜索/配对），拍照反查、分享投喂、反查结果都是
 * 压在上面的临时页面——用户完成一次动作就该退回搜索，不该占一个常驻标签位。
 */
@Composable
fun SynoriveApp(shareInbox: ShareInboxViewModel) {
    val navController = rememberNavController()
    val pendingShare by shareInbox.pending.collectAsState()

    // 分享菜单/浏览器"分享给 Synorive"随时可能触发——不管当前停在哪个页面，
    // 一收到待处理内容就把投喂页压到最上面
    LaunchedEffect(pendingShare) {
        if (pendingShare != null) {
            navController.navigate(Routes.SHARE_INTAKE)
        }
    }

    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route
    val showBottomBar = currentRoute == Routes.SEARCH || currentRoute == Routes.PAIRING

    Scaffold(
        bottomBar = {
            if (showBottomBar) {
                NavigationBar {
                    NavigationBarItem(
                        selected = currentRoute == Routes.SEARCH,
                        onClick = { navController.navigateToTab(Routes.SEARCH) },
                        icon = { Icon(Icons.Filled.Search, contentDescription = null) },
                        label = { Text("搜索") },
                    )
                    NavigationBarItem(
                        selected = currentRoute == Routes.PAIRING,
                        onClick = { navController.navigateToTab(Routes.PAIRING) },
                        icon = { Icon(Icons.Filled.SettingsEthernet, contentDescription = null) },
                        label = { Text("配对") },
                    )
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = Routes.SEARCH,
            modifier = Modifier.padding(padding),
        ) {
            composable(Routes.SEARCH) {
                SearchScreen(
                    onOpenCameraSearch = { navController.navigate(Routes.CAMERA_SEARCH) },
                    onOpenPairing = { navController.navigateToTab(Routes.PAIRING) },
                    onOpenReverseSearch = { itemId, isVideo ->
                        navController.navigate(Routes.reverse(itemId, isVideo))
                    },
                )
            }

            composable(Routes.PAIRING) { PairingScreen() }

            composable(Routes.CAMERA_SEARCH) {
                CameraSearchScreen(onBack = { navController.popBackStack() })
            }

            composable(Routes.SHARE_INTAKE) {
                val content = pendingShare
                if (content != null) {
                    ShareIntakeScreen(
                        content = content,
                        onDone = {
                            shareInbox.consume()
                            navController.popBackStack()
                        },
                    )
                }
            }

            composable(
                Routes.REVERSE,
                arguments = listOf(
                    navArgument("itemId") { type = NavType.StringType },
                    navArgument("isVideo") { type = NavType.BoolType },
                ),
            ) { entry ->
                val itemId = entry.arguments?.getString("itemId").orEmpty()
                val isVideo = entry.arguments?.getBoolean("isVideo") ?: false
                ReverseResultScreen(itemId = itemId, isVideo = isVideo, onBack = { navController.popBackStack() })
            }
        }
    }
}

/** 底部两个标签之间切换用的标准写法——不重复堆栈，回退键行为符合预期。 */
private fun NavController.navigateToTab(route: String) {
    navigate(route) {
        popUpTo(graph.findStartDestination().id) { saveState = true }
        launchSingleTop = true
        restoreState = true
    }
}
