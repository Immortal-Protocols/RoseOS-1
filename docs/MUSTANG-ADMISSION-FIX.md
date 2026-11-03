# Mustang updater-route admission correction

Unsigned run [41714573501](https://github.com/Immortal-Protocols/RoseOS-1/actions/runs/41714573501)
failed at `admit` at 2026-11-03T21:04:42Z. The job log records that the route
must contain exactly the eight fields accepted by
`roseos.mustang-updater-route.v1`. The source JSON contained a ninth, explanatory
`basis` field. Compilation and signing had not started.

Remove that extra field while preserving every functional route value and the
approval reference. This is device-qualified discovery configuration only: no
feed or public release is created, and delivery remains private. Validate the
same route in the existing hosted `describe` guard, before builder startup, and
in the unsigned runtime before creating an attempt directory.

The failed run and its allocation remain preserved. A later attempt must use
the corrected source and a fresh run root and allocation; it must not adopt or
resume this attempt. The shared workflow route and FIFO remain unchanged.
Cleanup observed `TERMINATED` at 2026-11-03T21:05:42Z and completed successfully
at 21:05:44Z. The one approved bootstrap run subsequently passed its guard and
controller and restarted the same instance (`685707771105568194`), observed
`RUNNING` with last start 21:06:29Z. Its key-generation/import outcome was still
pending when this correction was prepared. The uploaded public
receipt archive is artifact `13270478126`, SHA-256
`d098d7aa5f04a6bc07a1892db1c0c91a315336fa90a210e76376ee47d3c9ffeb`,
15,101 bytes, expiring 2026-11-10T21:04:43Z. This worker's local archive
download returned Forbidden and was not retried; diagnosis uses the completed
job log and matching source schema.

Review is limited to source and configuration parsing. Focused tests, existing
device regression, required CI and the other sprint verification omissions
remain undone. No output, signature, installer or physical-device acceptance
is claimed.
