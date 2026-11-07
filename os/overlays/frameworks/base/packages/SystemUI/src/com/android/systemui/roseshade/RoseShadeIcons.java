/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.systemui.roseshade;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.drawable.Drawable;

/**
 * The Rose shade's line icons (1G), drawn in code so the shade needs no new resources: Wi-Fi, Bluetooth,
 * flashlight, do not disturb, brightness, settings, chevrons, a person and a message bubble. There is no
 * microphone icon here: the Rose chin draws the only one.
 */
final class RoseShadeIcons extends Drawable {
    enum Glyph { WIFI, BLUETOOTH, FLASHLIGHT, MOON, SUN, GEAR, CHEVRON_DOWN, CHEVRON_UP, PERSON, BUBBLE }

    private final Glyph mGlyph;
    private final Paint mPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path mPath = new Path();
    private final RectF mOval = new RectF();

    RoseShadeIcons(Glyph glyph, int color, float strokePx) {
        mGlyph = glyph;
        mPaint.setColor(color);
        mPaint.setStyle(Paint.Style.STROKE);
        mPaint.setStrokeWidth(strokePx);
        mPaint.setStrokeCap(Paint.Cap.ROUND);
        mPaint.setStrokeJoin(Paint.Join.ROUND);
    }

    @Override
    public void draw(Canvas canvas) {
        final Rect b = getBounds();
        final float s = Math.min(b.width(), b.height());
        final float cx = b.exactCenterX();
        final float cy = b.exactCenterY();
        final float u = s / 24f;  // a 24-unit grid, like the board's icons
        mPath.reset();
        switch (mGlyph) {
            case WIFI:
                for (int i = 1; i <= 3; i++) {
                    final float r = 3.2f * u * i;
                    mOval.set(cx - r, cy + 6 * u - r, cx + r, cy + 6 * u + r);
                    canvas.drawArc(mOval, 225, 90, false, mPaint);
                }
                canvas.drawPoint(cx, cy + 6 * u, mPaint);
                break;
            case BLUETOOTH:
                mPath.moveTo(cx - 5 * u, cy - 5 * u);
                mPath.lineTo(cx + 5 * u, cy + 5 * u);
                mPath.lineTo(cx, cy + 10 * u);
                mPath.lineTo(cx, cy - 10 * u);
                mPath.lineTo(cx + 5 * u, cy - 5 * u);
                mPath.lineTo(cx - 5 * u, cy + 5 * u);
                canvas.drawPath(mPath, mPaint);
                break;
            case FLASHLIGHT:
                mPath.moveTo(cx - 6 * u, cy - 9 * u);
                mPath.lineTo(cx + 6 * u, cy - 9 * u);
                mPath.lineTo(cx + 6 * u, cy - 5 * u);
                mPath.lineTo(cx + 3 * u, cy - 1 * u);
                mPath.lineTo(cx + 3 * u, cy + 9 * u);
                mPath.lineTo(cx - 3 * u, cy + 9 * u);
                mPath.lineTo(cx - 3 * u, cy - 1 * u);
                mPath.lineTo(cx - 6 * u, cy - 5 * u);
                mPath.close();
                canvas.drawPath(mPath, mPaint);
                canvas.drawLine(cx, cy + 2 * u, cx, cy + 5 * u, mPaint);
                break;
            case MOON:
                mOval.set(cx - 8 * u, cy - 8 * u, cx + 8 * u, cy + 8 * u);
                canvas.drawArc(mOval, 60, 250, false, mPaint);
                mOval.set(cx - 2 * u, cy - 9 * u, cx + 10 * u, cy + 3 * u);
                canvas.drawArc(mOval, 115, 125, false, mPaint);
                break;
            case SUN:
                canvas.drawCircle(cx, cy, 4 * u, mPaint);
                for (int i = 0; i < 8; i++) {
                    final double a = Math.PI / 4 * i;
                    canvas.drawLine(cx + (float) Math.cos(a) * 7 * u, cy + (float) Math.sin(a) * 7 * u,
                            cx + (float) Math.cos(a) * 9.5f * u, cy + (float) Math.sin(a) * 9.5f * u, mPaint);
                }
                break;
            case GEAR:
                canvas.drawCircle(cx, cy, 3.5f * u, mPaint);
                canvas.drawCircle(cx, cy, 7 * u, mPaint);
                for (int i = 0; i < 8; i++) {
                    final double a = Math.PI / 4 * i;
                    canvas.drawLine(cx + (float) Math.cos(a) * 7 * u, cy + (float) Math.sin(a) * 7 * u,
                            cx + (float) Math.cos(a) * 9.5f * u, cy + (float) Math.sin(a) * 9.5f * u, mPaint);
                }
                break;
            case CHEVRON_DOWN:
                mPath.moveTo(cx - 6 * u, cy - 3 * u);
                mPath.lineTo(cx, cy + 3 * u);
                mPath.lineTo(cx + 6 * u, cy - 3 * u);
                canvas.drawPath(mPath, mPaint);
                break;
            case CHEVRON_UP:
                mPath.moveTo(cx - 6 * u, cy + 3 * u);
                mPath.lineTo(cx, cy - 3 * u);
                mPath.lineTo(cx + 6 * u, cy + 3 * u);
                canvas.drawPath(mPath, mPaint);
                break;
            case PERSON:
                canvas.drawCircle(cx, cy - 3 * u, 4 * u, mPaint);
                mOval.set(cx - 8 * u, cy + 3 * u, cx + 8 * u, cy + 15 * u);
                canvas.drawArc(mOval, 180, 180, false, mPaint);
                break;
            case BUBBLE:
                mOval.set(cx - 9 * u, cy - 7 * u, cx + 9 * u, cy + 7 * u);
                canvas.drawRoundRect(mOval, 7 * u, 7 * u, mPaint);
                canvas.drawLine(cx - 5 * u, cy + 6 * u, cx - 8 * u, cy + 10 * u, mPaint);
                break;
        }
    }

    @Override
    public void setAlpha(int alpha) {
        mPaint.setAlpha(alpha);
        invalidateSelf();
    }

    @Override
    public void setColorFilter(ColorFilter colorFilter) {
        mPaint.setColorFilter(colorFilter);
        invalidateSelf();
    }

    @Override
    public int getOpacity() {
        return PixelFormat.TRANSLUCENT;
    }
}
