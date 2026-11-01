package android.util;
import java.util.ArrayList;
import java.util.LinkedHashSet;
public class ArraySet<E> extends LinkedHashSet<E> {
  public ArraySet() {}
  public E valueAt(int i) { return new ArrayList<>(this).get(i); }
}
