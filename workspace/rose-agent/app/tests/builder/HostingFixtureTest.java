package dev.pixel.builder;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.Map;

/**
 * Project 1, the complete flow: the phone reads a hosted client-server version and the creator's invite exactly as the
 * one Rose server sends them. App Core records builder-hosting-v1.json from a real job and the real /invite route and
 * holds the live shapes to it on the server side (test_app_host_agent, test_builder_routes); this holds BuilderWire,
 * Hosting and SignIn to the same file, so one file pins versions[].hosting and the invite from both ends.
 */
public final class HostingFixtureTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    @SuppressWarnings("unchecked")
    static Map<String, Object> obj(Object o, String what) {
        if (!(o instanceof Map)) throw new AssertionError("fixture has no " + what);
        return (Map<String, Object>) o;
    }

    static String json(Object o) { StringBuilder b = new StringBuilder(); BuilderFixtureTest.write(o, b); return b.toString(); }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new AssertionError("usage: HostingFixtureTest builder-hosting-v1.json");
        Map<String, Object> f = obj(Json.parse(new String(Files.readAllBytes(Paths.get(args[0])), StandardCharsets.UTF_8)), "root");
        expect(Long.valueOf(1).equals(f.get("schemaVersion")), "fixture schemaVersion 1");

        // A hosted client-server version, as GET /builder/v1/projects carries it.
        BuilderApi.Version v = BuilderWire.version(obj(f.get("version"), "version"));
        expect(v.hosting.setUp() && v.hosting.appUrl.startsWith("https://") && v.hosting.setUpAt > 0, "hosting reads as set up, with its address and time");
        expect(v.server.status == ServerSummary.Status.OK, "the server summary beside it keeps its exact keys and stays readable");
        expect(Hosting.line(v.hosting).startsWith("Backend set up · "), "worded as set up, never as running");
        String signer = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        BuilderApi.Project p = new BuilderApi.Project("p", "Notes", "app.rose.gen.pp", signer, java.util.Collections.singletonList(v), null);
        expect(SignIn.offer(p, new InstallPlan.Installed("app.rose.gen.pp", signer, v.versionCode)) == SignIn.Offer.OFFER, "the hosted version installed, builder-signed: sign-in offered");
        expect(SignIn.offer(p, null) == SignIn.Offer.INSTALL_FIRST, "not installed: no invite asked for");

        // POST /builder/v1/projects/{id}/invite 200.
        Map<String, Object> inv = obj(f.get("invite"), "invite");
        String project = (String) inv.get("projectId"), account = (String) inv.get("account");
        BuilderApi.Invite invite = BuilderWire.invite(json(inv), project, account);
        expect(invite.projectId.equals(project) && invite.account.equals(account) && invite.versionCode >= 1, "the 200 reads for its project and account");
        expect(invite.expiresInS == 604800, "seven days");
        expect(!invite.toString().contains(invite.token), "toString never shows the token");
        try { BuilderWire.invite(json(inv), project + "x", account); throw new AssertionError("accepted another project's invite"); }
        catch (BuilderWire.Malformed e) { expect(!String.valueOf(e.getMessage()).contains(invite.token), "never in a message"); }

        // Each refusal exactly as the route sends it.
        Map<String, Object> refusals = obj(f.get("refusals"), "refusals");
        String[][] expected = {
            {"invalid_account", "invalid_account", "false"}, {"not_hosted", "not_hosted", "false"},
            {"already_enrolled", "already_enrolled", "false"}, {"server_unavailable", "server_unavailable", "true"},
            {"not_found", "not_found", "false"},
        };
        expect(refusals.size() == expected.length, "every refusal in the fixture is covered (" + refusals.keySet() + ")");
        for (String[] e : expected) {
            Map<String, Object> r = obj(refusals.get(e[0]), e[0]);
            BuilderApi.InviteRefused got = BuilderWire.inviteRefusal(((Long) r.get("status")).intValue(), json(r.get("body")));
            expect(got != null && e[1].equals(got.word) && Boolean.parseBoolean(e[2]) == got.retryable, e[0] + " reads as " + e[1] + ", retryable " + e[2]);
            expect(!SignIn.refused(got, "Notes").contains("(" + got.word + ")"), e[0] + " is worded, not shown raw");
        }

        System.out.println("HostingFixtureTest: " + checks + " checks passed");
    }
}
