# R8 规则（release 开混淆 + 资源裁剪）
# ====================================================================
# 开混淆的两个理由：
#   ① APK 小一圈，冷启动少解压/少加载一批类
#   ② 反编译出来是 a.b.c，不再是一眼可读的业务逻辑
#
# 🔴 反射拿不到的东西必须显式 keep。R8 删掉一个只被反射用到的类时
#    **不报错**，装到手机上才崩 —— 所以下面每条都写清楚保的是什么。

# ── kotlinx.serialization ────────────────────────────────────────
# 序列化器是编译期生成的伴生对象，只被反射查到，R8 看不到引用链。
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.synorive.mobile.**$$serializer { *; }
-keepclassmembers class com.synorive.mobile.** {
    *** Companion;
}
-keepclasseswithmembers class com.synorive.mobile.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# ── Retrofit ────────────────────────────────────────────────────
# 接口方法上的注解和泛型返回值是运行时读的，擦掉就拿不到路由和响应类型。
-keepattributes Signature, RuntimeVisibleAnnotations, AnnotationDefault
-keep,allowobfuscation interface com.synorive.mobile.data.network.EngineApi
-keep,allowobfuscation,allowshrinking interface retrofit2.Call
-keep,allowobfuscation,allowshrinking class retrofit2.Response
-keep,allowobfuscation,allowshrinking class kotlin.coroutines.Continuation

# ── OkHttp ──────────────────────────────────────────────────────
# 这几个是可选依赖的兼容分支，本项目没引，忽略它们的缺失告警。
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**

# ── Room ────────────────────────────────────────────────────────
# 实体和 DAO 的实现类由 KSP 生成、按名字查找。
-keep class com.synorive.mobile.data.local.** { *; }

# ── 数据模型 ────────────────────────────────────────────────────
# @Serializable 的字段名就是 JSON 字段名，混淆掉字段名等于换了协议。
-keep class com.synorive.mobile.data.model.** { *; }
-keep class com.synorive.mobile.data.update.UpdateModels* { *; }

# ── 保留行号，崩溃栈还能对上源码 ─────────────────────────────────
-keepattributes SourceFile, LineNumberTable
-renamesourcefileattribute SourceFile
