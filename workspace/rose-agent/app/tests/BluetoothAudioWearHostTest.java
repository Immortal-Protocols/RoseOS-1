package dev.pixel.voice;

import android.content.Context;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import java.lang.reflect.Field;
import java.util.LinkedHashMap;
import java.util.Map;

/** Synthetic callback scheduling, not Android/device acceptance. Uses the production signal adapter and decision classes. */
public final class BluetoothAudioWearHostTest {
    static int checks;
    static void expect(boolean value,String message) {
        checks++;
        if (!value) throw new AssertionError(message);
    }
    static void reset() throws Exception {
        Field started=BluetoothAudioWear.class.getDeclaredField("started");
        started.setAccessible(true);
        started.setBoolean(null,false);
        GlassesWearHookRunner.reset();
    }
    static void devices(AudioManager audio,int... types) {
        audio.outputs=new AudioDeviceInfo[types.length];
        for (int i=0;i<types.length;i++) audio.outputs[i]=new AudioDeviceInfo(types[i]);
    }
    static void config(Object... kv) {
        Map<String,Object> params=new LinkedHashMap<>();
        for (int i=0;i<kv.length;i+=2) params.put((String)kv[i],kv[i+1]);
        GlassesWearHookRunner.config=GlassesWearHookRunner.config.apply(CustomizationConfig.GLASSES_HOOK,params);
    }
    static void at(long now) { GlassesWearHookRunner.now=now; }
    static long fires() { return GlassesWearHookRunner.hook.fires(); }

    static void existingContracts() throws Exception {
        for (int type:new int[]{7,8,23,26,27,30})
            expect(BluetoothAudioWear.bluetooth(type),"supported Bluetooth output type "+type);
        for (int type:new int[]{0,1,2,3,4,9,11,22,25})
            expect(!BluetoothAudioWear.bluetooth(type),"non-Bluetooth output type "+type);

        reset();
        BluetoothAudioWear.start(new Context(null));
        AudioManager audio=new AudioManager();
        Context context=new Context(audio);
        BluetoothAudioWear.start(context);
        BluetoothAudioWear.start(context);
        expect(audio.registrations==1,"no-manager start can retry, then registers once");
        audio.dispatchInitial();
        expect(fires()==0,"disconnected startup is only a baseline");

        at(5_000); devices(audio,8); audio.dispatchAdded();
        expect(fires()==1,"normal connect fires");
        expect("tray".equals(GlassesWearHookRunner.lastFire.action),"tray action preserved");
        at(6_000); devices(audio,8,7); audio.dispatchAdded();
        expect(fires()==1,"second audio device is not another aggregate transition");
        at(7_000); devices(audio,7); audio.dispatchRemoved();
        expect(fires()==1 && GlassesWearHookRunner.hook.state()==GlassesWearHook.WORN,
            "one remaining Bluetooth output stays connected");
        at(8_000); devices(audio); audio.dispatchRemoved();
        at(9_000); devices(audio,8); audio.dispatchAdded();
        expect(fires()==1,"short reconnect blip suppressed");
        config("enabled",false);
        at(10_000); devices(audio); audio.dispatchRemoved();
        at(14_000); devices(audio,8); audio.dispatchAdded();
        expect(fires()==1,"disabled hook tracks connection without firing");
        config("enabled",true,"action","app","packageName","dev.pixel.notes");
        at(15_000); audio.dispatchAdded();
        expect(fires()==1,"enabling while connected does not fire");
        at(16_000); devices(audio); audio.dispatchRemoved();
        at(20_000); devices(audio,8); audio.dispatchAdded();
        expect(fires()==2 && "app".equals(GlassesWearHookRunner.lastFire.action)
            && "dev.pixel.notes".equals(GlassesWearHookRunner.lastFire.packageName),
            "next qualifying transition uses changed action and package");

        reset(); audio=new AudioManager(); devices(audio,8);
        BluetoothAudioWear.start(new Context(audio));
        at(6_000); audio.dispatchInitial(); audio.dispatchAdded();
        expect(fires()==0,"already connected at startup never fires");
    }

    static void delayedInitialCallback() throws Exception {
        reset();
        AudioManager audio=new AudioManager();
        devices(audio,AudioDeviceInfo.TYPE_BUILTIN_SPEAKER);
        BluetoothAudioWear.start(new Context(audio));
        // The process began disconnected. Its main thread is busy through t=6 s.
        // A headset connects at t=5 s, after the required 3 s disconnected interval.
        // The original initial callback still contains only the built-in speaker.
        at(5_000); devices(audio,AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,AudioDeviceInfo.TYPE_BLUETOOTH_A2DP);
        at(6_000); audio.dispatchInitial(); audio.dispatchAdded();
        expect(fires()==1,"connection after startup but before initial callback must fire exactly once");
        expect("tray".equals(GlassesWearHookRunner.lastFire.action),"delayed callback retains configured action");
    }

    public static void main(String[] args) throws Exception {
        existingContracts();
        System.out.println("BluetoothAudioWearHostTest existing contracts: "+checks+" checks passed");
        delayedInitialCallback();
        System.out.println("BluetoothAudioWearHostTest: "+checks+" checks passed");
    }
}
