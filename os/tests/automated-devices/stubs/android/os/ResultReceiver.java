package android.os;
public class ResultReceiver {
  public static final Creator CREATOR = new Creator();
  public static final class Creator { public ResultReceiver createFromParcel(Parcel p) { return new ResultReceiver(null); } }
  public ResultReceiver(Handler h) {}
  protected void onReceiveResult(int code, Bundle data) {}
  public void writeToParcel(Parcel p, int f) {}
}
