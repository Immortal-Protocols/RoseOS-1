package android.os;
import java.util.ArrayDeque;
/** Test stub: values written in order and read back in the same order. */
public final class Parcel {
  private final ArrayDeque<Object> values = new ArrayDeque<>();
  public static Parcel obtain() { return new Parcel(); }
  public void setDataPosition(int p) {}
  public void recycle() {}
  public void writeLong(long v) { values.add(v); }
  public void writeInt(int v) { values.add(v); }
  public void writeBoolean(boolean v) { values.add(v); }
  public void writeStringArray(String[] v) { values.add(v == null ? null : v.clone()); }
  public long readLong() { return (Long) values.poll(); }
  public int readInt() { return (Integer) values.poll(); }
  public boolean readBoolean() { return (Boolean) values.poll(); }
  public String[] createStringArray() { return (String[]) values.poll(); }
}
