package android.os;
public interface Parcelable {
  int describeContents();
  void writeToParcel(Parcel dest, int flags);
  interface Creator<T> { T createFromParcel(Parcel in); T[] newArray(int size); }
}
