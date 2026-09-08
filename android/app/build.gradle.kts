plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android { namespace = "com.zerodrivex.zdxnode"; compileSdk = 35; buildToolsVersion = "35.0.0"
    defaultConfig { applicationId = "com.zerodrivex.zdxnode"; minSdk = 26; targetSdk = 35; versionCode = 1; versionName = "1.0.0" }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    signingConfigs {
        create("release") {
            val store = providers.environmentVariable("ZDX_ANDROID_KEYSTORE").orNull
            val password = providers.environmentVariable("ZDX_ANDROID_KEYSTORE_PASSWORD").orNull
            val alias = providers.environmentVariable("ZDX_ANDROID_KEY_ALIAS").orNull
            val aliasPassword = providers.environmentVariable("ZDX_ANDROID_KEY_PASSWORD").orNull
            if (store != null && password != null && alias != null && aliasPassword != null) {
                storeFile = file(store); storePassword = password; keyAlias = alias; keyPassword = aliasPassword
            }
        }
    }
    buildTypes { release { isMinifyEnabled = false; signingConfig = signingConfigs.getByName("release") } }
}
