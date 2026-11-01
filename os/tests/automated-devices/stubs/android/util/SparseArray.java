package android.util;
import java.util.ArrayList;
import java.util.TreeMap;
public class SparseArray<E> {
  private final TreeMap<Integer, E> map = new TreeMap<>();
  public SparseArray() {} public SparseArray(int capacity) {}
  public void put(int k, E v) { map.put(k, v); }
  public E get(int k) { return map.get(k); }
  public E get(int k, E d) { return map.containsKey(k) ? map.get(k) : d; }
  public void remove(int k) { map.remove(k); }
  public boolean contains(int k) { return map.containsKey(k); }
  public int size() { return map.size(); }
  public int keyAt(int i) { return new ArrayList<>(map.keySet()).get(i); }
  public E valueAt(int i) { return new ArrayList<>(map.values()).get(i); }
}
