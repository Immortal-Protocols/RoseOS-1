package android.media;

public final class AudioManager {
    public static final int GET_DEVICES_OUTPUTS=2;
    public AudioDeviceInfo[] outputs=new AudioDeviceInfo[0], initial;
    public AudioDeviceCallback callback;
    public int registrations;
    public android.os.Handler handler;
    public AudioDeviceInfo[] getDevices(int flags) {
        if (flags != GET_DEVICES_OUTPUTS) throw new AssertionError("must query outputs only");
        return outputs.clone();
    }
    public void registerAudioDeviceCallback(AudioDeviceCallback callback, android.os.Handler handler) {
        if (handler == null) throw new AssertionError("must choose callback handler");
        registrations++;
        this.callback=callback;
        this.handler=handler;
        initial=outputs.clone();
        // Android posts the initial device callback, rather than running it inline.
    }
    public void queueInitial() { handler.post(() -> callback.onAudioDevicesAdded(initial)); }
    public void queueAdded() {
        AudioDeviceInfo[] added=outputs.clone();
        handler.post(() -> callback.onAudioDevicesAdded(added));
    }
    public void queueRemoved() { handler.post(() -> callback.onAudioDevicesRemoved(new AudioDeviceInfo[0])); }
    public void dispatchInitial() { callback.onAudioDevicesAdded(initial); }
    public void dispatchAdded() { callback.onAudioDevicesAdded(outputs.clone()); }
    public void dispatchRemoved() { callback.onAudioDevicesRemoved(new AudioDeviceInfo[0]); }
}
