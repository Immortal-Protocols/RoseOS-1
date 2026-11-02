package dev.pixel.backgroundcontrol;

import dev.pixel.backgroundcontrol.ViewerPresentation.Mode;

public final class ViewerPresentationTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        // AUTO-PIP-OFF-330: automatic picture-in-picture is off by default.
        expect(!ViewerPresentation.AUTOMATIC_PIP, "Automatic picture-in-picture is off by default");

        // An explicit Watch (Rose's apps, an overview selection) is full screen, whatever was showing before.
        expect(ViewerPresentation.forEntry(true, null) == Mode.EXPANDED, "A new explicit Watch opens full screen");
        expect(ViewerPresentation.forEntry(true, Mode.PIP) == Mode.EXPANDED, "An explicit Watch over the floating view expands it");
        expect(ViewerPresentation.forEntry(true, Mode.EXPANDED) == Mode.EXPANDED, "A repeated Watch stays full screen");
        expect(!ViewerPresentation.entersPip(Mode.EXPANDED), "Full screen never asks for picture-in-picture");

        // A fresh viewer with no Watch (the session notification, any unbound launch) opens full screen, not floating.
        expect(ViewerPresentation.forEntry(false, null) == Mode.EXPANDED, "An unbound entry no longer floats by default");
        expect(ViewerPresentation.forEntry(false, Mode.EXPANDED) == Mode.EXPANDED, "An unbound reopen keeps full screen");
        // A viewer still floating from before is brought full screen by the next entry, never kept floating.
        expect(ViewerPresentation.forEntry(false, Mode.PIP) == Mode.EXPANDED, "An unbound reopen of an old floating view expands it");
        expect(ViewerPresentation.entersPip(Mode.PIP), "Only the floating presentation asks for picture-in-picture");

        // Recreation, including state saved by an earlier build ("PIP", or nothing from before presentations were
        // saved), comes back full screen and does not silently reopen the floating window.
        expect(ViewerPresentation.restore("EXPANDED") == Mode.EXPANDED, "A recreated full-screen viewer stays full screen");
        for (String saved : new String[] {"PIP", null, "", "expanded", "FULL"})
            expect(ViewerPresentation.restore(saved) == Mode.EXPANDED, "Restore defaults to full screen: " + saved);
        for (String saved : new String[] {"PIP", null, "", "EXPANDED"})
            expect(!ViewerPresentation.entersPip(ViewerPresentation.restore(saved)), "Recreation never floats: " + saved);
        System.out.println("ViewerPresentationTest: " + checks + " checks passed");
    }
}
