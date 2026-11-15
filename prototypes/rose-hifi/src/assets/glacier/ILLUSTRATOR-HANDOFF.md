# Glacier v1 — GLACIER-1E-1110

Illustrator: 01a17e38-7c1f-7075-b9c3-20309ea7e0fb. Designer owns integration; existing HiFi owner consumes assets. Prototype only, Oxblood remains available. No native implementation or production theme selector.

## Direction and provenance
Two precise mineral-blue planes, deep graphite quiet field, one silver sunlit edge. Original image-generation output, no external artwork or third-party assets. Raster originals plus exact generation/refinement prompts are included; not editable vector/3D material source. CSS/JSON palette and generator are editable source. Image generation is not deterministic reconstruction. Initial visible grain was rejected; final wide/portrait versions reduce it to smooth surfaces. Portrait is an intentional imagegen recomposition, not an exact geometric crop of the wide image.

## Placement
Use glacier-portrait.png for phone aspect ratios with background-size: cover; background-position: 50% 50%. At short aspect ratios, top-align it (50% 0%) to keep the diagonal light below app content. Use glacier-wide.png for landscape, center/cover. Do not stretch. Use opaque surface tokens behind readable content, notification cards, keyboard and controls; no text on the bright bevel. Keep original app artwork unfiltered. Home labels belong on the dark upper field or opaque tile/label backplates. Raised chin/keyboard insets belong to existing prototype geometry. Do not copy fixture geometry/icons from the generated context study into implementation.

Suggested image-only CSS:
```
.glacier-wallpaper {background: #0B141D url(glacier-portrait.png) center/cover no-repeat;}
@media (max-aspect-ratio: 1/1) and (min-aspect-ratio: 3/5) {
  .glacier-wallpaper {background-position: center top;}
}
@media (orientation: landscape) {
  .glacier-wallpaper {background-image: url(glacier-wide.png);}
}
```

## Semantic states
Base #0B141D; surface #142536; raised #20374A. Ivory primary #F0F6FA; secondary #B8CAD8. Pale mineral accent #A6D8F5 with dark #08131D text. Selected uses accent fill PLUS check or existing pressed semantics. Pressed #7BAAC8 with dark text. Focus #D5EDFF: 2px ring separated by 2px dark gap. Border-control #7897AE distinguishes actionable boundaries; #405B70 is decorative only. Disabled #899DAE on #20303D, actual disabled semantics and no activation; do not reduce entire-control opacity.

Warning #FFD28A; destructive and mic-muted #FFABAC; success #AFE0C0. Color never stands alone: warning icon/text, named destructive action, mic slash and accessible muted label. Input mute does not change speech output or work. Pending/failed states retain last confirmed state, with pending/error label and retry. Blue means selection/focus, not confirmation that microphone or connection succeeded. Preserve existing pearl split/connection and activity semantics; material recolor does not redefine meaning.

## Surfaces and review
Home: original system glyphs and third-party icons, shallow existing metal edge. Rose: existing connect/disconnect control, no duplicate mic, approved compact named bottom-left return above chin; preserve exact live app context. Notifications: opaque cards, explicit Send; failed replies keep draft/selection, keyboard hide never sends/dismisses. Keyboard: opaque keys, no number row or duplicate mic. Motion remains user-controlled and reduced-motion supported; wallpaper itself is still.

Context board is AI-generated static illustration, not a captured or functional prototype. It depicts Home/Rose palette only; icon geometry, crop exactness, text and spacing are illustrative. No browser/native/device behavior was tested by Illustrator. HiFi must inspect actual composites at 320px/390px and short heights, keyboard/chin/return states, and accessibility before review. Numeric contrast in contrast.json measures opaque token pairs only; no claim of full-screen WCAG conformance.

## Source and retained work
Assets are supplied independently of prototype source. No repository source, build, security, deployment, Oxblood, Night Array or charging-tray assets changed. Frankel/Mustang/Yogi require distinct physical builders; no build is involved here.
