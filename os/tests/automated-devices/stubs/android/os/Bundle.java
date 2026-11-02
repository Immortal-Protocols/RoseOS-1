package android.os;
import java.util.HashMap;
import java.util.Map;
/** Test stub: a typed map, as the real Bundle is for these value types. */
public class Bundle {
  private final Map<String, Object> values = new HashMap<>();
  public String getString(String k) { return (String) values.get(k); }
  public void putLong(String k, long v) { values.put(k, v); }
  public void putIntArray(String k, int[] v) { values.put(k, v); }
  public void putBooleanArray(String k, boolean[] v) { values.put(k, v); }
  public void putStringArray(String k, String[] v) { values.put(k, v); }
  public long getLong(String k) { return (Long) values.get(k); }
  public int[] getIntArray(String k) { return (int[]) values.get(k); }
  public boolean[] getBooleanArray(String k) { return (boolean[]) values.get(k); }
  public String[] getStringArray(String k) { return (String[]) values.get(k); }
  public java.util.Set<String> keySet() { return values.keySet(); }
}
