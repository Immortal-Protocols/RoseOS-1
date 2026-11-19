plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

// The runnable example: one template (Tally) under two themes (Baseline, Paper), swapped by one line in MainActivity.
// Debug-signed with the SDK's debug key; never released, never installed by Rose.
android {
    namespace = "app.rose.starters.sample"
    compileSdk = libs.versions.compileSdk.get().toInt()
    defaultConfig {
        applicationId = "app.rose.starters.sample"
        minSdk = libs.versions.minSdk.get().toInt()
        targetSdk = libs.versions.targetSdk.get().toInt()
        versionCode = 1
        versionName = "0.1"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
    testOptions { unitTests { isIncludeAndroidResources = true } }
}

dependencies {
    implementation(project(":examples"))
    implementation(libs.activity.compose)
    debugImplementation(libs.compose.ui.tooling)

    testImplementation(project(":design-testing"))
    debugImplementation(libs.compose.ui.test.manifest)
}
