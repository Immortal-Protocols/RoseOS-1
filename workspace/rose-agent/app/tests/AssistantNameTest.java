package dev.pixel.voice;

public final class AssistantNameTest {
    static int checks;
    static void eq(Object expected, Object actual) {
        checks++;
        if (!java.util.Objects.equals(expected, actual)) throw new AssertionError(expected + " != " + actual);
    }
    public static void main(String[] args) {
        eq("Nova", AssistantName.valid("  Nova  "));
        eq("Elodie-Rose", AssistantName.valid("Elodie-Rose"));
        eq("Étoile", AssistantName.valid("E\u0301toile"));
        eq("小玫", AssistantName.valid("小玫"));
        eq("O’Rose", AssistantName.valid("O’Rose"));
        for (Object bad : new Object[]{null, 42, "", "...", "<b>Nova</b>", "Nova1", "a b c d", "abcdefghijklmnopqrstuvwxy", "Nova\u202E"}) eq(null, AssistantName.valid(bad));
        eq("Nova speaking", AssistantName.inText("Rose speaking", "Nova"));
        eq("Nova: connected, microphone on, Nova speaking", AssistantName.inText("Rose: connected, microphone on, Rose speaking", "Nova"));
        eq("RoseOS", AssistantName.inText("RoseOS", "Nova"));
        eq("Rose's microphone", AssistantName.inText("Rose's microphone", "Rose"));
        System.out.println("AssistantNameTest: " + checks + " checks passed");
    }
}
