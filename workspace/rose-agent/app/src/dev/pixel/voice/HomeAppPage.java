package dev.pixel.voice;

import android.content.Context;
import android.view.View;
import android.view.ViewGroup;

/** One Home app page over the shared wallpaper (OS-PAGING-158-M001): a fixed number of equal columns
 * (HomeAppLayout.columns of the inset width) and as many rows as the plan gave it, sitting at the bottom of
 * the page just above the fixed dots, rows HomeAppLayout.rowGap apart. Cells take their real measured height,
 * so the owner's font size is honoured, and each measurement is reported once per size so MainActivity can
 * re-plan the pages from the real inset viewport. No heading is drawn; the page's name is its content
 * description. Insets arrive as padding. */
final class HomeAppPage extends ViewGroup {
 interface Fit { void measured(int width, int height, float cellHeight); }

 final int columns;
 final float density;
 final Fit fit;
 int reportedWidth = -1, reportedHeight = -1, cellHeight;

 HomeAppPage(Context context, int columns, Fit fit) {
  super(context);
  this.columns = Math.max(1, columns);
  this.density = context.getResources().getDisplayMetrics().density;
  this.fit = fit;
 }

 @Override protected void onMeasure(int widthSpec, int heightSpec) {
  int width = MeasureSpec.getSize(widthSpec), height = MeasureSpec.getSize(heightSpec);
  setMeasuredDimension(width, height);
  int innerWidth = Math.max(0, width - getPaddingLeft() - getPaddingRight());
  int innerHeight = Math.max(0, height - getPaddingTop() - getPaddingBottom());
  int cellWidth = innerWidth / columns;
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
  int cellWidth = innerWidth / columns, used = HomeAppLayout.usedRows(count, columns);
  float gap = HomeAppLayout.rowGap(innerHeight, cellHeight, used, density);
  float top = getPaddingTop() + HomeAppLayout.gridTop(innerHeight, cellHeight, used, density);
  int left = getPaddingLeft() + (innerWidth - cellWidth * columns) / 2;
  for (int i = 0; i < count; i++) {
   int x = left + (i % columns) * cellWidth, y = Math.round(top + (i / columns) * (cellHeight + gap));
   getChildAt(i).layout(x, y, x + cellWidth, y + cellHeight);
  }
 }
}
