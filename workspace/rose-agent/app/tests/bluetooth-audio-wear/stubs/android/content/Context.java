package android.content;

public class Context {
    private final android.media.AudioManager audio;
    public Context(android.media.AudioManager audio) { this.audio = audio; }
    public Context getApplicationContext() { return this; }
    public <T> T getSystemService(Class<T> type) { return type.cast(audio); }
}
