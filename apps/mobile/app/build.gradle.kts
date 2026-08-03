import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
}

/**
 * 签名配置来自**仓库外**的 keystore.properties。
 *
 * 🔴 为什么不写在 gradle.properties 里：那个文件是跟着仓库走的。
 *    密码进了 git 就等于公开了，而这个仓库马上要转公开。
 *
 * 文件不存在时不报错、只是 release 构建退回调试签名 —— 别人 clone 下来
 * 应该能直接 `assembleDebug`，不该被一个他不可能有的密钥卡住。
 * 但 `assembleRelease` 会在下面打一行醒目的警告，避免"以为签好了其实没有"。
 */
val keystorePropsFile = rootProject.file("keystore.properties")
val keystoreProps = Properties().apply {
    if (keystorePropsFile.exists()) keystorePropsFile.inputStream().use { load(it) }
}
val hasReleaseSigning = keystoreProps.getProperty("storeFile")?.isNotBlank() == true

android {
    namespace = "com.synorive.mobile"
    // 目标 SDK 用当前稳定版；打开项目时 Android Studio 大概率会提示更新，跟着提示走就行
    compileSdk = 35

    defaultConfig {
        applicationId = "com.synorive.mobile"
        // A16 验收线：安卓 8.0（API 26）起，桌面端的旧机型覆盖面已经够用
        minSdk = 26
        targetSdk = 35
        // 🔴 versionCode 是**自更新唯一的判据**（versionName 只是给人看的字符串）。
        //    每次发版必须 +1，否则装了旧版的手机永远查不到新版，而且它
        //    报的是「已是最新」不是报错。用 `node scripts/release.mjs` 会自动改这两行。
        versionCode = 101
        versionName = "0.1.1"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // U 组自更新要知道去哪个仓库查 Release。写死在代码里的话，
        // 改仓库名要翻三个文件；放这儿构建期注入，只有这一处。
        buildConfigField("String", "UPDATE_REPO", "\"Aevorine/Synorive\"")
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                storeFile = rootProject.file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
                // v2 从 API 24 起就支持，而本项目 minSdk = 26 —— 所以 v2
                // 单独就覆盖了全部目标机型。v1（JAR 签名）留着只是给
                // 「哪天把 minSdk 降到 23 以下」兜底；实测 AGP 在 minSdk≥24
                // 时不会真的写 v1 段（apksigner 报 v1: false），这是正常的，
                // **不是签名失败**。判据看 v2 那一行。
                enableV1Signing = true
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (hasReleaseSigning) {
                signingConfig = signingConfigs.getByName("release")
            } else {
                logger.warn(
                    "⚠️  apps/mobile/keystore.properties 不存在 —— release APK 将**没有正式签名**，" +
                        "装到手机上会和已装的正式版冲突。见 apps/mobile/keystore.properties.example"
                )
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        // UPDATE_REPO 要生成到 BuildConfig 里，AGP 8 起这个开关默认是关的
        buildConfig = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    implementation(libs.androidx.material.icons.extended)
    debugImplementation(libs.androidx.ui.tooling)

    implementation(libs.androidx.navigation.compose)

    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler)

    implementation(libs.androidx.datastore.preferences)

    implementation(libs.retrofit.core)
    implementation(libs.retrofit.converter.kotlinx)
    implementation(libs.okhttp)
    implementation(libs.okhttp.logging)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)

    implementation(libs.coil.compose)
}
