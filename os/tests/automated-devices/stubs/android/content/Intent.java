package android.content;
public class Intent {
  public static final String EXTRA_PACKAGE_NAME = "p", EXTRA_USER_ID = "u", EXTRA_RESULT_RECEIVER = "r";
  public static final int FLAG_ACTIVITY_NEW_TASK = 1, FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS = 2;
  public Intent setComponent(ComponentName c) { return this; }
  public Intent putExtra(String k, Object v) { return this; }
  public Intent putExtra(String k, int v) { return this; }
  public Intent setFlags(int f) { return this; }
  public String getStringExtra(String k) { return null; }
  public int getIntExtra(String k, int d) { return d; }
}
