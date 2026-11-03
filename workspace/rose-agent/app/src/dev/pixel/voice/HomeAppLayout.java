package dev.pixel.voice;

/** How the Home pages fit the real inventory into the inset viewport (OS-PAGING-158-M001), in the Oxblood materials
 * of the committed Rose HiFi (#410; main f6503a0, oxblood.css:354-363; qa/oxblood/oxblood-contract.json "homeGrid").
 * Density follows the owner's phone-test correction (#410 comment 6592423187), which supersedes the reference's two
 * bottom rows: every page is filled with as many rows as the measured safe area holds, from its top down to the
 * reserve above the dots and the edge, and only then does the inventory spill onto the next page; the last page is
 * naturally partial. App pages: 72dp cells 6dp apart (a 59dp tile, 5dp gap and the name), rows at least 14dp apart
 * with any spare height shared between them (up to MAX_EXTRA_GAP_DP each) so a full page reaches its bottom. Home:
 * the same rule for its 60dp unlabelled cells below the date. Columns follow the available width only, never a label;
 * cells keep their measured height, so large text means fewer rows, never overlap. Every launchable app gets one cell
 * and the page count follows from the inventory; an empty inventory is still one page. Sizes are in one unit (px or
 * dp) chosen by the caller. Pure logic, no Android types. */
final class HomeAppLayout {
 private HomeAppLayout() {}

 static final int CELL_DP = 72, HOME_CELL_DP = 60, TILE_DP = 59, ICON_DP = 40, GLYPH_DP = 46, LABEL_GAP_DP = 5,
   COL_GAP_DP = 6, ROW_GAP_DP = 14, MAX_EXTRA_GAP_DP = 32, MARGIN_DP = 24, TOP_DP = 24, LABEL_LINES = 2;
 static final float HOME_COL_GAP_DP = 17.5f;
 /** From the edge's top up to the bottom of the pages' area: clear of the dots' 44dp row (centred 30dp above the edge,
  * so its top is 52dp up) by 12dp. The reference's 95dp band left a blank strip under two rows; filled pages use it. */
 static final int BAND_BOTTOM_DP = 64;
 /** Home's grid starts this far below the date's line. */
 static final int BELOW_DATE_DP = 24;
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

 /** Rows of cellHeight that fit height with at least the row gap between them: at least one, never capped. */
 static int rows(float height, float cellHeight, float density) {
  if (cellHeight <= 0 || height <= 0) return 1;
  int fit = (int) Math.floor((height + ROW_GAP_DP * density) / (cellHeight + ROW_GAP_DP * density));
  return Math.max(1, fit);
 }

 /** The minimum gap between rows. */
 static float rowGap(float density) { return ROW_GAP_DP * density; }

 /** Distance from one row's top to the next on a page of height planned for rows rows: the cell, the gap and an equal
  * share of the spare height (at most MAX_EXTRA_GAP_DP), so a full page reaches down to its bottom. Every page of a
  * grid uses the same pitch, so a partial last page keeps the same rows from the top. */
 static float rowPitch(float height, int rows, float cellHeight, float density) {
  float base = cellHeight + rowGap(density);
  if (rows <= 1) return base;
  float spare = height - gridHeight(rows, cellHeight, density);
  return base + Math.max(0, Math.min(MAX_EXTRA_GAP_DP * density, spare / (rows - 1)));
 }

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
  * the edge and the reserve above it (dp). The first measured page corrects it. */
 static float estimatedHeightDp(float screenHeightDp) {
  return screenHeightDp - TOP_DP - 24 - EDGE_DP - BAND_BOTTOM_DP;
 }

 /** The same estimate for Home's grid, which starts below the date's line (dateLineDp tall) instead of the page top. */
 static float estimatedHomeHeightDp(float screenHeightDp, float dateLineDp) {
  return screenHeightDp - Math.max(DATE_TOP_DP, 24 + 21) - dateLineDp - BELOW_DATE_DP - EDGE_DP - BAND_BOTTOM_DP;
 }

 /** Whether a page measured at this size holds a different number of apps than the plan it was built with. */
 static boolean replan(int plannedColumns, int plannedPerPage, float width, float height, float cellHeight, float density) {
  return plannedColumns != columns(width / density) || plannedPerPage != perPage(width, height, cellHeight, density);
 }

 /** Cells on Home for an inset area below the date and its measured (unlabelled) cell height. */
 static int homeCapacity(float width, float height, float cellHeight, float density) {
  return homeColumns(width / density) * rows(height, cellHeight, density);
 }

 /** How Home's capacity is shared: {curated apps kept, inventory apps that fill the rest}. The System folder, when
  * there is one, always keeps the last cell; the curated apps (the owner's defaults and the suite) come first and are
  * cut only when Home cannot hold them all; the remaining cells take the start of the normal app inventory, which the
  * app pages then continue (no app is placed twice to fill space). */
 static int[] homeShare(int curated, boolean folder, int inventory, int capacity) {
  int cells = Math.max(1, capacity) - (folder ? 1 : 0);
  int kept = Math.max(0, Math.min(curated, cells));
  int fill = Math.max(0, Math.min(inventory, cells - kept));
  return new int[]{kept, fill};
 }

 /** Where Home's grid starts: below the date, whose line starts at dateTop (already clear of the status bar) and is
  * dateLine tall. */
 static float homeTop(float dateTop, float dateLine, float density) {
  return dateTop + dateLine + BELOW_DATE_DP * density;
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
