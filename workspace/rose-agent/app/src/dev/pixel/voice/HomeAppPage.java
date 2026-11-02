package dev.pixel.voice;

import android.content.Context;
import android.view.View;
import android.view.ViewGroup;

/** One Home page grid over the shared field (OS-PAGING-158-M001; #410 Oxblood geometry, HomeAppLayout): a fixed
 * number of columns of fixed-width cells (72dp app cells 6dp apart, or Home's 60dp cells 17.5dp apart), centred, and
 * as many rows as the plan gave it, 14dp apart, placed in the band just above the dots: its top is max(170dp, its
 * height) above the page's inset bottom. Cells take their real measured height, so the owner's font size is honoured,
 * and each measurement is reported once per size so MainActivity can re-plan the pages from the real inset viewport.
 * No heading is drawn; the page's name is its content description. Insets arrive as padding. */
final class HomeAppPage extends ViewGroup {
 interface Fit { void measured(int width, int height, float cellHeight); }

 final int columns;
 final float density, cellWidthDp, gapDp;
 final Fit fit;
 int reportedWidth = -1, reportedHeight = -1, cellHeight;

 /** An app page: 72dp cells 6dp apart. */
 HomeAppPage(Context context, int columns, Fit fit) {
  this(context, columns, HomeAppLayout.CELL_DP, HomeAppLayout.COL_GAP_DP, fit);
 }

 HomeAppPage(Context context, int columns, float cellWidthDp, float gapDp, Fit fit) {
  super(context);
  this.columns = Math.max(1, columns);
  this.density = context.getResources().getDisplayMetrics().density;
  this.cellWidthDp = cellWidthDp;
  this.gapDp = gapDp;
  this.fit = fit;
  setClipChildren(false);
 }

 int cellWidth(int innerWidth) {
  // Never wider than an equal share of the width (a very narrow screen), else the reference cell width.
  int gap = Math.round(gapDp * density);
  return Math.max(1, Math.min(Math.round(cellWidthDp * density), (innerWidth - (columns - 1) * gap) / columns));
 }

 @Override protected void onMeasure(int widthSpec, int heightSpec) {
  int width = MeasureSpec.getSize(widthSpec), height = MeasureSpec.getSize(heightSpec);
  setMeasuredDimension(width, height);
  int innerWidth = Math.max(0, width - getPaddingLeft() - getPaddingRight());
  int innerHeight = Math.max(0, height - getPaddingTop() - getPaddingBottom());
  int cellWidth = cellWidth(innerWidth);
  cellHeight = 0;
  for (int i = 0; i < getChildCount(); i++) {
   View child = getChildAt(i);
   child.measure(MeasureSpec.makeMeasureSpec(cellWidth, MeasureSpec.EXACTLY), MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED));
   cellHeight = Math.max(cellHeight, child.getMeasuredHeight());
  }
  for (int i = 0; i < getChildCount(); i++) getChildAt(i).measure(MeasureSpec.makeMeasureSpec(cellWidth, MeasureSpec.EXACTLY),
    MeasureSpec.makeMeasureSpec(cellHeight, MeasureSpec.EXACTLY));
  if (fit != null && innerWidth > 0 && innerHeight > 0 && (innerWidth != reportedWidth || innerHeight != reportedHeight)) {
   reportedWidth = innerWidth;
   reportedHeight = innerHeight;
   float cell = cellHeight > 0 ? cellHeight : HomeAppLayout.cellHeight(density, 0);
   post(() -> fit.measured(innerWidth, innerHeight, cell));
  }
 }

 @Override protected void onLayout(boolean changed, int l, int t, int r, int b) {
  int count = getChildCount();
  if (count == 0) return;
  int innerWidth = Math.max(0, getWidth() - getPaddingLeft() - getPaddingRight());
  int innerHeight = Math.max(0, getHeight() - getPaddingTop() - getPaddingBottom());
  int cellWidth = cellWidth(innerWidth), gap = Math.round(gapDp * density);
  int used = HomeAppLayout.usedRows(count, columns);
  float rowGap = HomeAppLayout.rowGap(density);
  float top = getPaddingTop() + HomeAppLayout.gridTop(innerHeight, HomeAppLayout.gridHeight(used, cellHeight, density), density);
  int left = getPaddingLeft() + Math.round(HomeAppLayout.gridLeft(innerWidth, columns, cellWidth, gap));
  for (int i = 0; i < count; i++) {
   int x = left + (i % columns) * (cellWidth + gap), y = Math.round(top + (i / columns) * (cellHeight + rowGap));
   getChildAt(i).layout(x, y, x + cellWidth, y + cellHeight);
  }
 }
}
