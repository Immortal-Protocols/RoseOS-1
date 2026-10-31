package dev.pixel.backgroundcontrol;

import dev.pixel.backgroundcontrol.ViewerPresentation.Mode;

public final class ViewerPresentationTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        // An explicit Watch (Rose's apps, an overview selection) is full screen, whatever was showing before.
        expect(ViewerPresentation.forEntry(true, null) == Mode.EXPANDED, "A new explicit Watch opens full screen");
        expect(ViewerPresentation.forEntry(true, Mode.PIP) == Mode.EXPANDED, "An explicit Watch over the floating view expands it");
        expect(ViewerPresentation.forEntry(true, Mode.EXPANDED) == Mode.EXPANDED, "A repeated Watch stays full screen");
        expect(!ViewerPresentation.entersPip(Mode.EXPANDED), "Full screen never asks for picture-in-picture");

        // The fallback stays: the automatic show and an unbound reopen are picture-in-picture.
        expect(ViewerPresentation.forEntry(false, null) == Mode.PIP, "The automatic show keeps the floating view");
        expect(ViewerPresentation.forEntry(false, Mode.PIP) == Mode.PIP, "An unbound reopen of the floating view stays floating");
        expect(ViewerPresentation.entersPip(Mode.PIP), "The floating view asks for picture-in-picture");
        // An unbound reopen (the notification) never shrinks a viewer the user opened full screen.
        expect(ViewerPresentation.forEntry(false, Mode.EXPANDED) == Mode.EXPANDED, "An unbound reopen keeps full screen");

        // Recreation restores what was shown; anything unrecognised falls back to the floating view.
        expect(ViewerPresentation.restore("EXPANDED") == Mode.EXPANDED, "A recreated full-screen viewer stays full screen");
        for (String saved : new String[] {"PIP", null, "", "expanded", "FULL"})
            expect(ViewerPresentation.restore(saved) == Mode.PIP, "Restore falls back to the floating view: " + saved);
        System.out.println("ViewerPresentationTest: " + checks + " checks passed");
    }
}
