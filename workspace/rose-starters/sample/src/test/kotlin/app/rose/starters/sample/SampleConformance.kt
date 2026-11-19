package app.rose.starters.sample

import app.rose.starters.examples.PaperTheme
import app.rose.design.testing.ThemeCases
import app.rose.design.testing.ThemeConformanceTest
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/** Paper keeps the Controls contract: what lets Tally (or any template) run under it unchanged. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37])
class PaperConformance(dark: Boolean) : ThemeConformanceTest(PaperTheme, dark) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}
