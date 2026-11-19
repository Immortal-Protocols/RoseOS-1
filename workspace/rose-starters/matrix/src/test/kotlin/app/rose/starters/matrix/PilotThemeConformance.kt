package app.rose.starters.matrix

import app.rose.design.Theme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.ThemeConformanceTest
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/** Every pilot theme keeps the Controls contract (placeholders are skipped and reported as skipped). */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37])
class PilotThemeConformance(@Suppress("unused") private val id: String, theme: Theme, dark: Boolean) : ThemeConformanceTest(theme, dark) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0} dark={2}")
        fun cases() = ThemeCases.of(*Pilots.themes.toTypedArray())
    }
}
