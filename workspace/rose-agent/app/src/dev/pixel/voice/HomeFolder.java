package dev.pixel.voice;

import android.content.Context;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.util.List;

/**
 * The open "…" System folder (#403): the system and duplicate apps (HomeAppGroups.SYSTEM) in a panel over a dimmed
 * Home, in the same cells as the app pages (HomeAppTile), scrolling when they do not fit. Tapping outside the panel,
 * or Back (the hosting dialog), closes it; launching an app from it closes it first (MainActivity). The panel is an
 * accessibility pane named by its title, with the title as its heading; the dimmed area is a labelled close button.
 * The dim covers the whole window; the system-bar insets only move the panel. Holds no state of its own.
 */
final class HomeFolder extends FrameLayout {
 static final int MARGIN_DP = 24, PANEL_PAD_DP = 18, RADIUS_DP = 28, ROW_GAP_DP = 12;
 static final int SCRIM = 0x99100306, PANEL = 0xf2240a10, PANEL_RING = 0x33ffd9cc;

 final View scrim;
 final LinearLayout panel;
 final TextView title;
 final Grid grid;

 HomeFolder(Context context, String name, List<View> tiles, int columns, Runnable close) {
  super(context);
  float density = context.getResources().getDisplayMetrics().density;
  scrim = new View(context);
  scrim.setBackgroundColor(SCRIM);
  scrim.setContentDescription("Close " + name);
  scrim.setOnClickListener(v -> close.run());
  addView(scrim, new LayoutParams(-1, -1));

  panel = new LinearLayout(context);
  panel.setOrientation(LinearLayout.VERTICAL);
  GradientDrawable surface = new GradientDrawable();
  surface.setColor(PANEL);
  surface.setCornerRadius(RADIUS_DP * density);
  surface.setStroke(Math.max(1, Math.round(density)), PANEL_RING);
  panel.setBackground(surface);
  panel.setElevation(12 * density);
  int pad = HomeAppTile.dp(density, PANEL_PAD_DP);
  panel.setPadding(pad, pad, pad, pad);
  panel.setClickable(true); // a tap on the panel between cells does not reach the scrim
  panel.setAccessibilityPaneTitle(name);

  title = new TextView(context);
  title.setText(name);
  title.setTextSize(17f);
  title.setTextColor(HomeGlyph.CREAM);
  title.setTypeface(CustomizationVisuals.labelTypeface(context, 500)); // 1O font choice; default SANS_SERIF 500
  title.setGravity(Gravity.CENTER);
  title.setAccessibilityHeading(true);
  LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(-1, -2);
  titleParams.bottomMargin = HomeAppTile.dp(density, 12);
  panel.addView(title, titleParams);

  grid = new Grid(context, columns, HomeAppTile.dp(density, ROW_GAP_DP));
  for (View tile : tiles) grid.addView(tile);
  ScrollView scroll = new ScrollView(context);
  scroll.setVerticalScrollBarEnabled(false);
  scroll.addView(grid, new ScrollView.LayoutParams(-1, -2));
  panel.addView(scroll, new LinearLayout.LayoutParams(-1, -2));

  int margin = HomeAppTile.dp(density, MARGIN_DP);
  LayoutParams panelParams = new LayoutParams(-1, -2, Gravity.CENTER);
  panelParams.setMargins(margin, margin, margin, margin);
  addView(panel, panelParams);
  // The dim covers the whole window, under the system bars too; only the panel keeps clear of them.
  setOnApplyWindowInsetsListener((v, insets) -> {
   panelParams.setMargins(margin + insets.getSystemWindowInsetLeft(), margin + insets.getSystemWindowInsetTop(),
     margin + insets.getSystemWindowInsetRight(), margin + insets.getSystemWindowInsetBottom());
   panel.setLayoutParams(panelParams);
   return insets;
  });
 }

 /** Equal columns across the panel; rows of the tallest cell, ROW_GAP apart, top-aligned; as tall as its rows. */
 static final class Grid extends ViewGroup {
  final int columns, gap;
  int cellWidth, cellHeight;

  Grid(Context context, int columns, int gap) { super(context); this.columns = Math.max(1, columns); this.gap = gap; }

  @Override protected void onMeasure(int widthSpec, int heightSpec) {
   int width = MeasureSpec.getSize(widthSpec);
   cellWidth = width / columns;
   cellHeight = 0;
   for (int i = 0; i < getChildCount(); i++) {
    getChildAt(i).measure(MeasureSpec.makeMeasureSpec(cellWidth, MeasureSpec.EXACTLY), MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED));
    cellHeight = Math.max(cellHeight, getChildAt(i).getMeasuredHeight());
   }
   for (int i = 0; i < getChildCount(); i++) getChildAt(i).measure(MeasureSpec.makeMeasureSpec(cellWidth, MeasureSpec.EXACTLY),
     MeasureSpec.makeMeasureSpec(cellHeight, MeasureSpec.EXACTLY));
   int rows = HomeAppLayout.usedRows(getChildCount(), columns);
   setMeasuredDimension(width, rows == 0 ? 0 : rows * cellHeight + (rows - 1) * gap);
  }

  @Override protected void onLayout(boolean changed, int l, int t, int r, int b) {
   int left = (getWidth() - cellWidth * columns) / 2;
   for (int i = 0; i < getChildCount(); i++) {
    int x = left + (i % columns) * cellWidth, y = (i / columns) * (cellHeight + gap);
    getChildAt(i).layout(x, y, x + cellWidth, y + cellHeight);
   }
  }
 }
}
