package dev.pixel.rosienotes;

import java.io.*;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.util.*;

/** Immutable Rosie-only notes. No Android types, owner data, caller-selected paths or network.
 * The caller supplies only the app's trusted private directory, never a request parameter.
 * Each task owns one permanent workspace binding. Disk readback, not a cached reply, is proof. */
final class LifeNoteStore {
 static final String NAMESPACE = "rosie-life-v1";
 static final int VERSION = 1, MAX_BODY = 16000, MAX_TITLE = 300, MAX_ID = 128, MAX_NOTES = 256;
 private static final int MAGIC = 0x524c4e31, MAX_BYTES = 70000;
 interface Clock { long nowMillis(); }
 private final File privateRoot;
 private final Clock clock;
 interface WriteGate { boolean permits(String task, String workspace, String controllerTask); }
 private final WriteGate writeGate;
 LifeNoteStore(File privateRoot, WriteGate gate) { this(privateRoot, System::currentTimeMillis, gate); }
 LifeNoteStore(File privateRoot, Clock clock, WriteGate gate) { this.privateRoot = privateRoot; this.clock = clock; this.writeGate = gate; }

 static Map<String, Object> capabilities() {
  Map<String, Object> c = base("ok");
  c.put("target_package", "dev.pixel.rosienotes"); c.put("workspace_binding", "controller_session_id");
  c.put("max_body_chars", MAX_BODY); c.put("max_title_chars", MAX_TITLE); c.put("max_id_chars", MAX_ID);
  c.put("max_notes", MAX_NOTES); c.put("character_count", "unicode_code_points");
  c.put("id_pattern", "[A-Za-z0-9][A-Za-z0-9_-]{0,127}");
  c.put("hash", "sha256_utf8"); c.put("saved_at_unit", "unix_seconds");
  c.put("immutable", true); c.put("owner_data_access", false);
  Map<String, Object> operations = new LinkedHashMap<>();
  operations.put("life_note_capabilities", Collections.emptyList());
  operations.put("life_note_put", Arrays.asList("task_id", "workspace_id", "controller_task_id", "title", "body", "sha256"));
  operations.put("life_note_read", Arrays.asList("task_id", "workspace_id"));
  c.put("operations", operations);
  return c;
 }

 /** Serializes same-JVM instances; the file lock also serializes separate processes. */
 Map<String, Object> execute(String method, Map<String, Object> params) {
  synchronized (LifeNoteStore.class) {
   boolean writeStarted = false;
   try {
    if ("life_note_capabilities".equals(method)) { only(params); return capabilities(); }
    boolean put = "life_note_put".equals(method);
    if (!put && !"life_note_read".equals(method)) throw new Invalid("Unknown private-note operation");
    if (put) only(params, "task_id", "workspace_id", "controller_task_id", "title", "body", "sha256");
    else only(params, "task_id", "workspace_id");
    String task = id(params.get("task_id")), workspace = id(params.get("workspace_id"));
    String title = null, body = null, hash = null, controllerTask = null;
    if (put) {
     controllerTask = controllerTask(params.get("controller_task_id"));
     if (!task.equals(controllerTask)) throw new Invalid("Controller task must equal the private-note task");
     title = text(params.get("title"), MAX_TITLE); body = text(params.get("body"), MAX_BODY);
     hash = hash(params.get("sha256"));
     if (!hash.equals(sha256(body))) throw new Invalid("Body SHA-256 does not match");
    }
    Path dir = directory();
    Path lockPath = dir.resolve(".lock");
    try (FileChannel channel = FileChannel.open(lockPath, StandardOpenOption.CREATE, StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS);
         FileLock lock = channel.tryLock()) {
     if (lock == null) return error("unavailable", "store_busy", "Private-note store is busy; read back later");
     privateFile(lockPath);
     Path target = dir.resolve(task + ".note");
     if (Files.exists(target, LinkOption.NOFOLLOW_LINKS)) {
      Note existing = read(target);
      if (!existing.task.equals(task)) throw new Corrupt();
      if (!existing.workspace.equals(workspace)) return error("scope_mismatch", "scope_mismatch", "Task belongs to a different private workspace");
      if (put && !existing.controllerTask.equals(controllerTask)) return error("scope_mismatch", "scope_mismatch", "Task belongs to a different controller task");
      if (put && (!existing.title.equals(title) || !existing.body.equals(body) || !existing.hash.equals(hash)))
       return error("conflict", "immutable_note", "This task already has a different private note");
      return existing.result(put, false);
     }
     if (!put) return error("not_found", "note_not_found", "No saved private note exists for this task");
     int count = 0;
     try (DirectoryStream<Path> notes = Files.newDirectoryStream(dir, "*.note")) { for (Path ignored : notes) count++; }
     if (count >= MAX_NOTES) return error("unavailable", "store_full", "Private-note limit reached; no notes were removed");
     if (writeGate == null || !writeGate.permits(task, workspace, controllerTask)) return error("scope_mismatch", "workspace_not_ready", "Exact Rosie workspace is not ready for this controller task");
     long now = clock.nowMillis();
     if (now <= 0) return error("unavailable", "clock_unavailable", "Save time could not be established");
     Note note = new Note(task, workspace, controllerTask, title, body, hash, now);
     byte[] encoded = encode(note);
     // One bounded staging slot under the store lock, including after a process crash.
     // Never invent a new task/note ID on retry.
     Path temp = dir.resolve(".pending");
     if (Files.isSymbolicLink(temp)) throw new IOException("Unsafe staging file");
     writeStarted = true;
     try {
      try (FileChannel out = FileChannel.open(temp, StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING,
          StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS)) {
       privateFile(temp);
       ByteBuffer bytes = ByteBuffer.wrap(encoded);
       while (bytes.hasRemaining()) out.write(bytes);
       out.force(true);
      }
      if (!writeGate.permits(task, workspace, controllerTask)) return error("scope_mismatch", "workspace_not_ready", "Rosie workspace stopped before private-note publication");
      // No non-atomic fallback. A failed/uncertain publish must be reconciled by exact readback.
      Files.move(temp, target, StandardCopyOption.ATOMIC_MOVE);
      syncDirectory(dir);
      Note saved = read(target);
      if (!Arrays.equals(encoded, encode(saved))) throw new Corrupt();
      return saved.result(true, true);
     } finally { Files.deleteIfExists(temp); }
    }
   } catch (Invalid e) { return error("invalid_request", "invalid_request", e.getMessage());
   } catch (Corrupt e) { return error("corrupt", "corrupt_note", "Private-note record is corrupt; it was not replaced");
   } catch (Exception e) {
    // Do not expose paths, note bodies or exception details over the wire.
    return error(writeStarted ? "uncertain" : "unavailable", writeStarted ? "readback_required" : "store_unavailable",
        writeStarted ? "Save outcome is uncertain; read this exact task and workspace before any retry" : "Private-note storage is unavailable");
   }
  }
 }

 /** Local read-only UI only. No provider operation exposes enumeration. At most 20 recent notes. */
 Map<String, Object> localSnapshot() {
  synchronized (LifeNoteStore.class) {
   try {
    Path dir = directory();
    try (FileChannel channel = FileChannel.open(dir.resolve(".lock"), StandardOpenOption.CREATE, StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS);
         FileLock lock = channel.tryLock()) {
     if (lock == null) throw new IOException("Busy");
     privateFile(dir.resolve(".lock"));
     List<Note> notes = new ArrayList<>(); int unavailable = 0, count = 0;
     try (DirectoryStream<Path> files = Files.newDirectoryStream(dir, "*.note")) {
      for (Path path : files) {
       if (++count > MAX_NOTES) throw new IOException("Capacity exceeded");
       try { Note n = read(path); if (!path.getFileName().toString().equals(n.task + ".note")) throw new Corrupt(); notes.add(n); }
       catch (IOException | Corrupt e) { unavailable++; }
      }
     }
     notes.sort((a,b) -> Long.compare(b.saved, a.saved));
     List<Map<String, Object>> recent = new ArrayList<>();
     for (int i = 0; i < Math.min(20, notes.size()); i++) recent.add(notes.get(i).result(false, false));
     Map<String, Object> result = base("ok"); result.put("notes", recent); result.put("unavailable_count", unavailable); result.put("total", notes.size()); return result;
    }
   } catch (Exception e) { return error("unavailable", "store_unavailable", "Private notes could not be read"); }
  }
 }

 private Path directory() throws IOException {
  if (privateRoot == null || !privateRoot.isDirectory()) throw new IOException("Private root unavailable");
  Path parent = privateRoot.toPath().toRealPath(), dir = parent.resolve(NAMESPACE);
  if (!Files.exists(dir, LinkOption.NOFOLLOW_LINKS)) Files.createDirectory(dir, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
  if (Files.isSymbolicLink(dir) || !Files.isDirectory(dir, LinkOption.NOFOLLOW_LINKS)) throw new IOException("Unsafe private directory");
  Files.setPosixFilePermissions(dir, PosixFilePermissions.fromString("rwx------"));
  // Persist the directory entry as well as each note's later rename.
  syncDirectory(parent);
  return dir;
 }
 private static void privateFile(Path file) throws IOException { Files.setPosixFilePermissions(file, PosixFilePermissions.fromString("rw-------")); }
 private static void syncDirectory(Path dir) throws IOException {
  try (FileChannel channel = FileChannel.open(dir, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) { channel.force(true); }
 }
 private static Note read(Path path) throws IOException, Corrupt {
  if (Files.isSymbolicLink(path) || !Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) throw new Corrupt();
  long size = Files.size(path);
  if (size < 40 || size > MAX_BYTES) throw new Corrupt();
  ByteArrayOutputStream bytes = new ByteArrayOutputStream();
  try (InputStream in = Files.newInputStream(path, LinkOption.NOFOLLOW_LINKS)) {
   byte[] chunk = new byte[4096];
   for (int n; (n = in.read(chunk)) != -1;) { if (bytes.size() + n > MAX_BYTES) throw new Corrupt(); bytes.write(chunk, 0, n); }
  }
  byte[] all = bytes.toByteArray();
  if (all.length < 40) throw new Corrupt();
  byte[] payload = Arrays.copyOf(all, all.length - 32), checksum = Arrays.copyOfRange(all, all.length - 32, all.length);
  if (!MessageDigest.isEqual(digest(payload), checksum)) throw new Corrupt();
  try (DataInputStream in = new DataInputStream(new ByteArrayInputStream(payload))) {
   if (in.readInt() != MAGIC || in.readInt() != VERSION) throw new Corrupt();
   String task = id(string(in)), workspace = id(string(in));
   String controllerTask = controllerTask(string(in));
   String title = text(string(in), MAX_TITLE), body = text(string(in), MAX_BODY), hash = hash(string(in));
   long saved = in.readLong();
   if (in.available() != 0 || saved <= 0 || !task.equals(controllerTask) || !hash.equals(sha256(body))) throw new Corrupt();
   return new Note(task, workspace, controllerTask, title, body, hash, saved);
  } catch (Invalid | EOFException | CharacterCodingException e) { throw new Corrupt(); }
 }
 private static byte[] encode(Note note) throws IOException {
  ByteArrayOutputStream bytes = new ByteArrayOutputStream();
  try (DataOutputStream out = new DataOutputStream(bytes)) {
   out.writeInt(MAGIC); out.writeInt(VERSION);
   for (String value : Arrays.asList(note.task, note.workspace, note.controllerTask, note.title, note.body, note.hash)) {
    byte[] text = value.getBytes(StandardCharsets.UTF_8); out.writeInt(text.length); out.write(text);
   }
   out.writeLong(note.saved);
  }
  byte[] payload = bytes.toByteArray(); bytes.write(digest(payload)); return bytes.toByteArray();
 }
 private static String string(DataInputStream in) throws IOException, Corrupt {
  int size = in.readInt(); if (size < 0 || size > MAX_BYTES || size > in.available()) throw new Corrupt();
  byte[] bytes = new byte[size]; in.readFully(bytes);
  return StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
      .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
 }
 static String sha256(String body) {
  byte[] bytes = digest(body.getBytes(StandardCharsets.UTF_8)); StringBuilder hex = new StringBuilder();
  for (byte b : bytes) hex.append(String.format(Locale.ROOT, "%02x", b & 255)); return hex.toString();
 }
 private static byte[] digest(byte[] bytes) {
  try { return MessageDigest.getInstance("SHA-256").digest(bytes); } catch (Exception e) { throw new IllegalStateException("SHA-256 unavailable"); }
 }
 private static String id(Object value) throws Invalid {
  if (!(value instanceof String) || !((String) value).matches("[A-Za-z0-9][A-Za-z0-9_-]{0,127}")) throw new Invalid("Expected bounded opaque task and workspace IDs");
  return (String) value;
 }
 private static String controllerTask(Object value) throws Invalid {
  if (!(value instanceof String) || !((String) value).matches("[A-Za-z0-9._:-]{1,128}")) throw new Invalid("Expected bounded controller task ID");
  return (String) value;
 }
 private static String hash(Object value) throws Invalid {
  if (!(value instanceof String) || !((String) value).matches("[0-9a-f]{64}")) throw new Invalid("Expected lowercase SHA-256");
  return (String) value;
 }
 private static String text(Object value, int limit) throws Invalid {
  if (!(value instanceof String)) throw new Invalid("Expected nonempty note text");
  String text = (String) value;
  if (text.isEmpty() || text.codePointCount(0, text.length()) > limit) throw new Invalid("Note text exceeds bounds or is empty");
  for (int i = 0; i < text.length(); i++) {
   char c = text.charAt(i);
   if (Character.isHighSurrogate(c)) { if (++i >= text.length() || !Character.isLowSurrogate(text.charAt(i))) throw new Invalid("Invalid Unicode note text"); }
   else if (Character.isLowSurrogate(c)) throw new Invalid("Invalid Unicode note text");
  }
  return text;
 }
 private static void only(Map<String, Object> params, String... keys) throws Invalid {
  if (params == null || !params.keySet().equals(new HashSet<>(Arrays.asList(keys)))) throw new Invalid("Unexpected or missing private-note arguments");
 }
 private static Map<String, Object> base(String status) {
  Map<String, Object> out = new LinkedHashMap<>(); out.put("status", status); out.put("version", VERSION); out.put("namespace", NAMESPACE); return out;
 }
 static Map<String, Object> error(String status, String code, String reason) {
  Map<String, Object> out = base(status); out.put("verified", false); out.put("code", code); out.put("reason", reason); return out;
 }
 private static final class Invalid extends Exception { Invalid(String message) { super(message); } }
 private static final class Corrupt extends Exception {}
 private static final class Note {
  final String task, workspace, controllerTask, title, body, hash; final long saved;
  Note(String task, String workspace, String controllerTask, String title, String body, String hash, long saved) {
   this.task = task; this.workspace = workspace; this.controllerTask = controllerTask; this.title = title; this.body = body; this.hash = hash; this.saved = saved;
  }
  Map<String, Object> result(boolean put, boolean created) {
   Map<String, Object> out = base("verified"); out.put("verified", true); out.put("task_id", task); out.put("workspace_id", workspace);
   out.put("controller_task_id", controllerTask); out.put("note_id", "life-" + task); out.put("title", title); out.put("body", body); out.put("sha256", hash);
   out.put("saved_at", saved / 1000.0); if (put) out.put("created", created); return out;
  }
 }
}
