package android.util;
import java.util.Objects;
public class Pair<F, S> {
  public final F first; public final S second;
  public Pair(F f, S s) { first = f; second = s; }
  public static <A, B> Pair<A, B> create(A a, B b) { return new Pair<>(a, b); }
  @Override public boolean equals(Object o) { return o instanceof Pair && Objects.equals(first, ((Pair<?, ?>) o).first) && Objects.equals(second, ((Pair<?, ?>) o).second); }
  @Override public int hashCode() { return Objects.hash(first, second); }
}
