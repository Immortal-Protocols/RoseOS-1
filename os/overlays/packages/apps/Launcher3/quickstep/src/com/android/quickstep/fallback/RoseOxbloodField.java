/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BlurMaskFilter;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.PorterDuff;
import android.graphics.PorterDuffXfermode;
import android.graphics.RadialGradient;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;

/**
 * RoseOS (#410): the switcher's Oxblood field, OS Product's own Launcher3 renderer of the contract's fields.rose
 * (qa/oxblood/oxblood-contract.json; switcher.field.kind "rose"), the pinned prototypes/rose-hifi asset
 * src/assets/oxblood-rose.svg at f6503a0d (blob de9f42a1). Launcher3 cannot link Rose Agent's OxbloodField, so this is
 * a separate implementation of the same artwork, drawn shape for shape from the SVG (viewBox 390x844):
 * <ul>
 * <li>base: vertical gradient #120a0d, #0e080b at .6, #12060a;</li>
 * <li>glass sheet: polygon (390,118) (390,292) (295,580), gradient from its box's top right to bottom left,
 * #8a3a4c at .35 to 0, and its hairline (390,292)-(295,580) #ffb3bd at .7, 1 wide;</li>
 * <li>crimson plane: polygon (295,580) (390,515) (390,844) (123,844) (206,642), gradient from its box's top left to
 * bottom right, #7a1428 at .9, #36020d at .45, #1a0207;</li>
 * <li>lit edge (206,642)-(295,580)-(390,515): #ffc4c8 4 wide at .5 with a soft blur (stdDeviation 3), then 1.2 wide;
 * the lower hairline (206,642)-(123,844) #c2384c at .55, 1 wide;</li>
 * <li>glint: circle at (295,580), r 26, radial #fff0f0 at .9 to #ff8a9a at 0.</li>
 * </ul>
 * Placement is the contract's: scale = max(W/390, H/844), centred across, top-aligned. Gradients in the SVG are in
 * each shape's objectBoundingBox, so each is built in unit space and mapped to its box with a local matrix. Static
 * artwork: nothing animates, so reduced motion changes nothing. Pure Android graphics; no I/O in Oxblood.
 *
 * <p>Plan 1E: the field follows the theme its owner gives it (setTheme; RoseThemeSetting). Oxblood draws the geometry
 * above unchanged. A theme with a raster wallpaper (Glacier) draws that raster instead, the package's
 * own drawable (RoseTheme.wallpaperDrawable), on the theme's field colour, scaled to cover, centred across and
 * top-aligned like the geometry;
 * if it cannot be loaded, the geometry is drawn in the theme's colours (RoseTheme.map).
 */
final class RoseOxbloodField extends Drawable {
    static final float VIEW_W = 390, VIEW_H = 844;
    static final int[] BASE = {0xFF120A0D, 0xFF0E080B, 0xFF12060A};
    static final float[] BASE_STOPS = {0f, .6f, 1f};
    static final float[] SHEET = {390, 118, 390, 292, 295, 580};
    static final float[] PLANE = {295, 580, 390, 515, 390, 844, 123, 844, 206, 642};
    static final int[] SHEET_COLORS = {0x598A3A4C, 0x008A3A4C};
    static final int[] PLANE_COLORS = {0xE67A1428, 0xFF36020D, 0xFF1A0207};
    static final float[] PLANE_STOPS = {0f, .45f, 1f};
    static final int SHEET_LINE = 0xB3FFB3BD, EDGE_GLOW = 0x80FFC4C8, EDGE = 0xFFFFC4C8, LOWER_LINE = 0x8CC2384C;
    static final int[] GLINT = {0xE6FFF0F0, 0x00FF8A9A};
    static final float GLINT_X = 295, GLINT_Y = 580, GLINT_R = 26, BLUR_STD_DEV = 3;

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
    private final Paint line = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path sheet = polygon(SHEET), plane = polygon(PLANE);
    private final Path edge = new Path(), lower = new Path(), sheetLine = new Path();
    private Shader base, sheetShader, planeShader, glint;
    private final BlurMaskFilter soft;
    private int alpha = 255;
    /** Plan 1E: the theme drawn, and (raster themes) the package context its raster is loaded from. */
    private String theme = RoseTheme.DEFAULT;
    private Context resources;
    /** Redraws the surface this field is drawn on once the raster is ready (the strip's scrim). */
    private Runnable onRaster;
    private final Paint art = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG | Paint.DITHER_FLAG);
    private final RectF artBounds = new RectF();

    RoseOxbloodField() {
        shade();
        // SVG feGaussianBlur stdDeviation 3 as a blur mask; Skia converts a blur radius r to sigma = 0.57735 r + 0.5.
        soft = new BlurMaskFilter((BLUR_STD_DEV - 0.5f) / 0.57735f, BlurMaskFilter.Blur.NORMAL);
        edge.moveTo(206, 642);
        edge.lineTo(295, 580);
        edge.lineTo(390, 515);
        lower.moveTo(206, 642);
        lower.lineTo(123, 844);
        sheetLine.moveTo(390, 292);
        sheetLine.lineTo(295, 580);
        line.setStyle(Paint.Style.STROKE);
    }

    /** The geometry's shaders in the current theme (identity in Oxblood: exactly the SVG's colours). */
    private void shade() {
        base = new LinearGradient(0, 0, 0, VIEW_H, RoseTheme.map(theme, BASE), BASE_STOPS, Shader.TileMode.CLAMP);
        // objectBoundingBox gradients: (1,0)->(0,1) and (0,0)->(1,1) in unit space, mapped onto each polygon's box.
        sheetShader = new LinearGradient(1, 0, 0, 1, RoseTheme.map(theme, SHEET_COLORS), null, Shader.TileMode.CLAMP);
        sheetShader.setLocalMatrix(box(SHEET));
        planeShader = new LinearGradient(0, 0, 1, 1, RoseTheme.map(theme, PLANE_COLORS), PLANE_STOPS, Shader.TileMode.CLAMP);
        planeShader.setLocalMatrix(box(PLANE));
        glint = new RadialGradient(GLINT_X, GLINT_Y, GLINT_R, RoseTheme.map(theme, GLINT), null, Shader.TileMode.CLAMP);
    }

    /**
     * Plan 1E: draws in this theme from now on (normalized; unknown is Oxblood). A change drops the cached shaders and
     * raster and invalidates; the same theme changes nothing. The context is kept as its application context only to
     * load the theme's raster.
     */
    void setTheme(Context context, String id) {
        String next = RoseTheme.normalize(id);
        if (context != null) {
            resources = context.getApplicationContext() != null ? context.getApplicationContext() : context;
        }
        if (next.equals(theme)) return;
        theme = next;
        shade();
        invalidateSelf();
    }

    String theme() { return theme; }

    /** Called on the main thread when the raster becomes ready, so the owner redraws its surface. */
    void setOnRaster(Runnable redraw) { onRaster = redraw; }

    /**
     * The theme's raster wallpaper, or null to draw the geometry (Oxblood, no context, not decoded yet or unloadable).
     * The image is decoded once per process off the UI thread (RoseThemeSetting.field), never in draw; until it
     * is ready the geometry draws in the theme's own colours, and onRaster redraws the surface when it arrives.
     */
    private Bitmap raster() {
        if (!RoseTheme.rasterWallpaper(theme) || resources == null) return null;
        return RoseThemeSetting.field(resources, theme, () -> {
            invalidateSelf();
            if (onRaster != null) onRaster.run();
        });
    }

    private static Path polygon(float[] points) {
        Path path = new Path();
        path.moveTo(points[0], points[1]);
        for (int i = 2; i < points.length; i += 2) path.lineTo(points[i], points[i + 1]);
        path.close();
        return path;
    }

    /** Maps the unit square onto the bounding box of the given polygon. */
    static Matrix box(float[] points) {
        RectF r = bounds(points);
        Matrix m = new Matrix();
        m.setScale(r.width(), r.height());
        m.postTranslate(r.left, r.top);
        return m;
    }

    static RectF bounds(float[] points) {
        RectF r = new RectF(points[0], points[1], points[0], points[1]);
        for (int i = 2; i < points.length; i += 2) r.union(points[i], points[i + 1]);
        return r;
    }

    /** The contract's placement: scale = max(W/390, H/844), centred across, top-aligned. */
    static float scale(int width, int height) { return Math.max(width / VIEW_W, height / VIEW_H); }

    static float offsetX(int width, float scale) { return (width - VIEW_W * scale) / 2f; }

    @Override
    public void draw(Canvas canvas) {
        Rect b = getBounds();
        if (b.isEmpty() || alpha == 0) return;
        int save = canvas.save();
        canvas.clipRect(b);
        Bitmap image = raster();
        if (image != null) {
            // The raster theme: its field colour, then the image scaled to cover, centred across, top-aligned or
            // centred (RoseTheme.wallpaperCentred), then the theme's Rose scrim (this is the switcher's Rose field).
            art.setShader(null);
            art.setColor(RoseTheme.Field.color(theme, RoseTheme.Field.BACKGROUND));
            art.setAlpha(Math.round(Color.alpha(art.getColor()) * alpha / 255f));
            canvas.drawRect(b, art);
            float s = Math.max(b.width() / (float) image.getWidth(), b.height() / (float) image.getHeight());
            float w = image.getWidth() * s, h = image.getHeight() * s, left = b.left + (b.width() - w) / 2f;
            float top = RoseTheme.wallpaperCentred(theme) ? b.top + (b.height() - h) / 2f : b.top;
            artBounds.set(left, top, left + w, top + h);
            art.setColor(Color.BLACK);
            art.setAlpha(alpha);
            canvas.drawBitmap(image, null, artBounds, art);
            int scrim = RoseTheme.roseScrim(theme);
            if (scrim != 0) {
                art.setColor(scrim);
                art.setAlpha(Math.round(Color.alpha(scrim) * alpha / 255f));
                canvas.drawRect(b, art);
            }
            canvas.restoreToCount(save);
            return;
        }
        float s = scale(b.width(), b.height());
        canvas.translate(b.left + offsetX(b.width(), s), b.top);
        canvas.scale(s, s);
        paint.setShader(base);
        paint.setAlpha(alpha);
        canvas.drawRect(0, 0, VIEW_W, VIEW_H, paint);
        paint.setShader(sheetShader);
        paint.setAlpha(alpha);
        canvas.drawPath(sheet, paint);
        stroke(canvas, sheetLine, SHEET_LINE, 1f, null);
        paint.setShader(planeShader);
        paint.setAlpha(alpha);
        canvas.drawPath(plane, paint);
        stroke(canvas, edge, EDGE_GLOW, 4f, soft);
        stroke(canvas, edge, EDGE, 1.2f, null);
        stroke(canvas, lower, LOWER_LINE, 1f, null);
        paint.setShader(glint);
        paint.setAlpha(alpha);
        canvas.drawCircle(GLINT_X, GLINT_Y, GLINT_R, paint);
        paint.setShader(null);
        canvas.restoreToCount(save);
    }

    private final Paint layer = new Paint();

    /**
     * Draws the field as the scrim's own fill at the scrim's current opacity (0-255), on a canvas where the scrim has
     * just painted its colour. The artwork is composited once, as one image, and replaces that colour fill (SRC), so its
     * coverage of what is behind the window is exactly the scrim's own, mid-transition included. Nothing at 0; at 255
     * it is drawn directly; between, an offscreen layer exists only for that frame. The scrim is the first thing drawn
     * in its window, so SRC replaces only its own fill.
     */
    void drawAsScrim(Canvas canvas, int width, int height, int scrimAlpha) {
        if (scrimAlpha <= 0 || width <= 0 || height <= 0) return;
        setBounds(0, 0, width, height);
        setAlpha(255);
        if (scrimAlpha >= 255) {
            draw(canvas);
            return;
        }
        layer.setAlpha(scrimAlpha);
        layer.setXfermode(new PorterDuffXfermode(PorterDuff.Mode.SRC));
        int save = canvas.saveLayer(0, 0, width, height, layer);
        draw(canvas);
        canvas.restoreToCount(save);
    }

    private void stroke(Canvas canvas, Path path, int color, float width, BlurMaskFilter filter) {
        line.setColor(RoseTheme.map(theme, color));
        line.setAlpha(Math.round((color >>> 24) * alpha / 255f));
        line.setStrokeWidth(width);
        line.setMaskFilter(filter);
        canvas.drawPath(path, line);
    }

    @Override public void setAlpha(int alpha) {
        int clamped = Math.max(0, Math.min(255, alpha));
        if (clamped != this.alpha) {
            this.alpha = clamped;
            invalidateSelf();
        }
    }

    @Override public int getAlpha() { return alpha; }
    @Override public void setColorFilter(ColorFilter colorFilter) {
        paint.setColorFilter(colorFilter);
        art.setColorFilter(colorFilter);
    }
    @Override public int getOpacity() { return alpha == 255 ? PixelFormat.OPAQUE : PixelFormat.TRANSLUCENT; }
}
