package android.os;
import java.util.ArrayDeque;
/** Test stub: a FIFO queue drained explicitly, like a Looper's message queue. */
public class Handler {
  public final ArrayDeque<Runnable> queue = new ArrayDeque<>();
  public boolean post(Runnable r) { queue.add(r); return true; }
  public void drain() { while (!queue.isEmpty()) queue.poll().run(); }
}
