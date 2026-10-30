package dev.pixel.voice;

/** How the Home app pages fit the real inventory into the inset viewport (OS-PAGING-158-M001), after the
 * selected HiFi (prototypes/rose-hifi at ce126f56: a four-column narrow page of 84-wide cells, 64 tiles and
 * wrapping labels, 20 between rows, the grid sitting above the fixed page dots and tray). Columns follow the
 * available width only, never a label, so one long name cannot change the page's composition; rows follow
 * the available height and the measured cell height, which carries the owner's font size. Every launchable
 * app gets a cell and the page count follows from the inventory; an empty inventory is still one page.
 * All sizes are in one unit (px or dp) chosen by the caller. Pure logic, no Android types. */
final class HomeAppLayout {
 private HomeAppLayout() {}

 static final int CELL_DP = 84, TILE_DP = 64, ICON_DP = 44, LABEL_GAP_DP = 7, CELL_PAD_DP = 4, ROW_GAP_DP = 20,
   MIN_ROW_GAP_DP = 6, MARGIN_DP = 24, TOP_DP = 24, LABEL_LINES = 2;

 /** Columns for an inset content width: three only where four 72-wide cells cannot fit, four on a phone,
  * five or six on a wide (unfolded or landscape) screen. */
 static int columns(float widthDp) {
  if (widthDp < 4 * 72) return 3;
  if (widthDp < 600) return 4;
  return widthDp < 720 ? 5 : 6;
 }

 /** A cell: padding, the tile, the gap and two label lines of the measured line height. */
 static float cellHeight(float density, float labelLineHeight) {
  return (2 * CELL_PAD_DP + TILE_DP + LABEL_GAP_DP) * density + LABEL_LINES * labelLineHeight;
 }

 /** Rows of cellHeight that fit height with at least the minimum gap between them; never fewer than one. */
 static int rows(float height, float cellHeight, float density) {
  if (cellHeight <= 0 || height <= 0) return 1;
  return Math.max(1, (int) Math.floor((height + MIN_ROW_GAP_DP * density) / (cellHeight + MIN_ROW_GAP_DP * density)));
 }

 /** The gap between used rows: the reference 20 where there is room, less (down to the minimum) when not. */
 static float rowGap(float height, float cellHeight, int usedRows, float density) {
  if (usedRows <= 1) return 0;
  float spare = (height - usedRows * cellHeight) / (usedRows - 1);
  return Math.max(MIN_ROW_GAP_DP * density, Math.min(ROW_GAP_DP * density, spare));
 }

 static int pages(int apps, int perPage) {
  return Math.max(1, (apps + Math.max(1, perPage) - 1) / Math.max(1, perPage));
 }

 /** The apps on page index (0-based) as [from, to). */
 static int[] slice(int apps, int perPage, int index) {
  int from = Math.min(apps, index * perPage);
  return new int[]{from, Math.min(apps, from + perPage)};
 }

 /** Rows a page of count apps actually uses; the grid sits at the bottom of the page, above the dots. */
 static int usedRows(int count, int columns) {
  return (count + columns - 1) / columns;
 }

 /** Apps per page for an inset area and measured cell height (all px, with the display density). */
 static int perPage(float width, float height, float cellHeight, float density) {
  return columns(width / density) * rows(height, cellHeight, density);
 }

 /** The estimate used before any page has been measured: the configured screen less the status bar, the
  * navigation bar, the page top and the fixed tray and dots (dp). The first measured page corrects it. */
 static float estimatedHeightDp(float screenHeightDp, int trayDp, int gapDp, int dotsDp) {
  return screenHeightDp - TOP_DP - 24 - 48 - trayDp - 2 * gapDp - dotsDp;
 }

 /** Whether a page measured at this size holds a different number of apps than the plan it was built with. */
 static boolean replan(int plannedColumns, int plannedPerPage, float width, float height, float cellHeight, float density) {
  return plannedColumns != columns(width / density) || plannedPerPage != perPage(width, height, cellHeight, density);
 }

 /** Where the grid starts inside the page: at the bottom, above the dots; at the top if it would not fit. */
 static float gridTop(float height, float cellHeight, int usedRows, float density) {
  float grid = usedRows * cellHeight + Math.max(0, usedRows - 1) * rowGap(height, cellHeight, usedRows, density);
  return Math.max(0, height - grid);
 }

 /** The accessible name of app page index of total (no visible heading is drawn). */
 static String title(int index, int total) {
  return total > 1 ? "Apps, page " + (index + 1) + " of " + total : "Apps";
 }
}
