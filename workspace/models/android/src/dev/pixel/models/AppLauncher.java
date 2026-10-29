package dev.pixel.models;

/** Launches one allowlisted app by opaque app_id. Implementations never accept package strings. */
public interface AppLauncher {
    LaunchResult launch(String appId);
}
