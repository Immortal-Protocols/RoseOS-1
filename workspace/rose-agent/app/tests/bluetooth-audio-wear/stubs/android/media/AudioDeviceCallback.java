package android.media;

public abstract class AudioDeviceCallback {
    public void onAudioDevicesAdded(AudioDeviceInfo[] added) {}
    public void onAudioDevicesRemoved(AudioDeviceInfo[] removed) {}
}
