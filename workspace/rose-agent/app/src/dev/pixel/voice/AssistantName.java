package dev.pixel.voice;

import java.text.Normalizer;

/** The conversational identity, not the RoseOS or installed app brand. No HTML or commands. */
final class AssistantName {
    static final String DEFAULT = "Rose";
    private AssistantName() {}

    /** Matches the producer's bounded name grammar. Null means malformed, never a request to erase. */
    static String valid(Object raw) {
        if (!(raw instanceof String)) return null;
        String s = Normalizer.normalize((String) raw, Normalizer.Form.NFC).trim().replaceAll("\\s+", " ");
        if (s.isEmpty() || s.codePointCount(0, s.length()) > 24 || s.split(" ").length > 3) return null;
        boolean letter = false;
        for (int i = 0; i < s.length();) {
            int c = s.codePointAt(i); i += Character.charCount(c);
            if (Character.isLetter(c)) letter = true;
            else if (c != ' ' && c != '-' && c != '\'' && c != '’' && c != '.') return null;
        }
        return letter ? s : null;
    }

    /** Only call for agent-facing copy, never app/package, OS, updater or pairing labels. */
    static String inText(String text, String name) {
        return text.replaceAll("\\bRose\\b", java.util.regex.Matcher.quoteReplacement(name));
    }
}
