package dev.pixel.voice;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Map;

/** Nov 7 plan 1O: what a customization command reports. "verified" is storage only; Home's effective column count is
 * reported only once Home has drawn that exact revision; argument conversion refuses anything but plain values. */
public final class CustomizationOutcomeTest {
 static int checks;
 static void expect(boolean c, String m) { checks++; if (!c) throw new AssertionError(m); }

 public static void main(String[] args) {
  // status: storage, never visibility.
  expect(CustomizationOutcome.status(CustomizationConfig.STATE, false).equals("ok"), "state answers ok");
  expect(CustomizationOutcome.status(CustomizationConfig.HOME_COLUMNS, true).equals("verified"), "stored is verified");
  expect(CustomizationOutcome.status(CustomizationConfig.FONT, false).equals("uncertain"), "unconfirmed store is uncertain");

  // The review case: 6 requested on a 336dp inset width, Home can fit only 4.
  CustomizationConfig six = CustomizationConfig.DEFAULTS.apply(CustomizationConfig.HOME_COLUMNS, Collections.<String, Object>singletonMap("columns", 6L));
  int drawn = CustomizationConfig.effectiveHomeColumns(six.homeColumns, 336);
  expect(six.homeColumns == 6 && drawn == 5, "6 requested, 5 fit a phone: " + drawn);
  Map<String, Object> before = CustomizationOutcome.home(six.revision, CustomizationOutcome.NEVER_APPLIED, 0);
  expect(before.get("applied") == Boolean.FALSE && before.get("effectiveColumns") == MiniJson.NULL && before.get("appliedRevision") == MiniJson.NULL
    && CustomizationOutcome.HOME_PENDING_NOTE.equals(before.get("note")), "never drawn: no column claim, a note");
  Map<String, Object> stale = CustomizationOutcome.home(six.revision, six.revision - 1, 4);
  expect(stale.get("applied") == Boolean.FALSE && stale.get("effectiveColumns") == MiniJson.NULL && Long.valueOf(six.revision - 1).equals(stale.get("appliedRevision")),
    "Home drew an older revision: its columns are not this change's");
  Map<String, Object> ahead = CustomizationOutcome.home(0, 5, 4);
  expect(ahead.get("applied") == Boolean.FALSE && ahead.get("effectiveColumns") == MiniJson.NULL, "a mark above a reset revision is not proof");
  Map<String, Object> shown = CustomizationOutcome.home(six.revision, six.revision, drawn);
  expect(shown.get("applied") == Boolean.TRUE && Long.valueOf(5).equals(shown.get("effectiveColumns")) && !shown.containsKey("note"),
    "drawn: Home's own count (5), not the preference (6)");
  expect(CustomizationOutcome.home(3, 3, 0).get("effectiveColumns") == MiniJson.NULL, "a drawn revision without a column report claims no count");
  String json = CustomizationConfig.json(shown);
  expect(json.equals("{\"appliedRevision\":" + six.revision + ",\"applied\":true,\"effectiveColumns\":5}"), json);
  expect(CustomizationConfig.json(before).contains("\"effectiveColumns\":null"), CustomizationConfig.json(before));

  // Plan 1E: the theme counts as shown on the system surfaces only when SystemUI acknowledged exactly it.
  expect(Boolean.TRUE.equals(CustomizationOutcome.system("glacier", "glacier").get("applied"))
    && !CustomizationOutcome.system("glacier", "glacier").containsKey("note"), "SystemUI shows the stored theme");
  expect(Boolean.FALSE.equals(CustomizationOutcome.system("glacier", "oxblood").get("applied"))
    && CustomizationOutcome.SYSTEM_PENDING_NOTE.equals(CustomizationOutcome.system("glacier", "oxblood").get("note")), "another theme shown is not applied");
  expect(Boolean.FALSE.equals(CustomizationOutcome.system("glacier", null).get("applied"))
    && CustomizationOutcome.system("glacier", null).get("shown") == MiniJson.NULL, "no acknowledgment is not applied");
  expect(CustomizationConfig.json(CustomizationOutcome.system("oxblood", "oxblood")).equals("{\"shown\":\"oxblood\",\"applied\":true}"),
    CustomizationConfig.json(CustomizationOutcome.system("oxblood", "oxblood")));

  // Argument conversion.
  expect(Long.valueOf(5).equals(CustomizationOutcome.plain(5)) && Long.valueOf(5).equals(CustomizationOutcome.plain(5L))
    && Long.valueOf(5).equals(CustomizationOutcome.plain(new BigInteger("5"))) && Long.valueOf(5).equals(CustomizationOutcome.plain(new BigDecimal("5.0"))),
    "integral numbers become Long");
  expect(Double.valueOf(5.5).equals(CustomizationOutcome.plain(new BigDecimal("5.5"))) && Double.valueOf(5.5).equals(CustomizationOutcome.plain(5.5)), "fractions become Double");
  expect("serif".equals(CustomizationOutcome.plain("serif")) && Boolean.TRUE.equals(CustomizationOutcome.plain(true)), "strings and booleans pass");
  for (Object bad : new Object[]{null, MiniJson.NULL, new ArrayList<Object>(), Collections.emptyMap(), new BigInteger("123456789012345678901234567890"), 'c'}) {
   boolean refused = false;
   try { CustomizationOutcome.plain(bad); } catch (IllegalArgumentException e) { refused = "Unsupported argument type".equals(e.getMessage()); }
   expect(refused, "refused: " + bad);
  }
  // A converted fraction still fails the model's integer rule.
  boolean rejected = false;
  try { CustomizationConfig.DEFAULTS.apply(CustomizationConfig.HOME_COLUMNS, Collections.singletonMap("columns", CustomizationOutcome.plain(new BigDecimal("5.5")))); }
  catch (IllegalArgumentException e) { rejected = true; }
  expect(rejected, "5.5 columns rejected after conversion");
  System.out.println("CustomizationOutcomeTest: " + checks + " checks passed");
 }
}
