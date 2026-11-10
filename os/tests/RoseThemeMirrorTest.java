package com.android.wm.shell;

import java.util.ArrayList;
import java.util.List;

/** Plan 1E: SystemUI follows Rose Agent's theme, mirrors it for Launcher3 and the keyboard, and acknowledges truthfully. */
public final class RoseThemeMirrorTest {
    private static int checks, failures;

    private static void check(boolean ok, String what) {
        checks++;
        if (!ok) {
            failures++;
            System.out.println("FAIL " + what);
        }
    }

    static final class FakePort implements RoseThemeMirror.Port {
        String setting;
        boolean writable = true;
        final List<String> writes = new ArrayList<>(), acks = new ArrayList<>();

        @Override public String read() { return setting; }

        @Override public boolean write(String id) {
            if (!writable) return false;
            writes.add(id);
            setting = id;
            return true;
        }

        @Override public void acknowledge(String id) { acks.add(id); }
    }

    public static void main(String[] args) {
        final int[] shown = {0};
        Runnable listener = () -> shown[0]++;
        RoseThemeMirror.listen(listener);

        FakePort port = new FakePort();
        check(RoseThemeMirror.current().equals(RoseTheme.DEFAULT), "before start SystemUI draws the default");
        RoseThemeMirror mirror = new RoseThemeMirror(port);
        port.setting = "glacier";
        mirror.start();
        check(RoseThemeMirror.current().equals("glacier"), "start adopts the persisted theme before Rose answers");
        check(shown[0] == 0, "start draws the persisted theme without a change notification");

        // A Rose without a theme key changes nothing and says nothing.
        mirror.fromRose(null);
        check(port.acks.isEmpty() && port.writes.isEmpty(), "no theme key: nothing changes, nothing is acknowledged");

        // Rose asks for Oxblood: written, shown, acknowledged once.
        mirror.fromRose("oxblood");
        check(port.writes.size() == 1 && port.writes.get(0).equals("oxblood"), "the setting is written for Launcher3 and the keyboard");
        check(RoseThemeMirror.current().equals("oxblood") && shown[0] == 1, "SystemUI's surfaces are told once");
        check(port.acks.size() == 1 && port.acks.get(0).equals("oxblood"), "acknowledged after it is shown");
        mirror.fromRose("oxblood");
        check(port.acks.size() == 1 && port.writes.size() == 1 && shown[0] == 1, "a repeated STATE is not a change and not re-acknowledged");

        // A failed write keeps the current theme and acknowledges what is still shown.
        port.writable = false;
        mirror.fromRose("glacier");
        check(RoseThemeMirror.current().equals("oxblood") && shown[0] == 1, "a failed write shows nothing new");
        check(port.acks.size() == 1, "the shown theme was already acknowledged; no false success");
        port.writable = true;
        mirror.resubscribed();
        mirror.fromRose("glacier");
        check(RoseThemeMirror.current().equals("glacier") && port.acks.get(port.acks.size() - 1).equals("glacier"), "a later try succeeds and is acknowledged");

        // An id this SystemUI does not know (a newer Rose) changes nothing; the shown theme is acknowledged.
        int before = port.writes.size();
        mirror.resubscribed();
        mirror.fromRose("aurora");
        check(port.writes.size() == before && RoseThemeMirror.current().equals("glacier"), "unknown ids are ignored");
        check(port.acks.get(port.acks.size() - 1).equals("glacier"), "Rose learns the theme actually shown");

        // The setting changed outside (adb): SystemUI follows it.
        port.setting = "oxblood";
        mirror.settingChanged();
        check(RoseThemeMirror.current().equals("oxblood"), "SystemUI follows the setting");
        check(port.acks.get(port.acks.size() - 1).equals("oxblood"), "an outside change is acknowledged, so Rose never keeps a stale answer");
        int acks = port.acks.size();
        mirror.settingChanged();
        check(port.acks.size() == acks, "an unchanged setting is not acknowledged again");
        port.setting = "garbage";
        mirror.settingChanged();
        check(RoseThemeMirror.current().equals(RoseTheme.DEFAULT), "a damaged setting draws the default");

        // The setting already holds the value (another SystemUI path wrote it): not written again, still shown.
        port.setting = "glacier";
        int writes = port.writes.size();
        mirror.fromRose("glacier");
        check(port.writes.size() == writes && RoseThemeMirror.current().equals("glacier"), "no redundant write");

        RoseThemeMirror.unlisten(listener);
        int n = shown[0];
        port.setting = "oxblood";
        mirror.settingChanged();
        check(shown[0] == n, "unlisten stops notifications");

        System.out.println("RoseThemeMirrorTest: " + (checks - failures) + "/" + checks + " checks passed");
        if (failures > 0) System.exit(1);
    }
}
