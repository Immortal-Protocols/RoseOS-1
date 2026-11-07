/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package dev.pixel.rose.keyboard;

import android.inputmethodservice.InputMethodService;
import android.view.View;
import android.view.inputmethod.EditorInfo;

/**
 * RoseOS (1M, F10): the Rose keyboard, the selected design (prototypes/rose-keyboard, 818a032) as an input method.
 * The service only connects Android's input-method lifecycle to one KeyboardSession and its view. No microphone key,
 * no network and no permission beyond the input-method binding; voice and the microphone-input mute stay on the chin.
 */
public final class RoseKeyboardService extends InputMethodService {
    private final KeyboardSession session = new KeyboardSession(this::getCurrentInputConnection, () -> requestHideSelf(0));

    @Override
    public View onCreateInputView() {
        RoseKeyboardView view = new RoseKeyboardView(this, session);
        session.attach(view);
        return view;
    }

    @Override
    public void onStartInputView(EditorInfo info, boolean restarting) {
        super.onStartInputView(info, restarting);
        session.start(info);
    }

    @Override
    public void onUpdateSelection(int oldSelStart, int oldSelEnd, int newSelStart, int newSelEnd,
            int candidatesStart, int candidatesEnd) {
        super.onUpdateSelection(oldSelStart, oldSelEnd, newSelStart, newSelEnd, candidatesStart, candidatesEnd);
        session.selectionChanged(newSelStart, newSelEnd);
    }
}
