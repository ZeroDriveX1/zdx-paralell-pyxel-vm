plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android { namespace = "com.zerodrivex.zdxnode"; compileSdk = 35; buildToolsVersion = "35.0.0"
    defaultConfig { applicationId = "com.zerodrivex.zdxnode"; minSdk = 28; targetSdk = 35; versionCode = 3; versionName = "1.1.1"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
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

dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
}
