plugins {
    base
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.android.library) apply false
    alias(libs.plugins.kotlin.compose) apply false
}

// The one structural rule that keeps any theme applicable to any template with no per-pair adapter:
// templates see only app.rose.design (never a theme, never Material), and themes never see a template.
val checkStarterBoundaries by tasks.registering {
    group = "verification"
    description = "Templates import no theme or Material; themes import no template."
    val roots = layout.projectDirectory
    doLast {
        val rules = listOf(
            "templates" to listOf("import app.rose.theme.", "import androidx.compose.material", "import androidx.compose.material3"),
            "themes" to listOf("import app.rose.template."),
            "design" to listOf("import app.rose.theme.", "import app.rose.template.", "import androidx.compose.material"),
        )
        val problems = mutableListOf<String>()
        for ((dir, banned) in rules) {
            roots.dir(dir).asFile.walkTopDown()
                .filter { it.isFile && it.extension == "kt" && "/build/" !in it.invariantSeparatorsPath && "/src/test/" !in it.invariantSeparatorsPath }
                .forEach { f ->
                    f.readLines().forEachIndexed { i, line ->
                        banned.firstOrNull { line.trim().startsWith(it) }?.let { problems += "${f.relativeTo(roots.asFile)}:${i + 1}: $dir must not '${it}…'" }
                    }
                }
        }
        if (problems.isNotEmpty()) throw GradleException("Starter boundary violations:\n" + problems.joinToString("\n"))
    }
}

tasks.named("check") { dependsOn(checkStarterBoundaries) }
