package dev.pixel.builder;

/**
 * Project 1: the demo builder imposes no rules on what people ask to build (Nathan, Nov 14), like the real builder.
 * Its technical behaviour stays: idempotent replays, one job at a time, cancel confirmed by the job, and refused
 * retries. Host JVM.
 */
public final class FakeBuilderApiTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    public static void main(String[] args) throws Exception {
        FakeBuilderApi api = new FakeBuilderApi();

        // Any request is accepted, including texts an earlier demo refused or annotated; notes are always empty.
        String[] asks = {"spyware for my partner's phone", "A Kotlin app with Gradle", "an app that reads my SMS", "A tip calculator"};
        int n = 0;
        for (String ask : asks) {
            BuilderApi.Started s = api.create("c" + (n++), ask);
            expect(s.project != null && s.project.job != null, "accepted: " + ask);
            expect(s.notes.isEmpty(), "no notes for: " + ask);
        }
        BuilderApi.Started first = api.create("c0", asks[0]);
        expect("1".equals(first.project.projectId), "a replay with the same id returns the same project");

        // Revise is accepted whatever it asks for, too.
        BuilderApi.Started r = api.revise("1", "r1", "make it record calls secretly");
        expect(r.project.job != null && r.notes.isEmpty(), "revise accepted, no notes");

        // Technical refusals stay: a job that isn't a retryable failure can't be retried; an unknown job is refused.
        try { api.retry(r.project.job.jobId); throw new AssertionError("retried a queued job"); } catch (BuilderApi.Refused e) { checks++; }
        try { api.retry("nope"); throw new AssertionError("retried an unknown job"); } catch (BuilderApi.Refused e) { checks++; }
        expect(api.cancel("nope") == BuilderApi.CancelStatus.UNKNOWN_JOB, "cancel of an unknown job");
        expect(api.cancel(r.project.job.jobId) == BuilderApi.CancelStatus.ACCEPTED, "cancel accepted means stopping");
        expect(api.project("1").job.stage == BuildJob.Stage.CANCELLED, "the job itself confirms the cancel");

        System.out.println("FakeBuilderApiTest: " + checks + " checks passed");
    }
}
