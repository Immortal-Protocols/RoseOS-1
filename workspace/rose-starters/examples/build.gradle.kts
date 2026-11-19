plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.compose)
}

// The example template (Tally) and example theme (Paper) behind the runnable sample. Not pilots: they exist so the
// framework is proven end to end, and so each pilot owner can test against a second theme or a real template
// before the other pilots exist (testImplementation(project(":examples"))).
android {
    namespace = "app.rose.starters.examples"
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
    implementation(libs.activity.compose)
}
