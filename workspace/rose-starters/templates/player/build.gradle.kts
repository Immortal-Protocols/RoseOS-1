plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.compose)
}

// OWNER: Player template engineer. Template: behaviour and state only. Depends on :design, never on a theme or Material.
android {
    namespace = "app.rose.template.player"
    compileSdk = libs.versions.compileSdk.get().toInt()
    defaultConfig { minSdk = libs.versions.minSdk.get().toInt() }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
    testOptions { unitTests { isIncludeAndroidResources = true } }
}

dependencies {
    api(project(":design"))
    implementation(libs.activity.compose) // BackHandler, rememberSaveable helpers

    testImplementation(project(":design-testing"))
    testImplementation(project(":examples"))
    debugImplementation(libs.compose.ui.test.manifest)
}
