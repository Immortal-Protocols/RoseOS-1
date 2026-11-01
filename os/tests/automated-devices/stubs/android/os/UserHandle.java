package android.os;
public final class UserHandle {
  public static final int USER_NULL = -10000, PER_USER_RANGE = 100000;
  private final int id; private UserHandle(int id) { this.id = id; }
  public static UserHandle of(int id) { return new UserHandle(id); }
  public static int getUserId(int uid) { return uid / PER_USER_RANGE; }
  public int getIdentifier() { return id; }
}
