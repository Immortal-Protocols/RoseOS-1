package android.os;
import java.util.*;
public class Handler {
 static final Queue<Runnable> queue=new ArrayDeque<>();
 public Handler(Looper l){}
 public void post(Runnable r){queue.add(r);}
 public void postDelayed(Runnable r,long delay){queue.add(()->{SystemClock.time+=delay;r.run();});}
 public void removeCallbacksAndMessages(Object o){queue.clear();}
 public static void drain(){for(int n=0;!queue.isEmpty();n++){if(n>300)throw new AssertionError("Unbounded loop");queue.remove().run();}}
}
