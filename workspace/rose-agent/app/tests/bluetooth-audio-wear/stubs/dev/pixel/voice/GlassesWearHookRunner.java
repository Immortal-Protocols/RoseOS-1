package dev.pixel.voice;

/** Test boundary only. Uses the real decision class; records decisions instead of launching anything. */
final class GlassesWearHookRunner {
    static GlassesWearHook hook;
    static CustomizationConfig config;
    static GlassesWearHook.Decision lastFire;
    static long now;
    static void reset() {
        hook=new GlassesWearHook();
        config=CustomizationConfig.DEFAULTS.apply(CustomizationConfig.GLASSES_HOOK,
            java.util.Collections.<String,Object>singletonMap("enabled",true));
        lastFire=null;
        now=0;
    }
    static void onWearSignal(android.content.Context context, GlassesWearHook.Source source, int state) {
        GlassesWearHook.Decision d=hook.observe(source,state,now,config);
        if (d.fires()) lastFire=d;
    }
}
