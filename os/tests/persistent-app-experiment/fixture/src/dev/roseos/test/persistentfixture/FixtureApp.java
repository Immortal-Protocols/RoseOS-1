package dev.roseos.test.persistentfixture;

import android.app.Application;
import android.os.Process;
import android.util.Log;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

/** Synthetic persistent fixture: logs its compiled version, PID and whether its data sentinel already existed. */
public final class FixtureApp extends Application {
    static final String TAG = "PersistFixture";

    @Override
    public void onCreate() {
        super.onCreate();
        File sentinel = new File(getFilesDir(), "sentinel");
        String state;
        try {
            if (sentinel.exists()) {
                byte[] bytes = new byte[64];
                int n;
                try (FileInputStream in = new FileInputStream(sentinel)) {
                    n = in.read(bytes);
                }
                state = "existing:" + new String(bytes, 0, Math.max(n, 0), StandardCharsets.US_ASCII).trim();
            } else {
                try (FileOutputStream out = new FileOutputStream(sentinel)) {
                    out.write(("created-by-v" + FixtureVersion.CODE).getBytes(StandardCharsets.US_ASCII));
                }
                state = "created";
            }
        } catch (Exception e) {
            state = "error:" + e.getClass().getSimpleName();
        }
        Log.i(TAG, "marker version=" + FixtureVersion.CODE + " pid=" + Process.myPid() + " sentinel=" + state);
    }
}
