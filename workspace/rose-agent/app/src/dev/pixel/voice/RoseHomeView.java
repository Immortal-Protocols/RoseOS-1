package dev.pixel.voice;

import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Rect;
import android.graphics.Shader;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.provider.MediaStore;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;

/**
 * Launcher Home page from the Rose-Fold-v3 prototype (narrow Home): the prototype wallpaper, a
 * centered Rose presence and a light dock of real installed apps. It owns no voice state: the
 * presence only navigates, and its quiet session mark renders the ConversationState it is given.
 */
final class RoseHomeView extends FrameLayout {
 static final int DOCK_SIZE = 4;
 /** Decoded once per process: MainActivity rebuilds its pages on every HOME press. */
 static Bitmap cachedWallpaper;
 final Bitmap wallpaper;
 final View presence;
 final TextView sessionMark;
 HomeSessionMark.Mark shownMark;
 final Rect source = new Rect(), target = new Rect();
 final Paint picture = new Paint(Paint.FILTER_BITMAP_FLAG);

 RoseHomeView(MainActivity activity, List<ResolveInfo> dock, Runnable openRose, Runnable openApps, View pageDots) {
  super(activity);
  setWillNotDraw(false);
  setContentDescription("Home");
  if (cachedWallpaper == null) cachedWallpaper = BitmapFactory.decodeResource(getResources(), R.drawable.rose_home_wallpaper);
  wallpaper = cachedWallpaper;
  LinearLayout column = new LinearLayout(activity);
  column.setOrientation(LinearLayout.VERTICAL);
  column.setGravity(Gravity.CENTER_HORIZONTAL);
  int side = activity.dp(18), top = activity.dp(20), bottom = activity.dp(4);
  column.setPadding(side, top, side, bottom);
  column.setOnApplyWindowInsetsListener((v, i) -> {
   v.setPadding(side + i.getSystemWindowInsetLeft(), top + i.getSystemWindowInsetTop(),
     side + i.getSystemWindowInsetRight(), bottom + i.getSystemWindowInsetBottom());
   return i;
  });
  addView(column, new LayoutParams(-1, -1));

  // The prototype centers the presence in the space above the dock.
  FrameLayout organism = new FrameLayout(activity);
  presence = presence(activity, openRose);
  organism.addView(presence, new FrameLayout.LayoutParams(activity.dp(146), activity.dp(146), Gravity.CENTER));
  // Sits on the card's top-right corner, outside its padding, above its elevation.
  sessionMark = activity.text("", 15, 0xff283e39);
  sessionMark.setGravity(Gravity.CENTER);
  sessionMark.setIncludeFontPadding(false);
  GradientDrawable markBack = new GradientDrawable();
  markBack.setShape(GradientDrawable.OVAL);
  markBack.setColor(0xf2fbfcf5);
  markBack.setStroke(Math.max(1, activity.dp(1)), 0xffe0e6d8);
  sessionMark.setBackground(markBack);
  sessionMark.setElevation(activity.dp(7));
  sessionMark.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  sessionMark.setTranslationX(activity.dp(73 - 10));
  sessionMark.setTranslationY(-activity.dp(73 - 10));
  sessionMark.setVisibility(GONE);
  organism.addView(sessionMark, new FrameLayout.LayoutParams(activity.dp(32), activity.dp(32), Gravity.CENTER));
  column.addView(organism, new LinearLayout.LayoutParams(-1, 0, 1));

  LinearLayout tray = new LinearLayout(activity);
  tray.setGravity(Gravity.CENTER_VERTICAL);
  tray.setPadding(activity.dp(8), activity.dp(12), activity.dp(8), activity.dp(12));
  GradientDrawable trayBack = new GradientDrawable();
  trayBack.setColor(0xd9fafcf4);
  trayBack.setCornerRadius(activity.dp(24));
  tray.setBackground(trayBack);
  for (ResolveInfo app : dock) {
   String label = AppLabels.label(activity.getPackageManager(), app.activityInfo.packageName,
     app.loadLabel(activity.getPackageManager()).toString());
   int iconResource = AppLabels.icon(activity.getPackageManager(), app.activityInfo.packageName);
   ImageView icon = new ImageView(activity);
   icon.setImageDrawable(iconResource == 0 ? app.loadIcon(activity.getPackageManager()) : activity.getDrawable(iconResource));
   tray.addView(dockTile(activity, icon, label, () -> WorkspaceLauncher.open(activity, app, label)), new LinearLayout.LayoutParams(0, -1, 1));
  }
  TextView glyph = activity.text("▦", 30, 0xff3b5547);
  glyph.setGravity(Gravity.CENTER);
  tray.addView(dockTile(activity, glyph, "All apps", openApps), new LinearLayout.LayoutParams(0, -1, 1));
  LinearLayout.LayoutParams trayParams = new LinearLayout.LayoutParams(-1, -2);
  trayParams.bottomMargin = activity.dp(8);
  column.addView(tray, trayParams);
  column.addView(pageDots, new LinearLayout.LayoutParams(-1, activity.dp(30)));
 }

 /** Shows the session from the authoritative state: microphone on, muted or connecting; nothing when
  * voice is unavailable or ended. Called from the activity's refresh tick, so returning shows fresh state. */
 void showSession(ConversationState state) {
  HomeSessionMark.Mark mark = HomeSessionMark.of(state);
  if (mark == shownMark) return;
  shownMark = mark;
  switch (mark) {
   case LISTENING: sessionMark.setText("🎙"); break;
   case MUTED: sessionMark.setText("🔇"); break;
   case CONNECTING: sessionMark.setText("◌"); break;
   default: sessionMark.setText(""); break;
  }
  sessionMark.setVisibility(mark == HomeSessionMark.Mark.NONE ? GONE : VISIBLE);
  String spoken = HomeSessionMark.description(mark);
  presence.setContentDescription(spoken.isEmpty() ? "Open Rose" : "Open Rose. " + spoken);
 }

 /** A plain icon over its label, as in the prototype dock; no tile background. */
 static View dockTile(MainActivity activity, View icon, String label, Runnable open) {
  LinearLayout tile = new LinearLayout(activity);
  tile.setOrientation(LinearLayout.VERTICAL);
  // Top-aligned at the tray's tallest tile, so icons stay level when one label takes two lines.
  tile.setGravity(Gravity.CENTER_HORIZONTAL | Gravity.TOP);
  tile.setPadding(activity.dp(4), activity.dp(8), activity.dp(4), activity.dp(6));
  tile.setClickable(true);
  tile.setFocusable(true);
  tile.setContentDescription(label);
  tile.setOnClickListener(v -> open.run());
  icon.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  if (icon instanceof ImageView) ((ImageView) icon).setScaleType(ImageView.ScaleType.FIT_CENTER);
  tile.addView(icon, new LinearLayout.LayoutParams(activity.dp(46), activity.dp(46)));
  DockLabelView name = new DockLabelView(activity, label);
  name.setTextSize(12);
  name.setTextColor(0xff22382e);
  name.setGravity(Gravity.CENTER_HORIZONTAL);
  name.setTypeface(activity.chatFont(800));
  name.setIncludeFontPadding(false);
  name.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
  LinearLayout.LayoutParams nameParams = new LinearLayout.LayoutParams(-1, -2);
  nameParams.topMargin = activity.dp(10);
  tile.addView(name, nameParams);
  return tile;
 }

 /** A dock label fitted to its tile width by DockLabel: the whole name, never a one-line cut. */
 static final class DockLabelView extends TextView {
  final String label;
  float basePx;

  DockLabelView(MainActivity activity, String label) {
   super(activity);
   this.label = label;
   setText(label);
  }

  /** The dock size the owner's font scale gives; fitting may go down to 10/12 of it. */
  @Override public void setTextSize(float sp) {
   super.setTextSize(sp);
   basePx = getTextSize();
  }

  @Override protected void onMeasure(int widthSpec, int heightSpec) {
   float available = MeasureSpec.getSize(widthSpec) - getPaddingLeft() - getPaddingRight();
   if (available > 0 && basePx > 0) {
    android.text.TextPaint paint = new android.text.TextPaint(getPaint());
    DockLabel fit = DockLabel.fit(label, basePx, basePx * 10 / 12, available, (text, size) -> {
     paint.setTextSize(size);
     float widest = 0;
     for (String line : text.split("\n")) widest = Math.max(widest, paint.measureText(line));
     return widest;
    });
    if (!fit.text.contentEquals(getText())) setText(fit.text);
    if (getTextSize() != fit.sizePx) super.setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX, fit.sizePx);
    // Each setter runs only on a change, so a measure pass never requests another layout once settled.
    if (getMaxLines() != fit.lines) setMaxLines(fit.lines);
    android.text.TextUtils.TruncateAt end = fit.ellipsize ? android.text.TextUtils.TruncateAt.END : null;
    if (getEllipsize() != end) setEllipsize(end);
   }
   super.onMeasure(widthSpec, heightSpec);
  }
 }

 /** Opens the conversation surface; never starts, mutes or ends the voice session. */
 static View presence(MainActivity activity, Runnable openRose) {
  FrameLayout card = new FrameLayout(activity);
  GradientDrawable back = new GradientDrawable();
  back.setColor(0xdbfbfcf5);
  back.setStroke(Math.max(1, activity.dp(1)), 0xc9ffffff);
  back.setCornerRadius(activity.dp(38));
  card.setBackground(back);
  card.setElevation(activity.dp(6));
  card.addView(new RoseMark(activity), new FrameLayout.LayoutParams(-1, -1));
  card.setPadding(activity.dp(8), activity.dp(8), activity.dp(8), activity.dp(8));
  card.setClickable(true);
  card.setFocusable(true);
  card.setContentDescription("Open Rose");
  card.setOnClickListener(v -> openRose.run());
  return card;
 }

 /** The prototype's static rose mark (its SVG fallback, viewBox 320), drawn natively. */
 static final class RoseMark extends View {
  final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG), line = new Paint(Paint.ANTI_ALIAS_FLAG);
  final Path inside = new Path(), left = new Path(), back = new Path(), right = new Path(), top = new Path();
  Shader insideShader, petalShader;

  RoseMark(android.content.Context context) {
   super(context);
   setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
   line.setStyle(Paint.Style.STROKE);
   line.setStrokeWidth(1);
   inside.moveTo(160, 68); inside.cubicTo(211, 82, 243, 125, 228, 186); inside.quadTo(198, 235, 160, 258);
   inside.quadTo(105, 231, 91, 180); inside.quadTo(83, 116, 160, 68); inside.close();
   left.moveTo(160, 258); left.cubicTo(72, 225, 58, 126, 100, 107); left.cubicTo(164, 119, 204, 180, 160, 258); left.close();
   back.moveTo(100, 107); back.cubicTo(145, 109, 141, 34, 184, 52); back.cubicTo(210, 88, 162, 134, 160, 258);
   back.cubicTo(151, 181, 141, 141, 100, 107); back.close();
   right.moveTo(160, 258); right.cubicTo(186, 192, 199, 134, 257, 134); right.cubicTo(260, 205, 210, 243, 160, 258); right.close();
   top.moveTo(184, 52); top.cubicTo(197, 112, 227, 107, 257, 134); top.cubicTo(209, 159, 181, 103, 184, 52); top.close();
   // SVG gradients use the object bounding box; in viewBox units these approximate each shape's box.
   insideShader = new LinearGradient(91, 0, 228, 0, 0xffefcdb5, 0xfff4f0db, Shader.TileMode.CLAMP);
   petalShader = new LinearGradient(60, 34, 260, 258, new int[]{0xfffaf4df, 0xffd5e1cf, 0xff819c87},
     new float[]{0, .45f, 1}, Shader.TileMode.CLAMP);
  }

  @Override protected void onDraw(Canvas canvas) {
   float size = Math.min(getWidth(), getHeight());
   if (size <= 0) return;
   canvas.save();
   canvas.translate((getWidth() - size) / 2f, (getHeight() - size) / 2f);
   canvas.scale(size / 320f, size / 320f);
   fill.setShader(insideShader);
   canvas.drawPath(inside, fill);
   fill.setShader(petalShader);
   draw(canvas, left, 0xffa9bbaa);
   draw(canvas, back, 0xffc4cfb9);
   draw(canvas, right, 0xffa9bbaa);
   draw(canvas, top, 0xffc4cfb9);
   canvas.restore();
  }

  void draw(Canvas canvas, Path petal, int stroke) {
   canvas.drawPath(petal, fill);
   line.setColor(stroke);
   canvas.drawPath(petal, line);
  }
 }

 /**
  * The owner's default phone, messages, camera and browser apps, in that order, taken from the
  * launchable apps. A role with no single default (a chooser) or no launchable activity is left out.
  */
 static List<ResolveInfo> dockApps(PackageManager pm, String self, List<ResolveInfo> launchable) {
  ArrayList<ResolveInfo> dock = new ArrayList<>();
  for (String pkg : dockRolePackages(pm, self)) {
   if (dock.size() == DOCK_SIZE) break;
   for (ResolveInfo app : launchable) if (pkg.equals(app.activityInfo.packageName)) { dock.add(app); break; }
  }
  return dock;
 }

 /** The packages holding those defaults, in order, without enumerating apps: four resolutions, cheap
  * enough to compare on every HOME so a changed default app rebuilds the dock. */
 static List<String> dockRolePackages(PackageManager pm, String self) {
  Intent[] roles = {
   new Intent(Intent.ACTION_DIAL, Uri.parse("tel:")),
   new Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:")),
   new Intent(MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA),
   new Intent(Intent.ACTION_VIEW, Uri.parse("https://"))
  };
  ArrayList<String> packages = new ArrayList<>();
  HashSet<String> used = new HashSet<>();
  for (Intent role : roles) {
   ResolveInfo handler = pm.resolveActivity(role, PackageManager.MATCH_DEFAULT_ONLY);
   String pkg = handler == null || handler.activityInfo == null ? null : handler.activityInfo.packageName;
   if (pkg == null || "android".equals(pkg) || pkg.equals(self) || !used.add(pkg)) continue;
   packages.add(pkg);
  }
  return packages;
 }

 @Override public WindowInsets dispatchApplyWindowInsets(WindowInsets insets) {
  for (int i = 0; i < getChildCount(); i++) getChildAt(i).dispatchApplyWindowInsets(insets);
  return insets;
 }

 /** CSS background-size: cover, centered: scale to fill and crop the overflow equally. */
 @Override protected void onSizeChanged(int w, int h, int oldW, int oldH) {
  super.onSizeChanged(w, h, oldW, oldH);
  target.set(0, 0, w, h);
  if (wallpaper == null || w <= 0 || h <= 0) return;
  int bw = wallpaper.getWidth(), bh = wallpaper.getHeight();
  if ((long) bw * h > (long) bh * w) {
   int cropW = Math.round(bh * (float) w / h);
   source.set((bw - cropW) / 2, 0, (bw + cropW) / 2, bh);
  } else {
   int cropH = Math.round(bw * (float) h / w);
   source.set(0, (bh - cropH) / 2, bw, (bh + cropH) / 2);
  }
 }

 @Override protected void onDraw(Canvas canvas) {
  if (wallpaper != null && !source.isEmpty()) canvas.drawBitmap(wallpaper, source, target, picture);
  else canvas.drawColor(0xffdfe8e0);
 }
}
