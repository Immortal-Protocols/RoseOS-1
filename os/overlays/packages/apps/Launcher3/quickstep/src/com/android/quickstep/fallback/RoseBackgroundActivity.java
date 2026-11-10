/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

/** Bounded, boot-scoped producer evidence. No controller-ready or voice inference. Pure host-testable model. */
final class RoseBackgroundActivity {
    static final String KEY = "rose_background_activity_v1";
    private String state = "";
    private long until, began = -1, settleAt = -1;
    private float settleFrom;
    private boolean working;

    void accept(String value, int boot, long now) {
        state = ""; until = 0;
        if (value == null || value.length() > 80 || boot < 0) return;
        String[] fields = value.split(":", -1);
        try {
            if (fields.length != 3 || Integer.parseInt(fields[0]) != boot) return;
            long deadline = Long.parseLong(fields[2]);
            if (deadline < now || deadline - now > 3000) return;
            if (!"working".equals(fields[1]) && !"terminal".equals(fields[1])) return;
            state = fields[1]; until = deadline;
        } catch (NumberFormatException ignored) { }
    }

    float frame(long now, boolean motion) {
        boolean fresh = now >= 0 && until >= now && until - now <= 3000;
        boolean next = fresh && "working".equals(state);
        if (!motion) {
            working = false; began = settleAt = -1;
            return 0;
        }
        if (next) {
            if (!working) began = now;
            settleAt = -1;
        } else if (working) {
            if (fresh && "terminal".equals(state)) {
                settleFrom = offset(now); settleAt = now;
            } else settleAt = -1;
        }
        working = next;
        if (working) return offset(now);
        if (!fresh || !"terminal".equals(state)) settleAt = -1;
        if (settleAt >= 0 && now >= settleAt && now - settleAt < 240)
            return settleFrom * (1f - (float)(now - settleAt) / 240);
        settleAt = -1;
        return 0;
    }
    boolean animating() { return working || settleAt >= 0; }
    private float offset(long now) {
        return began < 0 || now < began ? 0 : .6f * (float)Math.sin(2 * Math.PI * ((now - began) % 2400) / 2400);
    }
}
