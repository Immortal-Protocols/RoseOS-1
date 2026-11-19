pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "rose-starters"

// Framework (Home/App Product): the theme contract, stock controls, the baseline theme and the conformance kit.
include(":design", ":design-testing")
// The runnable example: one template (Tally), two themes (Baseline, Paper), a one-line swap in :sample's MainActivity.
include(":examples", ":sample")
// Pilot templates (behavior and state). Each depends on :design only.
include(":templates:feed", ":templates:sprite-game", ":templates:player")
// Pilot themes (appearance and stock controls). Each depends on :design only (plus its own look's libraries).
include(":themes:material3", ":themes:minimal", ":themes:skeuomorphic")
// Integration: every pilot template under every pilot theme, the conformance and behavior matrix (Home/App Product).
include(":matrix")
