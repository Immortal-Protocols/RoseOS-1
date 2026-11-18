package app.rose.theme.minimal

import app.rose.design.testing.ThemeCases
import app.rose.design.testing.ThemeConformanceTest
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/** OWNER: Minimal theme engineer. The Controls contract under Minimal, light and dark, API 34 and 37. Must pass unchanged. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37])
class MinimalThemeConformance(dark: Boolean) : ThemeConformanceTest(MinimalTheme, dark) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}
