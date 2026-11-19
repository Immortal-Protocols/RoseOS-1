package app.rose.starters.matrix

import app.rose.design.Template
import app.rose.design.Theme
import app.rose.template.feed.FeedTemplate
import app.rose.template.player.PlayerTemplate
import app.rose.template.spritegame.SpriteGameTemplate
import app.rose.theme.material3.Material3Theme
import app.rose.theme.minimal.MinimalTheme
import app.rose.theme.skeuomorphic.SkeuomorphicTheme

/** The pilot set: 3 templates × 3 themes = 9 combinations, each with no per-pair code. */
object Pilots {
    val templates: List<Template> = listOf(FeedTemplate, SpriteGameTemplate, PlayerTemplate)
    val themes: List<Theme> = listOf(Material3Theme, MinimalTheme, SkeuomorphicTheme)

    fun template(id: String?) = templates.firstOrNull { it.id == id }
    fun theme(id: String?) = themes.firstOrNull { it.id == id }
}
