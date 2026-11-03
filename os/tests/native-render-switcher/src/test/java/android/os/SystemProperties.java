package android.os;

/**
 * Compile-time only: android.jar omits this hidden class (frameworks/base 84183aff core/java/android/os/
 * SystemProperties.java). At run time Robolectric's sandbox loads the real class from its SDK 35 runtime, which
 * RoseEdgeSwipeNativeTest checks by setting a property and reading it back.
 */
public class SystemProperties {
    public static boolean getBoolean(String key, boolean def) { throw new UnsupportedOperationException("stub"); }

    public static String get(String key) { throw new UnsupportedOperationException("stub"); }

    public static void set(String key, String value) { throw new UnsupportedOperationException("stub"); }
}
