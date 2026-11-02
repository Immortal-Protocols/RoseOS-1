package dev.pixel.voice;

import android.animation.ObjectAnimator;
import android.animation.StateListAnimator;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BlurMaskFilter;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.text.TextUtils;
import android.util.Property;
import android.view.Gravity;
import android.view.View;
import android.view.animation.PathInterpolator;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

/** One Home app cell (#410; the committed Oxblood Rose HiFi, main f6503a0, oxblood.css:38-43,215-228,354-363;
 * qa/oxblood/oxblood-contract.json "homeGrid"), shared by Home, the app pages and the System folder: the faint glass
 * tile (HomeGlassTile, 59dp) holding the app's mark, and on the app pages its name under it. A Rose suite app or the
 * System folder shows its monoline glyph (HomeGlyph, 46dp); any other installed app keeps its own icon unrestyled
 * (40dp, with the reference's soft drop shadow). Home's cells show no visible name, as in the reference; the name
 * stays the cell's accessible name. The cell is one accessibility node and one click target the full cell size, and
 * the tile presses to 94% over 160ms (instant when animations are off). The caller sets the click. */
final class HomeAppTile {
 private HomeAppTile() {}

 static final PathInterpolator EASE = new PathInterpolator(.2f, .8f, .2f, 1f);

 /** labelled: the app pages and the folder show the name; Home does not. glyph: the mark is a HomeGlyph. */
 static LinearLayout tile(Context context, Drawable icon, String label, boolean glyph, boolean labelled) {
  float density = context.getResources().getDisplayMetrics().density;
  LinearLayout tile = new LinearLayout(context);
  tile.setOrientation(LinearLayout.VERTICAL);
  tile.setGravity(Gravity.TOP | Gravity.CENTER_HORIZONTAL);
  tile.setContentDescription(label);
  tile.setClickable(true);
  tile.setFocusable(true);
  tile.setClipChildren(false);
  FrameLayout iconFrame = new FrameLayout(context);
  iconFrame.setBackground(new HomeGlassTile(density));
  iconFrame.setClipChildren(false);
  iconFrame.setDuplicateParentStateEnabled(true);
  iconFrame.setStateListAnimator(pressAnimator(context, View.SCALE_X, 1f, .94f, 160, View.SCALE_Y));
  ImageView image = new ImageView(context);
  image.setImageDrawable(glyph ? icon : new Shadowed(icon, density));
  image.setScaleType(ImageView.ScaleType.FIT_CENTER);
  image.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
  int mark = dp(density, glyph ? HomeAppLayout.GLYPH_DP : HomeAppLayout.ICON_DP);
  iconFrame.addView(image, new FrameLayout.LayoutParams(mark, mark, Gravity.CENTER));
  tile.addView(iconFrame, new LinearLayout.LayoutParams(dp(density, HomeAppLayout.TILE_DP), dp(density, HomeAppLayout.TILE_DP)));
  if (labelled) {
   TextView name = label(context, label);
   name.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
   LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(-1, HomeAppLayout.LABEL_LINES * name.getLineHeight());
   labelParams.topMargin = dp(density, HomeAppLayout.LABEL_GAP_DP);
   tile.addView(name, labelParams);
  }
  return tile;
 }

 /** The reference label (oxblood.css:43, style.css:590): 11.5sp system sans at weight 500 in the field's secondary
  * ink #d2b9ab with a soft dark shadow (0 1px 4px rgba(0,0,0,.7)), centred, wrapping to two lines so a long name is
  * never cut to one. */
 static TextView label(Context context, String label) {
  float density = context.getResources().getDisplayMetrics().density;
  TextView name = new TextView(context);
  name.setText(label);
  name.setTextSize(11.5f);
  name.setTextColor(0xffd2b9ab);
  name.setTypeface(Typeface.create(Typeface.SANS_SERIF, 500, false));
  name.setShadowLayer(dp(density, 2), 0, dp(density, 1), 0xb3000000);
  name.setIncludeFontPadding(false);
  name.setGravity(Gravity.TOP | Gravity.CENTER_HORIZONTAL);
  name.setMaxLines(HomeAppLayout.LABEL_LINES);
  name.setEllipsize(TextUtils.TruncateAt.END);
  return name;
 }

 /** A pressed-state animation of one or two view properties, ease (.2,.8,.2,1). Animator durations follow the system
  * animator scale, so with animations off the press is instant and the pressed state still shows. */
 @SafeVarargs
 static StateListAnimator pressAnimator(Context context, Property<View, Float> property, float rest, float pressed, long ms,
   Property<View, Float>... also) {
  StateListAnimator animator = new StateListAnimator();
  animator.addState(new int[]{android.R.attr.state_pressed}, animate(property, pressed, ms, also));
  animator.addState(new int[0], animate(property, rest, ms, also));
  return animator;
 }
 @SafeVarargs
 private static android.animation.Animator animate(Property<View, Float> property, float value, long ms, Property<View, Float>... also) {
  android.animation.PropertyValuesHolder[] holders = new android.animation.PropertyValuesHolder[1 + also.length];
  holders[0] = android.animation.PropertyValuesHolder.ofFloat(property, value);
  for (int i = 0; i < also.length; i++) holders[i + 1] = android.animation.PropertyValuesHolder.ofFloat(also[i], value);
  ObjectAnimator a = ObjectAnimator.ofPropertyValuesHolder((Object) null, holders);
  a.setDuration(ms);
  a.setInterpolator(EASE);
  return a;
 }

 /** As MainActivity.dp: truncated, so the cells keep their sizes exactly. */
 static int dp(float density, float value) { return (int) (value * density); }

 /** An app's own icon with the reference's drop-shadow(0 2px 3px rgba(0,0,0,.45)), following the icon's own alpha. */
 static final class Shadowed extends Drawable {
  final Drawable icon;
  final float density;
  final Paint shadow = new Paint(Paint.ANTI_ALIAS_FLAG);
  Bitmap mask;
  final int[] offset = new int[2];
  Shadowed(Drawable icon, float density) {
   this.icon = icon;
   this.density = density;
   shadow.setColor(0x73000000);
  }
  @Override protected void onBoundsChange(Rect b) {
   icon.setBounds(b);
   mask = null;
   if (b.isEmpty()) return;
   try {
    Bitmap art = Bitmap.createBitmap(b.width(), b.height(), Bitmap.Config.ARGB_8888);
    Canvas c = new Canvas(art);
    c.translate(-b.left, -b.top);
    icon.draw(c);
    Paint blur = new Paint();
    blur.setMaskFilter(new BlurMaskFilter(Math.max(.1f, (1.5f * density - .5f) / .57735f), BlurMaskFilter.Blur.NORMAL));
    mask = art.extractAlpha(blur, offset);
    art.recycle();
   } catch (RuntimeException | OutOfMemoryError unavailable) { mask = null; }
  }
  @Override public void draw(Canvas c) {
   Rect b = getBounds();
   if (mask != null) c.drawBitmap(mask, b.left + offset[0], b.top + offset[1] + 2 * density, shadow);
   icon.draw(c);
  }
  @Override public int getIntrinsicWidth() { return icon.getIntrinsicWidth(); }
  @Override public int getIntrinsicHeight() { return icon.getIntrinsicHeight(); }
  @Override public void setAlpha(int alpha) { icon.setAlpha(alpha); }
  @Override public void setColorFilter(ColorFilter filter) { icon.setColorFilter(filter); }
  @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
 }
}
