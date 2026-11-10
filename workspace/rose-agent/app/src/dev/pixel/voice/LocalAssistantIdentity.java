package dev.pixel.voice;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.JSONObject;

/** Last confirmed producer identity. Cached per pairing for offline UI and process restarts. */
final class LocalAssistantIdentity {
    private static final String PREFS = "local-assistant-identity";
    private LocalAssistantIdentity() {}

    static String scope(Context context) {
        if (!LocalRoseLink.enabled(context)) return "";
        LocalRoseConnection.Paired p = LocalRoseConnection.paired(context);
        return p == null ? "" : p.pin + ":" + p.deviceId;
    }

    static String name(Context context) {
        String scope = scope(context);
        SharedPreferences prefs = context.getSharedPreferences(PREFS, 0);
        if (scope.isEmpty() || !scope.equals(prefs.getString("scope", ""))) return AssistantName.DEFAULT;
        String name = AssistantName.valid(prefs.getString("name", ""));
        return name == null ? AssistantName.DEFAULT : name;
    }

    /** Caller rejects retired links. Ignore history, bad data and late events from a replaced pairing. */
    static void accept(Context context, String scope, JSONObject event) {
        if (scope.isEmpty() || !scope.equals(scope(context)) || event.optBoolean("replay")) return;
        Object raw;
        if ("hello".equals(event.optString("kind"))) {
            JSONObject state = event.optJSONObject("state");
            raw = state == null ? null : state.opt("assistant_name");
        } else if ("assistant_name".equals(event.optString("kind"))) {
            raw = event.opt("name");
        } else return;
        String name = AssistantName.valid(raw);
        if (name == null) return; // an older producer can omit the optional field
        context.getSharedPreferences(PREFS, 0).edit().putString("scope", scope).putString("name", name).commit();
    }
}
