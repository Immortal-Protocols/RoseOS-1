"""Project 1 app builder: phone request -> durable build job -> signed APK (docs/APP-BUILDER.md).

- ``ledger``: projects, versions and jobs in one private SQLite file. The package, signer and versionCode identity
  live here, never in generated source.
- ``template``: the framework-only skeleton generated code is written into, and the checks that keep identity out of
  its reach.
- ``toolchain``: the pinned aapt/javac/d8/zipalign build and ``scripts/sign-apk.py`` signing, run with a scrubbed
  environment, a timeout and cancellation.
- ``service``: the builder API v1 calls and the single worker that runs jobs.
- ``generator``: the Codex app writer (an ``app_build`` session on the existing bridge), the worker's ``generate``.

Nothing here is wired to a listener or enabled on a host: routes, the Codex generation thread, key creation and the
build host each need their own authorization.
"""
