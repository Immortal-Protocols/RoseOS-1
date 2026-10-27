package dev.pixel.backgroundcontrol;

import android.content.Context;
import android.content.ContextWrapper;
import android.graphics.Rect;
import android.view.View;
import android.view.WindowManager;
import android.view.WindowMetrics;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Proxy;

/** Adapts only the native mirror's geometry; the Activity and controller are unchanged. */
final class MirrorWindowContext extends ContextWrapper {
    private View mirror;
    private final WindowManager localWindowManager;

    MirrorWindowContext(Context context) {
        super(context);
        final WindowManager real = context.getSystemService(WindowManager.class);
        localWindowManager = (WindowManager) Proxy.newProxyInstance(
            WindowManager.class.getClassLoader(), new Class<?>[]{WindowManager.class},
            (proxy, method, args) -> {
                try {
                    Object value = method.invoke(real, args);
                    if (!method.getName().equals("getCurrentWindowMetrics")) return value;
                    WindowMetrics metrics = (WindowMetrics) value;
                    Rect physical = metrics.getBounds();
                    View root = mirror == null ? null : mirror.getRootView();
                    int[] frame = MirrorWindowFrame.bounds(physical.width(), physical.height(),
                        root == null ? 0 : root.getWidth(), root == null ? 0 : root.getHeight());
                    // MirrorView compares these bounds with getBoundsInWindow(), which is local.
                    // Keep the old layout's extent until resize completes, avoiding negative insets.
                    return new WindowMetrics(new Rect(frame[0], frame[1], frame[2], frame[3]),
                        metrics.getWindowInsets(), metrics.getDensity());
                } catch (InvocationTargetException e) { throw e.getCause(); }
            });
    }

    void setMirror(View view) { mirror = view; }

    @Override public Object getSystemService(String name) {
        return Context.WINDOW_SERVICE.equals(name) ? localWindowManager : super.getSystemService(name);
    }
}
