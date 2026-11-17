package dev.pixel.builder;

import java.util.Arrays;
import java.util.Collections;

/**
 * When the builder reads its screens again (Refresh): a build that finished or was repaired on the server while the
 * builder sat in the background, or while the library was showing, must show as it is now without the owner having to
 * open a project or tap All apps.
 */
public final class RefreshTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    static BuilderApi.Project project(String id, BuildJob.Stage active) {
        return new BuilderApi.Project(id, id, "app.rose.gen.p" + id, null, Collections.<BuilderApi.Version>emptyList(),
            active == null ? null : new BuildJob("j" + id, active, null, null));
    }

    public static void main(String[] args) {
        // ---- On resume: the library and a project are read again; typing screens are left alone ----
        expect(Refresh.onResume(Refresh.Shown.LIBRARY, false, true) == Refresh.OnResume.LIBRARY, "back to the library: read it again");
        expect(Refresh.onResume(Refresh.Shown.PROJECT, false, true) == Refresh.OnResume.PROJECT, "back to a project: read it again");
        expect(Refresh.onResume(Refresh.Shown.OTHER, false, true) == Refresh.OnResume.NOTHING, "New app, sign-in, pairing: keep what was typed");
        expect(Refresh.onResume(Refresh.Shown.LIBRARY, true, true) == Refresh.OnResume.NOTHING, "the resume right after onCreate has just fetched");
        expect(Refresh.onResume(Refresh.Shown.LIBRARY, false, false) == Refresh.OnResume.NOTHING, "not connected: nothing to ask");
        expect(Refresh.onResume(null, false, true) == Refresh.OnResume.NOTHING, "nothing shown yet");

        // ---- The library polls while any build in it is queued or running, and stops once all are done ----
        for (BuildJob.Stage running : new BuildJob.Stage[]{BuildJob.Stage.QUEUED, BuildJob.Stage.PREPARING, BuildJob.Stage.BUILDING,
                BuildJob.Stage.TESTING, BuildJob.Stage.SIGNING})
            expect(Refresh.libraryPolls(Arrays.asList(project("a", null), project("b", running))), "a build " + running + ": keep polling");
        for (BuildJob.Stage done : new BuildJob.Stage[]{BuildJob.Stage.READY, BuildJob.Stage.FAILED, BuildJob.Stage.CANCELLED})
            expect(!Refresh.libraryPolls(Arrays.asList(project("a", done))), "a build " + done + ": stop polling");
        expect(!Refresh.libraryPolls(Arrays.asList(project("a", null), project("b", null))), "no active build: no polling");
        expect(!Refresh.libraryPolls(Collections.<BuilderApi.Project>emptyList()) && !Refresh.libraryPolls(null), "no projects: no polling");

        System.out.println("RefreshTest: " + checks + " checks passed");
    }
}
