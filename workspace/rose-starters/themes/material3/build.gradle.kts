plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.compose)
}

// OWNER: Material 3 theme engineer. Theme: appearance and stock controls only. Depends on :design (plus its own look's libraries), never on a template.
android {
    namespace = "app.rose.theme.material3"
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
    implementation(libs.compose.material3)

    testImplementation(project(":design-testing"))
    testImplementation(project(":examples"))
    debugImplementation(libs.compose.ui.test.manifest)
}
