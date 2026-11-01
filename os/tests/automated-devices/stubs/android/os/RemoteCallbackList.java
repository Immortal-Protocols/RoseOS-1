package android.os;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;
/** Test stub: registration order, one cookie per callback, and the begin/finish broadcast protocol. */
public class RemoteCallbackList<E> {
  private final List<E> items = new ArrayList<>();
  private final List<Object> cookies = new ArrayList<>();
  private List<E> active;
  private List<Object> activeCookies;
  public boolean register(E e) { return register(e, null); }
  public boolean register(E e, Object cookie) {
    int i = items.indexOf(e);
    if (i >= 0) { cookies.set(i, cookie); return true; }
    items.add(e); cookies.add(cookie); return true;
  }
  public boolean unregister(E e) { int i = items.indexOf(e); if (i < 0) return false; items.remove(i); cookies.remove(i); return true; }
  public int beginBroadcast() {
    if (active != null) throw new IllegalStateException("beginBroadcast() called while already in a broadcast");
    active = new ArrayList<>(items); activeCookies = new ArrayList<>(cookies); return active.size();
  }
  public E getBroadcastItem(int i) { return active.get(i); }
  public Object getBroadcastCookie(int i) { return activeCookies.get(i); }
  public void finishBroadcast() { active = null; activeCookies = null; }
  public void broadcast(Consumer<E> action) {
    int n = beginBroadcast();
    try { for (int i = 0; i < n; i++) action.accept(getBroadcastItem(i)); } finally { finishBroadcast(); }
  }
}
