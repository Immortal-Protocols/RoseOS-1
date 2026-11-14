package android.media;

public final class AudioDeviceInfo {
    public static final int TYPE_BUILTIN_SPEAKER=2, TYPE_BLUETOOTH_SCO=7,
        TYPE_BLUETOOTH_A2DP=8, TYPE_HEARING_AID=23, TYPE_BLE_HEADSET=26,
        TYPE_BLE_SPEAKER=27, TYPE_BLE_BROADCAST=30;
    private final int type;
    public AudioDeviceInfo(int type) { this.type=type; }
    public int getType() { return type; }
}
