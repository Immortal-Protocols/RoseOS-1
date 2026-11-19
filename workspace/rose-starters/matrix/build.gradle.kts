plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

// OWNER: Home/App Product (integration). Every pilot template under every pilot theme: the conformance and behaviour
// matrix, and a debug app that opens any one combination. A development tool, not the Rose builder validation
// (that runs through Rose's generation path in Wave 3). Debug-signed, never released.
android {
    namespace = "app.rose.starters.matrix"
    compileSdk = libs.versions.compileSdk.get().toInt()
    defaultConfig {
        applicationId = "app.rose.starters.matrix"
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
    implementation(project(":design"))
    implementation(project(":templates:feed"))
    implementation(project(":templates:sprite-game"))
    implementation(project(":templates:player"))
    implementation(project(":themes:material3"))
    implementation(project(":themes:minimal"))
    implementation(project(":themes:skeuomorphic"))
    implementation(libs.activity.compose)

    testImplementation(project(":design-testing"))
    debugImplementation(libs.compose.ui.test.manifest)
}
