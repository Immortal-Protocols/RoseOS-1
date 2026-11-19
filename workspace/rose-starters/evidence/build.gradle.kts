plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.compose)
}

// OWNER: Home/App Product. Wave 3 behaviour evidence: the same template code under every pilot theme must expose
// the same behaviour. ThemeInvarianceTest writes a normalized semantics snapshot per (template, theme, API) to
// build/evidence/, and compareThemeSnapshots fails when one template's snapshots differ between themes, writing
// build/evidence/report.json either way.
//   Pilots:   ./gradlew :evidence:compareThemeSnapshots
//   Written:  ./gradlew :evidence:compareThemeSnapshots -PwrittenTemplate=<tree>/templates/<id> \
//                 -PtemplateObject=app.rose.template.<pkg>.<Object> -ProbolectricDependencyDir=<jars>
// A written template is a writer's code: run it only inside the builder's isolation (offline, no credentials).
android {
    namespace = "app.rose.starters.evidence"
    compileSdk = libs.versions.compileSdk.get().toInt()
    defaultConfig { minSdk = libs.versions.minSdk.get().toInt() }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            all {
                it.systemProperty("rose.evidence.out", layout.buildDirectory.dir("evidence").get().asFile.path)
                providers.gradleProperty("templateObject").orNull?.let { o -> it.systemProperty("rose.evidence.templates", o) }
                // Offline (inside the builder's isolation): Robolectric reads its Android jars from this folder
                // (android-all-instrumented-<version>.jar) instead of downloading them.
                providers.gradleProperty("robolectricDependencyDir").orNull?.let { d ->
                    it.systemProperty("robolectric.offline", "true")
                    it.systemProperty("robolectric.dependency.dir", d)
                }
            }
        }
    }
}

val written = findProject(":written") != null

dependencies {
    implementation(project(":design")) // Compose BOM and the contract
    testImplementation(project(":design-testing"))
    testImplementation(project(":themes:material3"))
    testImplementation(project(":themes:minimal"))
    testImplementation(project(":themes:skeuomorphic"))
    if (written) {
        testImplementation(project(":written"))
    } else {
        testImplementation(project(":templates:feed"))
        testImplementation(project(":templates:sprite-game"))
        testImplementation(project(":templates:player"))
    }
    debugImplementation(libs.compose.ui.test.manifest)
}

val evidenceDir = layout.buildDirectory.dir("evidence")
// Each run's snapshots stand alone: an earlier run's files never join the comparison.
// The snapshots are the test task's declared output, so a cached or up-to-date run restores exactly its own.
tasks.withType<Test>().configureEach {
    val dir = evidenceDir
    outputs.dir(dir)
    doFirst { dir.get().asFile.deleteRecursively() }
}
val compareThemeSnapshots by tasks.registering {
    group = "verification"
    description = "Fails when one template's behaviour snapshots differ between pilot themes; writes report.json."
    dependsOn("testDebugUnitTest")
    val dir = evidenceDir
    doLast {
        val root = dir.get().asFile
        val groups = root.listFiles { f -> f.isDirectory }.orEmpty().sortedBy { it.name }
        if (groups.isEmpty()) throw GradleException("No behaviour snapshots in $root: did ThemeInvarianceTest run?")
        val lines = mutableListOf<String>()
        // Folder names come from template ids, which a writer controls: escape everything that goes into JSON.
        fun json(text: String) = "\"" + text.flatMap { c ->
            when {
                c == '"' -> listOf('\\', '"')
                c == '\\' -> listOf('\\', '\\')
                c < ' ' -> "\\u%04x".format(c.code).toList()
                else -> listOf(c)
            }
        }.joinToString("") + "\""
        val problems = mutableListOf<String>()
        for (template in groups) {
            val byApi = template.listFiles { f -> f.name.endsWith(".txt") }.orEmpty().groupBy { it.name.substringAfter("-api") }
            for ((api, files) in byApi.toSortedMap()) {
                val texts = files.sortedBy { it.name }.associate { it.name.substringBefore("-api") to it.readText() }
                val distinct = texts.values.toSet().size
                val same = distinct == 1 && texts.size == 3
                val apiLevel = api.removeSuffix(".txt").toIntOrNull() ?: throw GradleException("Unexpected snapshot ${template.name}/$api")
                lines += "{\"template\":${json(template.name)},\"api\":$apiLevel,\"themes\":[${texts.keys.joinToString(",") { json(it) }}],\"identical\":$same}"
                if (!same) problems += "${template.name} API ${api.removeSuffix(".txt")}: ${texts.size} themes, $distinct distinct snapshots (${texts.keys})"
            }
        }
        File(root, "report.json").writeText("[\n" + lines.joinToString(",\n") + "\n]\n")
        if (problems.isNotEmpty()) throw GradleException("Behaviour differs between themes (see build/evidence):\n" + problems.joinToString("\n"))
    }
}
tasks.named("check") { dependsOn(compareThemeSnapshots) }
