plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.compose)
}

// The conformance kit: what every theme must keep (ThemeConformance) and helpers to run one template's behaviour
// test under every theme. Used from JVM (Robolectric) tests; never shipped in an app.
android {
    namespace = "app.rose.design.testing"
    compileSdk = libs.versions.compileSdk.get().toInt()
    defaultConfig { minSdk = libs.versions.minSdk.get().toInt() }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
}

dependencies {
    api(project(":design"))
    api(libs.junit)
    api(libs.robolectric)
    api(libs.compose.ui.test.junit4)
    api(libs.androidx.test.core)
    api(libs.espresso.core)
}
