package dev.pixel.rosienotes;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;

public final class WorkspaceBindingTest {
 private static int checks;
 private static void check(boolean ok, String name) { checks++; if (!ok) throw new AssertionError(name); }
 private static Map<String,Object> ready() {
  Map<String,Object> s = new LinkedHashMap<>(); s.put("status", "ready"); s.put("sessionId", "12345678-1234-1234-1234-123456789012");
  s.put("taskId", "task"); s.put("targetPackage", WorkspaceBinding.TARGET); s.put("blocked", false); s.put("foregroundFallback", false); s.put("elapsedMs", 1234L); return s;
 }
 public static void main(String[] argv) throws Exception {
  String workspace = "12345678-1234-1234-1234-123456789012";
  check(WorkspaceBinding.ready(ready(), "task", workspace, "task"), "actual ready controller state");
  for (String key : ready().keySet()) {
   Map<String,Object> s = ready(); s.remove(key); check(!WorkspaceBinding.ready(s, "task", workspace, "task"), "missing " + key);
   s = ready(); s.put(key, null); check(!WorkspaceBinding.ready(s, "task", workspace, "task"), "null " + key);
  }
  for (Object elapsed : Arrays.asList(-1, 600000, Double.NaN, Double.POSITIVE_INFINITY, "1234", true)) {
   Map<String,Object> s = ready(); s.put("elapsedMs", elapsed); check(!WorkspaceBinding.ready(s,"task",workspace,"task"), "bad elapsed");
  }
  for (String key : Arrays.asList("status","sessionId","taskId","targetPackage","blocked","foregroundFallback")) {
   Map<String,Object> s = ready(); s.put(key, key.equals("blocked") || key.equals("foregroundFallback") ? true : "wrong");
   check(!WorkspaceBinding.ready(s,"task",workspace,"task"), "different " + key);
  }
  check(!WorkspaceBinding.ready(ready(), "task", workspace, "other"), "no task aliases");
  Path root = Files.createTempDirectory("life-binding-test-");
  try {
   Map<String,Object> p = new LinkedHashMap<>(); p.put("task_id","task"); p.put("workspace_id",workspace); p.put("controller_task_id","task");
   p.put("title","Synthetic"); p.put("body","Synthetic note"); p.put("sha256",LifeNoteStore.sha256("Synthetic note"));
   Map<String,Object> read = new LinkedHashMap<>(); read.put("task_id","task"); read.put("workspace_id",workspace);
   check("scope_mismatch".equals(new LifeNoteStore(root.toFile(), null).execute("life_note_put",p).get("status")), "no gate means no write");
   check("scope_mismatch".equals(new LifeNoteStore(root.toFile(), (t,w,c) -> false).execute("life_note_put",p).get("status")), "unready workspace cannot write");
   AtomicInteger calls = new AtomicInteger();
   LifeNoteStore allowed = new LifeNoteStore(root.toFile(), (t,w,c) -> { calls.incrementAndGet(); return WorkspaceBinding.ready(ready(), t,w,c); });
   Map<String,Object> result = allowed.execute("life_note_put",p);
   check("verified".equals(result.get("status")) && "task".equals(result.get("controller_task_id")), "verified controller ownership persisted");
   check(calls.get() == 2, "new write checks actual state before staging and immediately before publication");
   LifeNoteStore closed = new LifeNoteStore(root.toFile(), (t,w,c) -> { throw new AssertionError("existing readback queried controller"); });
   check("verified".equals(closed.execute("life_note_read",read).get("status")), "readback after controller close");
   check(Boolean.FALSE.equals(closed.execute("life_note_put",p).get("created")), "idempotent retry after close does not rewrite");
   Map<String,Object> snapshot = closed.localSnapshot();
   check(((List<?>)snapshot.get("notes")).size()==1 && ((Number)snapshot.get("total")).intValue()==1, "local UI reads only saved private notes");
   Map<String,Object> interrupted = new LinkedHashMap<>(p); interrupted.put("task_id", "interrupted"); interrupted.put("controller_task_id", "interrupted");
   AtomicInteger checkCount = new AtomicInteger();
   LifeNoteStore interrupt = new LifeNoteStore(root.toFile(), (t,w,c) -> checkCount.incrementAndGet() == 1);
   check("scope_mismatch".equals(interrupt.execute("life_note_put",interrupted).get("status")), "stop during staging blocks publication");
   Map<String,Object> interruptedRead = new LinkedHashMap<>(read); interruptedRead.put("task_id", "interrupted");
   check("not_found".equals(interrupt.execute("life_note_read",interruptedRead).get("status")), "interrupted stage is not a saved note");
   check(!Files.exists(root.resolve(LifeNoteStore.NAMESPACE).resolve(".pending")), "interrupted stage cleaned");
   p.put("controller_task_id", "other");
   check("invalid_request".equals(allowed.execute("life_note_put",p).get("status")), "caller cannot alias a controller task");
  } finally {
   try (java.util.stream.Stream<Path> paths=Files.walk(root)) { for (Path p : (Iterable<Path>) paths.sorted(Comparator.reverseOrder())::iterator) Files.delete(p); }
  }
  System.out.println("WorkspaceBindingTest: " + checks + " checks passed");
 }
}
