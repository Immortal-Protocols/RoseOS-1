# APP-REMOVE-001-Q — advisory removal and core-preservation cases

QA-only and offline. `removal.test.mjs` loads the real `index.html` through the maintained
`tests/web-ui-harness.mjs`, answers backend calls with fixtures (no backend, network, device or
preference access), and compares source against the pre-removal reference `QA_BASE_REF`
(default `eca1fef`).

    node --test qa/app-remove-001/removal.test.mjs                      # this checkout
    QA_SRC=/path/to/workspace/rose-agent node --test qa/app-remove-001/removal.test.mjs

R1–R4 are removal cases. They fail on pre-removal source for the named reason; R3 and R4 also need
Product's `MainActivity` half. P1–P6 are preservation cases and pass both before and after. This is
advisory evidence, not a merge, build or release gate.

Baseline reconciliation: `QA_MAINACTIVITY_BASE_REF` compares `MainActivity` with its own pre-change
parent. For the merged pair, use PR #89's parent `27c7e57`. That parent already contains OS #83 Home
reuse (`c234714`: `ACTION_MAIN` 2→3, `CATEGORY_HOME` 1→2), which is not part of the removal. The
reference applies only to trees that descend from it.
