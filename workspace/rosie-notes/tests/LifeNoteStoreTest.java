package dev.pixel.rosienotes;

import java.io.File;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.*;
import java.util.concurrent.*;

/** Synthetic app-private roots only. No Android, phone, service, owner notes or network. */
public final class LifeNoteStoreTest {
 private static int checks;
 private static final long TIME = 1794300000123L;
 private static final String BODY = "Rosie 🌹\nCafe\u0301 / café / 東京\nhttps://example.org/research\n";
 private static void check(boolean value, String label) { checks++; if (!value) throw new AssertionError(label); }
 private static Map<String, Object> args(String task, String workspace) {
  Map<String, Object> p = new LinkedHashMap<>(); p.put("task_id", task); p.put("workspace_id", workspace); return p;
 }
 private static Map<String, Object> put(String task, String workspace, String body) {
  Map<String, Object> p = args(task, workspace); p.put("controller_task_id", task); p.put("title", "Synthetic research"); p.put("body", body); p.put("sha256", LifeNoteStore.sha256(body)); return p;
 }
 private static void status(Map<String, Object> result, String expected) {
  check(expected.equals(result.get("status")), "status " + expected + ": " + result);
  if (!expected.equals("verified") && !expected.equals("ok")) {
   check(Boolean.FALSE.equals(result.get("verified")), "failure never verified");
   check(!result.containsKey("body") && !result.containsKey("note_id") && !result.containsKey("saved_at"), "no content or fabricated receipt on failure");
  }
 }
 private static LifeNoteStore store(Path root) { return new LifeNoteStore(root.toFile(), () -> TIME, (t,w,ct) -> true); }
 private static Path record(Path root, String task) { return root.resolve(LifeNoteStore.NAMESPACE).resolve(task + ".note"); }
 private static String repeat(String text, int count) { StringBuilder b = new StringBuilder(); for (int i = 0; i < count; i++) b.append(text); return b.toString(); }
 private static void cleanup(Path root) throws Exception {
  try (java.util.stream.Stream<Path> entries = Files.walk(root)) {
   for (Path p : (Iterable<Path>) entries.sorted(Comparator.reverseOrder())::iterator) Files.delete(p);
  }
 }
 public static void main(String[] argv) throws Exception {
  if (argv.length > 0) {
   Map<String, Object> r = store(Paths.get(argv[0])).execute("life_note_read", args("task_A", "workspace_A"));
   status(r, "verified"); check(BODY.equals(r.get("body")), "fresh JVM body"); check(Double.valueOf(TIME / 1000.0).equals(r.get("saved_at")), "fresh JVM saved timestamp");
   System.out.println("restart readback passed"); return;
  }
  Path root = Files.createTempDirectory("life-note-test-");
  try {
   LifeNoteStore s = store(root);
   Map<String, Object> c = s.execute("life_note_capabilities", Collections.emptyMap()); status(c, "ok");
   check(c.get("max_body_chars").equals(16000) && c.get("max_title_chars").equals(300), "bounded capabilities");
   check(c.get("owner_data_access").equals(false), "owner data excluded");
   check(!Files.exists(root.resolve(LifeNoteStore.NAMESPACE)), "capability discovery touches no storage");
   status(s.execute("life_note_read", args("task_A", "workspace_A")), "not_found");
   Map<String, Object> first = s.execute("life_note_put", put("task_A", "workspace_A", BODY)); status(first, "verified");
   check(Boolean.TRUE.equals(first.get("created")), "created once"); check("life-task_A".equals(first.get("note_id")), "stable note id");
   check(BODY.equals(first.get("body")) && LifeNoteStore.sha256(BODY).equals(first.get("sha256")), "exact Unicode saved body hash");
   check(Double.valueOf(TIME / 1000.0).equals(first.get("saved_at")), "milliseconds represented in Unix seconds");
   byte[] original = Files.readAllBytes(record(root, "task_A"));
   Map<String, Object> again = new LifeNoteStore(root.toFile(), () -> TIME + 60000, (t,w,ct) -> true).execute("life_note_put", put("task_A", "workspace_A", BODY));
   status(again, "verified"); check(Boolean.FALSE.equals(again.get("created")), "duplicate not recreated");
   check(first.get("saved_at").equals(again.get("saved_at")), "idempotency keeps saved time");
   check(Arrays.equals(original, Files.readAllBytes(record(root, "task_A"))), "idempotency keeps exact disk bytes");
   Process process = new ProcessBuilder(System.getProperty("java.home") + "/bin/java", "-cp", System.getProperty("java.class.path"), LifeNoteStoreTest.class.getName(), root.toString()).inheritIO().start();
   check(process.waitFor() == 0, "separate-process restart survives");
   Map<String, Object> read = s.execute("life_note_read", args("task_A", "workspace_A")); status(read, "verified"); check(!read.containsKey("created"), "read does not claim creation");
   status(s.execute("life_note_put", put("task_A", "workspace_A", "different")), "conflict");
   Map<String, Object> renamed = put("task_A", "workspace_A", BODY); renamed.put("title", "different"); status(s.execute("life_note_put", renamed), "conflict");
   status(s.execute("life_note_read", args("task_A", "workspace_B")), "scope_mismatch");
   status(s.execute("life_note_put", put("task_A", "workspace_B", BODY)), "scope_mismatch");
   check(Arrays.equals(original, Files.readAllBytes(record(root, "task_A"))), "conflict preserves original");
   for (Object bad : Arrays.asList("../owner", "a/b", "a\\b", "a:b", "a.b", "", "é", "_a", repeat("x", 129), 12, true)) {
    Map<String, Object> p = put("bad", "workspace", BODY); p.put("task_id", bad); status(s.execute("life_note_put", p), "invalid_request");
    p = args("task_A", "workspace_A"); p.put("workspace_id", bad); status(s.execute("life_note_read", p), "invalid_request");
   }
   Map<String, Object> p = put("bad", "workspace", BODY); p.put("path", "/owner/notes"); status(s.execute("life_note_put", p), "invalid_request");
   p = put("bad", "workspace", BODY); p.remove("sha256"); status(s.execute("life_note_put", p), "invalid_request");
   p = put("bad", "workspace", BODY); p.put("sha256", repeat("0", 64)); status(s.execute("life_note_put", p), "invalid_request");
   p = put("bad", "workspace", BODY); p.put("sha256", LifeNoteStore.sha256(BODY).toUpperCase(Locale.ROOT)); status(s.execute("life_note_put", p), "invalid_request");
   for (Object bad : Arrays.asList("", repeat("b", 16001), "\ud800", "\udfff", "\ud800x", 7, true, Collections.emptyMap())) {
    p = put("bad", "workspace", BODY); p.put("body", bad); status(s.execute("life_note_put", p), "invalid_request");
   }
   p = put("bad", "workspace", BODY); p.put("body", null); status(s.execute("life_note_put", p), "invalid_request");
   p = put("bad", "workspace", BODY); p.put("title", repeat("t", 301)); status(s.execute("life_note_put", p), "invalid_request");
   p = put("bad", "workspace", BODY); p.put("title", "\ud800"); status(s.execute("life_note_put", p), "invalid_request");
   status(s.execute("life_note_list", Collections.emptyMap()), "invalid_request");
   status(s.execute("life_note_capabilities", args("t", "w")), "invalid_request");
   status(s.execute("life_note_read", null), "invalid_request");
   p = put("max", "workspace", repeat("🌹", 16000)); p.put("title", repeat("🌹", 300)); status(s.execute("life_note_put", p), "verified");
   check(((String) s.execute("life_note_read", args("max", "workspace")).get("body")).length() == 32000, "limits count code points, no normalization");
   check("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855".equals(LifeNoteStore.sha256("")), "standard SHA256");
   check("87c606627ecd645287f94280d61997265d5fc3ceb17ff74b5bb81b68e8b66968".equals(LifeNoteStore.sha256("🌹")), "UTF-8 SHA256 fixture");
   check(Files.getPosixFilePermissions(record(root, "task_A")).equals(PosixFilePermissions.fromString("rw-------")), "private note permissions");
   check(Files.getPosixFilePermissions(root.resolve(LifeNoteStore.NAMESPACE)).equals(PosixFilePermissions.fromString("rwx------")), "private directory permissions");
   // Disk truth always wins over an earlier successful operation/result.
   byte[] corrupt = original.clone(); corrupt[20] ^= 1; Files.write(record(root, "task_A"), corrupt);
   status(s.execute("life_note_read", args("task_A", "workspace_A")), "corrupt");
   status(s.execute("life_note_put", put("task_A", "workspace_A", BODY)), "corrupt");
   check(Arrays.equals(corrupt, Files.readAllBytes(record(root, "task_A"))), "corruption never repaired by overwriting");
   Files.write(record(root, "task_A"), original);
   Files.copy(record(root, "task_A"), record(root, "imposter")); status(s.execute("life_note_read", args("imposter", "workspace_A")), "corrupt");
   Files.write(record(root, "truncated"), new byte[7]); status(s.execute("life_note_read", args("truncated", "workspace_A")), "corrupt");
   Files.write(record(root, "oversized"), new byte[70001]); status(s.execute("life_note_read", args("oversized", "workspace_A")), "corrupt");
   Path outside = root.resolve("owner-synthetic.txt"); Files.write(outside, "never read me".getBytes(StandardCharsets.UTF_8));
   Files.createSymbolicLink(record(root, "symlink"), outside); status(s.execute("life_note_read", args("symlink", "workspace_A")), "corrupt");
   Files.createSymbolicLink(root.resolve(LifeNoteStore.NAMESPACE).resolve(".pending"), outside);
   status(s.execute("life_note_put", put("unsafe", "workspace", BODY)), "unavailable");
   check("never read me".equals(new String(Files.readAllBytes(outside), StandardCharsets.UTF_8)), "symlink targets untouched");
   Files.delete(root.resolve(LifeNoteStore.NAMESPACE).resolve(".pending"));
   Path lock = root.resolve(LifeNoteStore.NAMESPACE).resolve(".lock");
   try (FileChannel channel = FileChannel.open(lock, StandardOpenOption.WRITE); FileLock ignored = channel.lock()) {
    status(s.execute("life_note_put", put("busy", "workspace", BODY)), "unavailable");
   }
   status(new LifeNoteStore(root.toFile(), () -> 0, (t,w,ct) -> true).execute("life_note_put", put("clock", "workspace", BODY)), "unavailable");
   status(new LifeNoteStore(root.resolve("missing").toFile(), (t,w,ct) -> true).execute("life_note_read", args("task", "workspace")), "unavailable");
   ExecutorService pool = Executors.newFixedThreadPool(2);
   try {
    Future<Map<String,Object>> a = pool.submit(() -> store(root).execute("life_note_put", put("race", "workspace", "first")));
    Future<Map<String,Object>> b = pool.submit(() -> store(root).execute("life_note_put", put("race", "workspace", "second")));
    Set<Object> outcomes = new HashSet<>(Arrays.asList(a.get().get("status"), b.get().get("status")));
    check(outcomes.equals(new HashSet<>(Arrays.asList("verified", "conflict"))), "concurrent instances never overwrite");
   } finally { pool.shutdownNow(); }
   // Keep a stale staging file after a simulated pre-rename crash. Read never treats it as a receipt.
   Files.write(root.resolve(LifeNoteStore.NAMESPACE).resolve(".pending"), original);
   status(s.execute("life_note_read", args("pending", "workspace")), "not_found");
   status(s.execute("life_note_put", put("pending", "workspace", BODY)), "verified");
   check(!Files.exists(root.resolve(LifeNoteStore.NAMESPACE).resolve(".pending")), "stale stage replaced then removed");
   for (int i = 0; i < LifeNoteStore.MAX_NOTES; i++) Files.write(record(root, "capacity_" + i), new byte[]{0});
   Map<String, Object> full = s.execute("life_note_put", put("full", "workspace", BODY)); status(full, "unavailable"); check("store_full".equals(full.get("code")), "bounded total note count");
   status(s.execute("life_note_read", args("task_A", "workspace_A")), "verified");
   try { MiniJson.parse("{\"task_id\":\"a\",\"task_id\":\"b\"}"); throw new AssertionError("duplicate key accepted"); } catch (IllegalArgumentException expected) { checks++; }
   System.out.println("LifeNoteStoreTest: " + checks + " checks passed");
  } finally { cleanup(root); }
 }
}
