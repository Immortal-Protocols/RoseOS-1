package dev.pixel.builder;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * The creator's sign-in to their own client-server app, on App Core's v1 additions: versions[].hosting (the version
 * whose server is set up in the agent container), POST projects/<id>/invite and its refusals. When "Sign in" is
 * offered, the invite wire, and what each answer reads as. A token is never in any text the phone can show or log.
 */
public final class SignInTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    static final String SHA = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    static final String TOKEN = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567ABCDEFGHIJKLMNOPQRST"; // 52, rose-signin base32
    static final String LONG; static { StringBuilder b = new StringBuilder(); for (int i = 0; i < 512; i++) b.append((char) (0x21 + i % 94)); LONG = b.toString(); }
    static final String URL = "https://agent-0123456789abcdef0123456789abcdef.apps.chariots.sh";

    static Map<String, Object> hosting(Object state, Object url, Object at) {
        Map<String, Object> m = new HashMap<>();
        if (state != null) m.put("state", state);
        if (url != null) m.put("appUrl", url);
        if (at != null) m.put("setUpAt", at);
        return m;
    }

    static BuilderApi.Version version(long code, boolean hosted) {
        return new BuilderApi.Version(code, String.valueOf(code), 1000, "j" + code, "r" + code, SHA, 10, null, ApiReport.none(),
            ShareState.unpublished(), ServerSummary.absent(), hosted ? Hosting.parse(hosting("set_up", URL, 1794876618000L)) : Hosting.absent());
    }

    static BuilderApi.Project project(BuilderApi.Version... vs) {
        return new BuilderApi.Project("p1", "Notes", "app.rose.gen.pp1", SHA, Arrays.asList(vs), null);
    }

    static InstallPlan.Installed installed(long code) { return new InstallPlan.Installed("app.rose.gen.pp1", SHA, code); }

    static String invite(String project, Object version, String account, String token, Object expires) {
        return "{\"schemaVersion\":1,\"projectId\":\"" + project + "\",\"versionCode\":" + version + ",\"account\":\"" + account
            + "\",\"invite\":\"" + token + "\",\"expiresInS\":" + expires + "}";
    }

    public static void main(String[] args) throws Exception {
        // ---- versions[].hosting: read strictly; only an exact set_up with a plain https URL and a time is set up ----
        expect(Hosting.parse(null).status == Hosting.Status.ABSENT && Hosting.line(Hosting.parse(null)) == null, "absent shows nothing");
        Hosting up = Hosting.parse(hosting("set_up", URL, 1794876618000L));
        expect(up.setUp() && URL.equals(up.appUrl) && up.setUpAt == 1794876618000L, "set up, with its address and time");
        expect(("Backend set up · agent-0123456789abcdef0123456789abcdef.apps.chariots.sh").equals(Hosting.line(up)), "App Core's wording, the host only");
        expect(!Hosting.line(up).toLowerCase().contains("running"), "never says it runs");
        Object[] unreadable = {
            "set_up", hosting("running", URL, 1L), hosting("set_up", null, 1L), hosting("set_up", "http://a.example", 1L),
            hosting("set_up", "https://u@a.example", 1L), hosting("set_up", URL, null), hosting("set_up", URL, "soon"),
            hosting("set_up", URL, 0L), hosting("set_up", URL, 1.5), hosting(null, URL, 1L),
        };
        for (Object u : unreadable) expect(Hosting.parse(u).status == Hosting.Status.UNREADABLE && !Hosting.parse(u).setUp(), "unreadable, never set up: " + u);
        expect(Hosting.line(Hosting.unreadable()).contains("couldn't be read"), "unreadable wording");

        // ---- The library carries it per version, beside server; a bad one never fails the version ----
        String lib = "{\"schemaVersion\":1,\"projects\":[{\"projectId\":\"p1\",\"name\":\"Notes\",\"packageName\":\"app.rose.gen.pp1\","
            + "\"signerSha256\":null,\"activeJob\":null,\"versions\":["
            + "{\"versionCode\":1,\"versionName\":\"1\",\"createdAt\":1,\"jobId\":\"j1\",\"sourceRevision\":\"r1\",\"apk\":{\"sha256\":\"" + SHA + "\",\"size\":10},"
            + "\"server\":{\"schemaVersion\":1,\"status\":\"ok\",\"api\":1,\"minClientVersionCode\":1}},"
            + "{\"versionCode\":2,\"versionName\":\"2\",\"createdAt\":2,\"jobId\":\"j2\",\"sourceRevision\":\"r2\",\"apk\":{\"sha256\":\"" + SHA + "\",\"size\":10},"
            + "\"server\":{\"schemaVersion\":1,\"status\":\"ok\",\"api\":1,\"minClientVersionCode\":1},"
            + "\"hosting\":{\"state\":\"set_up\",\"appUrl\":\"" + URL + "\",\"setUpAt\":1794876618000}},"
            + "{\"versionCode\":3,\"versionName\":\"3\",\"createdAt\":3,\"jobId\":\"j3\",\"sourceRevision\":\"r3\",\"apk\":{\"sha256\":\"" + SHA + "\",\"size\":10},"
            + "\"hosting\":\"set_up\"}]}]}";
        List<BuilderApi.Version> vs = BuilderWire.library(lib).get(0).project.versions;
        expect(vs.size() == 3, "three versions, the bad hosting failing none");
        expect(vs.get(0).hosting.status == Hosting.Status.ABSENT && vs.get(1).hosting.setUp() && vs.get(2).hosting.status == Hosting.Status.UNREADABLE, "hosting per version");
        expect(vs.get(1).server.status == ServerSummary.Status.OK, "the server summary beside it stays readable");

        // ---- When "Sign in" is offered: the installed version is the one whose server is set up ----
        expect(SignIn.offer(project(version(1, false)), installed(1)) == SignIn.Offer.NONE, "nothing set up: nothing");
        expect(SignIn.offer(project(version(1, true)), null) == SignIn.Offer.INSTALL_FIRST, "not installed: install first, no invite made");
        expect(SignIn.offer(project(version(1, false), version(2, true)), installed(1)) == SignIn.Offer.UPDATE_FIRST, "an older install: update first");
        expect(SignIn.offer(project(version(1, true), version(2, false)), installed(2)) == SignIn.Offer.UPDATE_FIRST, "a newer, unhosted install: install the hosted one");
        expect(SignIn.offer(project(version(1, false), version(2, true)), installed(2)) == SignIn.Offer.OFFER, "the hosted version installed: offered");
        expect(SignIn.offer(null, installed(1)) == SignIn.Offer.NONE, "no project: nothing");

        // ---- An app under the same package name with another signer never gets an invite (App Core review #1) ----
        String other = "f" + SHA.substring(1);
        expect(SignIn.offer(project(version(1, true)), new InstallPlan.Installed("app.rose.gen.pp1", other, 1)) == SignIn.Offer.NOT_THIS_APP, "another signer: not offered");
        expect(SignIn.offer(project(version(1, true)), new InstallPlan.Installed("app.rose.gen.pp1", null, 1)) == SignIn.Offer.NOT_THIS_APP, "no signer read: not offered");
        BuilderApi.Project unpinned = new BuilderApi.Project("p1", "Notes", "app.rose.gen.pp1", null, Arrays.asList(version(1, true)), null);
        expect(SignIn.offer(unpinned, installed(1)) == SignIn.Offer.NOT_THIS_APP, "no pinned signer yet: not offered");
        expect(SignIn.offer(project(version(1, true)), new InstallPlan.Installed("app.rose.gen.pp1", SHA.toUpperCase(), 1)) == SignIn.Offer.OFFER, "signers compare without case");
        expect(SignIn.offerLine(SignIn.Offer.NOT_THIS_APP, "Notes").contains("wasn't signed by your builder"), "not-this-app wording");

        // ---- Re-checked just before the invite leaves: exact package, pinned signer, the invite's version ----
        BuilderApi.Invite forP1 = new BuilderApi.Invite("p1", 1, "nate", TOKEN, 604800);
        expect(SignIn.mayHandOver("app.rose.gen.pp1", SHA, installed(1), forP1), "exactly this app: handed over");
        expect(!SignIn.mayHandOver("app.rose.gen.pp1", SHA, new InstallPlan.Installed("app.rose.gen.pp1", other, 1), forP1), "signer changed since: dropped");
        expect(!SignIn.mayHandOver("app.rose.gen.pp1", SHA, installed(2), forP1), "version changed since: dropped");
        expect(!SignIn.mayHandOver("app.rose.gen.pp1", SHA, null, forP1), "uninstalled since: dropped");
        expect(!SignIn.mayHandOver("app.rose.gen.pp2", SHA, new InstallPlan.Installed("app.rose.gen.pp2", SHA, 1), forP1), "another project's package: dropped");
        expect(!SignIn.mayHandOver("app.rose.gen.pp1", null, installed(1), forP1), "no pinned signer: dropped");
        expect(!SignIn.mayHandOver("app.rose.gen.pp1", SHA, new InstallPlan.Installed("app.rose.gen.pp1x", SHA, 1), forP1), "installed record for another package: dropped");
        expect(!SignIn.mayHandOver("app.rose.gen.pp1", SHA, installed(1), null), "no invite: nothing");
        expect(SignIn.notHandedOver("Notes").contains("Nothing was signed in"), "the dropped invite is said plainly");
        expect(SignIn.offerLine(SignIn.Offer.INSTALL_FIRST, "Notes").equals("Install Notes to sign in to it."), "install-first wording");
        expect(SignIn.offerLine(SignIn.Offer.OFFER, "Notes") == null && SignIn.offerLine(SignIn.Offer.NONE, "Notes") == null, "no line when offered or not applicable");

        // ---- The invite request ----
        expect("{\"schemaVersion\":1,\"account\":\"nate\"}".equals(BuilderWire.inviteBody("nate")), "invite body");
        expect("nate".equals(BuilderWire.account("  nate ")), "account trimmed");
        expect(BuilderWire.account("") == null && BuilderWire.account("   ") == null && BuilderWire.account(null) == null, "no account");
        expect(BuilderWire.account("a\nb") == null && BuilderWire.account("a:b") == null, "no control characters or ':'");
        StringBuilder b128 = new StringBuilder(); for (int i = 0; i < 128; i++) b128.append('a');
        expect(BuilderWire.account(b128.toString()) != null && BuilderWire.account(b128 + "a") == null, "at most 128 bytes");
        StringBuilder e43 = new StringBuilder(); for (int i = 0; i < 43; i++) e43.append('é'); // 86 bytes
        expect(BuilderWire.account(e43.toString()) != null && BuilderWire.account(e43.toString() + e43 + "é") == null, "bytes, not characters");

        // ---- The 200 ----
        BuilderApi.Invite inv = BuilderWire.invite(invite("p1", 2, "nate", TOKEN, 604800), "p1", "nate");
        expect(inv.projectId.equals("p1") && inv.versionCode == 2 && inv.account.equals("nate") && inv.token.equals(TOKEN) && inv.expiresInS == 604800, "a 200 reads");
        expect(!inv.toString().contains(TOKEN) && inv.toString().contains("redacted"), "toString never shows the token");
        // Language-agnostic backends make their own opaque tokens: 1 to 512 printable ASCII, no whitespace.
        for (String ok : new String[]{"a", "opaque-Token_123.~!", TOKEN.toLowerCase(), LONG})
            expect(BuilderWire.invite(invite("p1", 2, "nate", ok.replace("\\", "\\\\").replace("\"", "\\\""), 604800), "p1", "nate").token.equals(ok), "an opaque token reads: " + ok.length());
        String[] bad = {
            invite("p2", 2, "nate", TOKEN, 604800), invite("p1", 2, "other", TOKEN, 604800),
            invite("p1", 2, "nate", "abc def", 604800), invite("p1", 2, "nate", "abc\\tdef", 604800),
            invite("p1", 2, "nate", "caf\u00e9", 604800), invite("p1", 2, "nate", LONG + "x", 604800),
            invite("p1", 0, "nate", TOKEN, 604800), invite("p1", 2, "nate", TOKEN, 0),
            invite("p1", "\"2\"", "nate", TOKEN, 604800), invite("p1", 2, "nate", TOKEN, 1.5),
            "{\"schemaVersion\":2,\"projectId\":\"p1\",\"versionCode\":2,\"account\":\"nate\",\"invite\":\"" + TOKEN + "\",\"expiresInS\":1}",
            "{\"schemaVersion\":1,\"projectId\":\"p1\",\"versionCode\":2,\"account\":\"nate\",\"expiresInS\":1}",
        };
        for (String b : bad) {
            try { BuilderWire.invite(b, "p1", "nate"); throw new AssertionError("accepted " + b.replace(TOKEN, "<token>")); }
            catch (BuilderWire.Malformed e) { expect(!String.valueOf(e.getMessage()).contains(TOKEN), "a refusal message never carries the token"); }
        }

        // ---- Refusals: App Core's {error: sentence, reason: word, retryable} ----
        BuilderApi.InviteRefused r = BuilderWire.inviteRefusal(409, "{\"error\":\"Not set up yet.\",\"reason\":\"not_hosted\",\"retryable\":false}");
        expect(r != null && "not_hosted".equals(r.word) && !r.retryable, "409 not_hosted");
        r = BuilderWire.inviteRefusal(409, "{\"error\":\"Try later.\",\"reason\":\"server_unavailable\",\"retryable\":true}");
        expect(r != null && r.retryable, "409 retryable");
        r = BuilderWire.inviteRefusal(400, "{\"error\":\"Bad name.\",\"reason\":\"invalid_account\"}");
        expect(r != null && "invalid_account".equals(r.word) && !r.retryable, "400 invalid_account");
        expect("not_found".equals(BuilderWire.inviteRefusal(404, "{\"detail\":\"Not Found\"}").word), "404: not found");
        expect(BuilderWire.inviteRefusal(409, "{\"error\":\"no reason\"}") == null, "no reason word: not a refusal");
        expect(BuilderWire.inviteRefusal(503, "not json") == null, "unreadable: not a refusal");
        expect(BuilderWire.inviteRefusal(409, "{\"reason\":\"Not A Word\"}") == null, "a reason that isn't a word: not a refusal");

        // ---- What each answer reads as ----
        expect(SignIn.refused(new BuilderApi.InviteRefused("not_hosted", false), "Notes").equals("Notes's backend isn't set up yet."), "not hosted");
        expect(SignIn.refused(new BuilderApi.InviteRefused("server_unavailable", true), "Notes").endsWith("Try again in a little while."), "retryable says so");
        expect(SignIn.refused(new BuilderApi.InviteRefused("open_admission", false), "Notes").contains("needs no invite"), "open admission");
        expect(SignIn.refused(new BuilderApi.InviteRefused("invites_unsupported", false), "Notes").equals("Notes's backend has no sign-in invites. Open Notes to sign up."), "a backend without invites");
        expect(SignIn.refused(new BuilderApi.InviteRefused("already_enrolled", false), "Notes").contains("already set up"), "already enrolled");
        expect(SignIn.refused(new BuilderApi.InviteRefused("brand_new", false), "Notes").contains("(brand_new)"), "an unknown word is shown plainly");
        expect(SignIn.refused(null, "Notes").contains("(refused)"), "no refusal");
        for (String w : new String[]{"invalid_account", "not_found"})
            expect(!SignIn.refused(new BuilderApi.InviteRefused(w, false), "Notes").contains("(" + w + ")"), "a known word is worded: " + w);

        // ---- The demo never pretends to sign anyone in ----
        try { new FakeBuilderApi().invite("p1", "nate"); throw new AssertionError("demo issued an invite"); }
        catch (BuilderApi.InviteRefused e) { expect("not_hosted".equals(e.word), "the demo has no backend"); }

        System.out.println("SignInTest: " + checks + " checks passed");
    }
}
