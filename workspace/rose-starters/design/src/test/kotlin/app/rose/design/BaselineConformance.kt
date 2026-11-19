package app.rose.design

import app.rose.design.testing.ThemeCases
import app.rose.design.testing.ThemeConformanceTest
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config

/** The baseline theme meets the contract it documents, on Android 14 (API 34) and current Android (API 37). */
@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(sdk = [34, 37])
class BaselineConformance(dark: Boolean) : ThemeConformanceTest(BaselineTheme, dark) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun modes() = ThemeCases.modes()
    }
}
