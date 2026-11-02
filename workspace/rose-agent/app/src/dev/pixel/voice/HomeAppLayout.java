package dev.pixel.voice;

/** How the Home pages fit the real inventory into the inset viewport (OS-PAGING-158-M001), after the committed
 * Oxblood Rose HiFi (#410; main f6503a0, oxblood.css:354-363; qa/oxblood/oxblood-contract.json "homeGrid"). App pages:
 * four 72dp cells 6dp apart (a 59dp tile, 5dp gap and the name), rows 14dp apart. Home: four 60dp unlabelled cells
 * 17.5dp apart. Both grids sit in one band above the dots and the edge: the band's bottom is the pages' inset bottom,
 * and a grid's top is max(170dp, its height) above it, so the reference's two rows start at the same line on every
 * page and a taller grid (large text) grows upward rather than into the dots. At most two rows a page, as in the
 * reference. Columns follow the available width only, never a label. Every launchable app gets a cell and the page
 * count follows from the inventory; an empty inventory is still one page. Sizes are in one unit (px or dp) chosen by
 * the caller. Pure logic, no Android types. */
final class HomeAppLayout {
 private HomeAppLayout() {}

 static final int CELL_DP = 72, HOME_CELL_DP = 60, TILE_DP = 59, ICON_DP = 40, GLYPH_DP = 46, LABEL_GAP_DP = 5,
   COL_GAP_DP = 6, ROW_GAP_DP = 14, BAND_DP = 170, MAX_ROWS = 2, MARGIN_DP = 24, TOP_DP = 24, LABEL_LINES = 2;
 static final float HOME_COL_GAP_DP = 17.5f;
 /** From the edge's top up to the band's bottom (reference: the edge at 740, the app grid's bottom at 645). */
 static final int BAND_BOTTOM_DP = 95;
 /** Home's edge (OxbloodEdge.HEIGHT_DP; kept here so this class stays free of Android types). */
 static final int EDGE_DP = 92;
 /** The dots' row (44dp tall) is centred this far above the edge's top (reference: dots at 710, edge at 740). */
 static final int DOTS_DP = 44, DOTS_ABOVE_EDGE_DP = 30;
 /** Home's date (oxblood.css:353): 22sp, #eadbd0, at left 33 and top 65 of the page. */
 static final int DATE_LEFT_DP = 33, DATE_TOP_DP = 65, DATE_SP = 22, DATE_INK = 0xffeadbd0;

 /** Columns for an inset content width: three only where four 72dp cells cannot fit, four on a phone (the
  * reference), five or six on a wide (unfolded or landscape) screen. */
 static int columns(float widthDp) {
  if (widthDp < 4 * CELL_DP + 3 * COL_GAP_DP) return 3;
  if (widthDp < 600) return 4;
  return widthDp < 720 ? 5 : 6;
 }

 /** Columns of Home's unlabelled grid: four, as in the reference, three only where four 60dp cells cannot fit. */
 static int homeColumns(float widthDp) {
  return widthDp < 4 * HOME_CELL_DP + 3 * HOME_COL_GAP_DP ? 3 : 4;
 }

 /** An app-page cell: the tile, the gap and two label lines of the measured line height. */
 static float cellHeight(float density, float labelLineHeight) {
  return (TILE_DP + LABEL_GAP_DP) * density + LABEL_LINES * labelLineHeight;
 }

 /** Rows of cellHeight that fit height with the row gap between them: at least one, at most MAX_ROWS. */
 static int rows(float height, float cellHeight, float density) {
  if (cellHeight <= 0 || height <= 0) return 1;
  int fit = (int) Math.floor((height + ROW_GAP_DP * density) / (cellHeight + ROW_GAP_DP * density));
  return Math.max(1, Math.min(MAX_ROWS, fit));
 }

 /** The gap between rows (fixed, as in the reference). */
 static float rowGap(float density) { return ROW_GAP_DP * density; }

 static float gridHeight(int usedRows, float cellHeight, float density) {
  return usedRows * cellHeight + Math.max(0, usedRows - 1) * rowGap(density);
 }

 static int pages(int apps, int perPage) {
  return Math.max(1, (apps + Math.max(1, perPage) - 1) / Math.max(1, perPage));
 }

 /** The apps on page index (0-based) as [from, to). */
 static int[] slice(int apps, int perPage, int index) {
  int from = Math.min(apps, index * perPage);
  return new int[]{from, Math.min(apps, from + perPage)};
 }

 /** Rows a page of count apps actually uses. */
 static int usedRows(int count, int columns) {
  return (count + columns - 1) / columns;
 }

 /** Apps per page for an inset area and measured cell height (all px, with the display density). */
 static int perPage(float width, float height, float cellHeight, float density) {
  return columns(width / density) * rows(height, cellHeight, density);
 }

 /** The estimate used before any page has been measured: the configured screen less the status bar, the page top,
  * the edge and the space between the edge and the band (dp). The first measured page corrects it. */
 static float estimatedHeightDp(float screenHeightDp) {
  return screenHeightDp - TOP_DP - 24 - EDGE_DP - BAND_BOTTOM_DP;
 }

 /** Whether a page measured at this size holds a different number of apps than the plan it was built with. */
 static boolean replan(int plannedColumns, int plannedPerPage, float width, float height, float cellHeight, float density) {
  return plannedColumns != columns(width / density) || plannedPerPage != perPage(width, height, cellHeight, density);
 }

 /** Where a grid of gridHeight starts inside a page area of height: max(BAND, gridHeight) above its bottom, never
  * above the area's top. */
 static float gridTop(float height, float gridHeight, float density) {
  return Math.max(0, height - Math.max(BAND_DP * density, gridHeight));
 }

 /** Where a row of columns cells of cellWidth with gap starts inside width: centred. */
 static float gridLeft(float width, int columns, float cellWidth, float gap) {
  return (width - (columns * cellWidth + Math.max(0, columns - 1) * gap)) / 2;
 }

 /** The HiFi wallpaper's position (style.css .wall: background-position 62% 30%) and the reading surface's faint
  * copy of it (.ivory-bg::after: 80% 20%). */
 static final float WALL_X = .62f, WALL_Y = .30f, IVORY_X = .80f, IVORY_Y = .20f;

 /** CSS background-size: cover at background-position x% y%: the source rectangle {left, top, right, bottom} of a
  * bitmapWidth x bitmapHeight image that fills a width x height area at its own aspect, the overflow cropped so
  * the point x/y of the way across the spare image lines up with x/y of the way across the area. */
 static int[] cover(int bitmapWidth, int bitmapHeight, float width, float height, float x, float y) {
  if (bitmapWidth <= 0 || bitmapHeight <= 0 || width <= 0 || height <= 0) return new int[4];
  if ((double) bitmapWidth * height > (double) bitmapHeight * width) {
   int cropW = Math.max(1, Math.round(bitmapHeight * width / height));
   int left = Math.round((bitmapWidth - cropW) * x);
   return new int[]{left, 0, left + cropW, bitmapHeight};
  }
  int cropH = Math.max(1, Math.round(bitmapWidth * height / width));
  int top = Math.round((bitmapHeight - cropH) * y);
  return new int[]{0, top, bitmapWidth, top + cropH};
 }

 /** The accessible name of app page index of total (no visible heading is drawn). */
 static String title(int index, int total) {
  return total > 1 ? "Apps, page " + (index + 1) + " of " + total : "Apps";
 }
}
