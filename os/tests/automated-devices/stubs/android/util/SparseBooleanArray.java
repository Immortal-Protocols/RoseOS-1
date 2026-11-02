package android.util;
import java.util.HashMap;
public class SparseBooleanArray {
  private final HashMap<Integer, Boolean> map = new HashMap<>();
  public boolean get(int k) { return map.getOrDefault(k, false); }
  public void put(int k, boolean v) { map.put(k, v); }
}
