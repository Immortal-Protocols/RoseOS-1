package dev.pixel.models;

/** Truthful outcome of one open_app request. launch_requested is dispatch, not observed rendering. */
public final class LaunchResult {
    public enum Status {
        LAUNCH_REQUESTED("launch_requested"),
        NOT_FOUND("not_found"),
        LOCKED("locked"),
        NOT_ALLOWED("not_allowed"),
        CANCELLED("cancelled"),
        FAILED("failed");

        public final String wire;

        Status(String wire) {
            this.wire = wire;
        }
    }

    public final String appId;
    public final Status status;
    /** Short non-secret detail, e.g. the resolved label or the platform exception class. */
    public final String detail;

    public LaunchResult(String appId, Status status, String detail) {
        this.appId = appId;
        this.status = status;
        this.detail = detail == null ? "" : detail;
    }

    @Override
    public String toString() {
        return "open_app " + appId + ": " + status.wire + (detail.isEmpty() ? "" : " (" + detail + ")");
    }
}
