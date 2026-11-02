package dev.pixel.voice;

import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

/** One Home app cell (#403), shared by Home, the app pages and the System folder: the subdued tile (HomeGlassTile)
 * holding the app's mark (a HomeGlyph, the app's own themed monochrome layer, or its own icon) and the app's name
 * under it. The cell is one accessibility node named by the app (the image and the visible label are not read
 * separately) and one click target the full cell size. The caller sets the click. */
final class HomeAppTile {
 private HomeAppTile() {}

 static LinearLayout tile(Context context, Drawable icon, String label) {
  float density = context.getResources().getDisplayMetrics().density;
  LinearLayout tile = new LinearLayout(context);
  tile.setOrientation(LinearLayout.VERTICAL);
  tile.setGravity(Gravity.TOP | Gravity.CENTER_HORIZONTAL);
  tile.setPadding(dp(density, 2), dp(density, HomeAppLayout.CELL_PAD_DP), dp(density, 2), dp(density, HomeAppLayout.CELL_PAD_DP));
  tile.setContentDescription(label);
  tile.setClickable(true);
  tile.setFocusable(true);
  FrameLayout iconFrame = new FrameLayout(context);
  iconFrame.setBackground(new HomeGlassTile(density));
  iconFrame.setElevation(dp(density, 4));
  ImageView image = new ImageView(context);
  image.setImageDrawable(icon);
  image.setScaleType(ImageView.ScaleType.FIT_CENTER);
  image.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
  iconFrame.addView(image, new FrameLayout.LayoutParams(dp(density, HomeAppLayout.ICON_DP), dp(density, HomeAppLayout.ICON_DP), Gravity.CENTER));
  tile.addView(iconFrame, new LinearLayout.LayoutParams(dp(density, HomeAppLayout.TILE_DP), dp(density, HomeAppLayout.TILE_DP)));
  TextView name = label(context, label);
  name.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
  LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(-1, HomeAppLayout.LABEL_LINES * name.getLineHeight());
  labelParams.topMargin = dp(density, HomeAppLayout.LABEL_GAP_DP);
  tile.addView(name, labelParams);
  return tile;
 }

 /** The reference label (.app .label): 12.5sp system sans at weight 500, white over the wallpaper with its
  * 0 1px 3px rgba(8,22,45,.75) shadow, wrapping to two lines. */
 static TextView label(Context context, String label) {
  float density = context.getResources().getDisplayMetrics().density;
  TextView name = new TextView(context);
  name.setText(label);
  name.setTextSize(12.5f);
  name.setTextColor(0xffffffff);
  name.setTypeface(Typeface.create(Typeface.SANS_SERIF, 500, false));
  name.setLetterSpacing(.005f);
  name.setShadowLayer(dp(density, 3), 0, dp(density, 1), 0xbf08162d);
  name.setIncludeFontPadding(false);
  name.setGravity(Gravity.TOP | Gravity.CENTER_HORIZONTAL);
  name.setMaxLines(HomeAppLayout.LABEL_LINES);
  name.setEllipsize(TextUtils.TruncateAt.END);
  return name;
 }

 /** As MainActivity.dp: truncated, so the cells keep their sizes exactly. */
 static int dp(float density, float value) { return (int) (value * density); }
}
