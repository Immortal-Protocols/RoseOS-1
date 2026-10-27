package dev.pixel.backgroundcontrol;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RuntimeShader;
import android.os.SystemClock;
import android.widget.FrameLayout;

/** A non-interactive ownership indicator drawn over the live view without shrinking it. */
final class AgentPaneFrame extends FrameLayout {
    private final Paint edge = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RuntimeShader glow = new RuntimeShader(
        "uniform float2 size; uniform float depth; uniform float radius; uniform float phase;"
        + "half4 main(float2 xy) {"
        + " float2 q=abs(xy-size*.5)-(size*.5-float2(radius));"
        + " float d=length(max(q,0.0))+min(max(q.x,q.y),0.0)-radius;"
        + " if(d>0.0) return half4(0.0);"
        + " float u=(xy.x/size.x+xy.y/size.y)*.5;"
        + " float3 deep=float3(1.0,.16,.025);"
        + " float3 color=float3(1.0,.44,.045);"
        + " float3 bright=float3(1.0,.74,.18);"
        + " float drift=.25*sin(phase*.5);"
        + " float3 tint=mix(deep,color,.5+.5*sin(u*12.0+drift));"
        + " tint=mix(tint,bright,.28+.22*sin(u*19.0-drift));"
        + " float a=.28*exp(d/depth)+.10*exp(d/(depth*.24));"
        + " a*=1.0+.10*sin(phase);"
        + " return half4(tint*a,a); }");
    private final float dp;
    private boolean agentActive;
    private boolean taskRunning;
    private final Runnable nextFrame = () -> invalidate();

    AgentPaneFrame(Context context) {
        super(context);
        dp = getResources().getDisplayMetrics().density;
        setBackgroundColor(Color.BLACK);
        // Warm agent palette; gentle breathing, independent of speech activity.
        edge.setShader(glow);
    }

    void setAgentActive(boolean active) {
        if (agentActive != active) {
            agentActive = active;
            if (!active) removeCallbacks(nextFrame);
            invalidate();
        }
    }

    void setTaskRunning(boolean running) {
        if (taskRunning != running) {
            taskRunning = running;
            removeCallbacks(nextFrame);
            invalidate();
        }
    }

    @Override protected void dispatchDraw(Canvas canvas) {
        super.dispatchDraw(canvas);
        if (!agentActive || getWidth() == 0 || getHeight() == 0) return;
        glow.setFloatUniform("size", getWidth(), getHeight());
        float phase = taskRunning
            ? (float) ((SystemClock.uptimeMillis() % 110000L) * 2 * Math.PI / 5500) : 0;
        glow.setFloatUniform("phase", phase);
        glow.setFloatUniform("depth", 14 * dp * (1 + .04f * (float) Math.sin(phase)));
        glow.setFloatUniform("radius", 18 * dp);
        canvas.drawRect(0, 0, getWidth(), getHeight(), edge);
        removeCallbacks(nextFrame);
        if (taskRunning && isAttachedToWindow() && isShown() && getWindowVisibility() == VISIBLE)
            postDelayed(nextFrame, 40);
    }

    @Override public void onVisibilityAggregated(boolean visible) {
        super.onVisibilityAggregated(visible);
        removeCallbacks(nextFrame);
        if (visible && agentActive) invalidate();
    }

    @Override protected void onDetachedFromWindow() {
        removeCallbacks(nextFrame);
        super.onDetachedFromWindow();
    }
}
