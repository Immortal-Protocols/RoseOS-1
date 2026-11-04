package com.android.wm.shell.transition; import android.annotation.NonNull; import android.annotation.Nullable; import android.os.IBinder; import android.view.SurfaceControl; import android.window.TransitionInfo; import android.window.TransitionRequestInfo; import android.window.WindowContainerTransaction;
public class Transitions { public IBinder startTransition(int type, @NonNull WindowContainerTransaction wct, @Nullable TransitionHandler handler) { return null; } public void addHandler(@NonNull TransitionHandler handler) {}
 public interface TransitionFinishCallback { void onTransitionFinished(@Nullable WindowContainerTransaction wct); }
 public static final class RequestResult { public RequestResult(WindowContainerTransaction wct, TransitionHandler h) {} }
 public interface TransitionHandler { boolean startAnimation(@NonNull IBinder transition, @NonNull TransitionInfo info, @NonNull SurfaceControl.Transaction startTransaction, @NonNull SurfaceControl.Transaction finishTransaction, @NonNull TransitionFinishCallback finishCallback);
  @Nullable @Deprecated default WindowContainerTransaction handleRequest(@NonNull IBinder transition, @NonNull TransitionRequestInfo request) { throw new UnsupportedOperationException(); }
  @Nullable default RequestResult handleRequestOnly(@NonNull IBinder transition, @NonNull TransitionRequestInfo request) { return null; }
  default void onTransitionConsumed(@NonNull IBinder transition, boolean aborted, @Nullable SurfaceControl.Transaction finishTransaction) {}
  default void setAnimScaleSetting(float scale) {} } }
