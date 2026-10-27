package dev.pixel.voice;

import android.content.Intent;
import android.os.Bundle;

/** The existing app picker, in a regular resizable task beside the agent. */
public final class WorkspaceAppsActivity extends MainActivity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        apps();
    }
    @Override public void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        apps();
    }
}
