package android.util;
import java.util.ArrayList;
import java.util.LinkedHashMap;
public class ArrayMap<K, V> extends LinkedHashMap<K, V> {
  public K keyAt(int i) { return new ArrayList<>(keySet()).get(i); }
  public V valueAt(int i) { return new ArrayList<>(values()).get(i); }
}
