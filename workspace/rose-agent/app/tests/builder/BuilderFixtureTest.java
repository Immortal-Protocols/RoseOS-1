package dev.pixel.builder;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.Map;

/**
 * Project 1, one server: the phone reads builder API v1 exactly as the one Rose server (rose.app --app-builder) serves
 * it. App Core records the fixture from the real path (rose.app Server, build_remote_app, signed requests, an SDK-built
 * version) and holds the live response shape to it on the server side (rose.tests.test_app_builder_one_server.Wire);
 * this holds BuilderWire to the same file, so one file pins the wire from both ends.
 */
public final class BuilderFixtureTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    /** One recorded reply as the server's body. Json.write covers only what the phone sends (no fractions), so this test
     *  writes the replies back itself; strings still go through Json.write's escaping. */
    static String part(Map<String, Object> fixture, String key) {
        Object o = fixture.get(key);
        if (!(o instanceof Map)) throw new AssertionError("fixture has no " + key);
        StringBuilder b = new StringBuilder();
        write(o, b);
        return b.toString();
    }

    static void write(Object v, StringBuilder b) {
        if (v instanceof Map) {
            b.append('{'); boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                if (!first) b.append(','); first = false;
                b.append(Json.write(e.getKey())).append(':'); write(e.getValue(), b);
            }
            b.append('}');
        } else if (v instanceof java.util.List) {
            b.append('['); boolean first = true;
            for (Object x : (java.util.List<?>) v) { if (!first) b.append(','); first = false; write(x, b); }
            b.append(']');
        } else if (v instanceof Double) b.append(new java.math.BigDecimal((Double) v).toPlainString());
        else b.append(Json.write(v));
    }

    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new AssertionError("usage: BuilderFixtureTest builder-library-v1.json");
        Map<String, Object> f = (Map<String, Object>) Json.parse(new String(Files.readAllBytes(Paths.get(args[0])), StandardCharsets.UTF_8));
        expect(Long.valueOf(1).equals(f.get("schemaVersion")), "fixture schemaVersion 1");

        // POST /builder/v1/projects: the accepted create.
        BuilderWire.Accepted started = BuilderWire.accepted(part(f, "started"), true);
        expect(started.projectId != null && started.jobId != null && started.notes.isEmpty(), "create accepted: project, job, no notes");

        // GET /builder/v1/jobs/<id>: the same job, ready.
        BuildJob job = BuilderWire.job(part(f, "job"));
        expect(job.jobId.equals(started.jobId), "the polled job is the started one");
        expect(job.stage == BuildJob.Stage.READY && job.failure == null && job.attempt == 1, "ready, first attempt, no failure");
        expect(job.startedAt > 0 && job.message != null, "seconds read as a time; the server's message is kept");

        // GET /builder/v1/projects: the library with the built version, a client-only app.
        java.util.List<BuilderWire.LibraryProject> library = BuilderWire.library(part(f, "library"));
        expect(library.size() == 1, "one project");
        BuilderApi.Project p = library.get(0).project;
        expect(p.projectId.equals(started.projectId) && library.get(0).activeJobId == null && p.job == null, "the project, with no active job");
        expect(p.packageName.equals("app.rose.gen.p" + p.projectId), "a generated package (app.rose.gen.p<projectId>)");
        expect(p.signerSha256 != null && p.signerSha256.matches("[0-9a-f]{64}"), "the builder's signer pin");
        expect(p.versions.size() == 1, "one version");
        BuilderApi.Version v = p.versions.get(0);
        expect(v.versionCode == 1 && v.jobId.equals(job.jobId), "version 1, from the ready job");
        expect(v.apkSha256.matches("[0-9a-f]{64}") && v.apkSize > 0, "a real APK: SHA-256 and size for the artifact download");
        expect(v.createdAt > 0, "createdAt read as a time");
        expect(v.apiReport.status == ApiReport.Status.COMPLETE && !v.apiReport.unreadable, "the advisory API report reads as complete");
        expect(v.share.status == ShareState.Status.UNPUBLISHED && !v.share.unreadable, "no share: unpublished");
        expect(v.server.status == ServerSummary.Status.ABSENT, "client-only: no server summary");

        System.out.println("BuilderFixtureTest: " + checks + " checks passed");
    }
}
