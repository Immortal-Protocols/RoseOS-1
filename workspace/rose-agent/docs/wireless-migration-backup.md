# Wireless migration backup

Temporary migration tool, added on October 15, 2026, at the user's request to
back up the Pixel over its existing internet connection. No LAN reachability,
ADB or USB is needed. No model processes the exported file contents.

`migration_backup_open` opens the explicit native export screen;
`migration_backup_status` returns progress and whether Android grants all-files
access. The native screen opens Android's special file-access permission panel
and has an explicit Start backup button. This permission cannot access other
apps' private storage. Remove it after completing the export.

The exporter streams a ZIP over the already paired and certificate-pinned HTTPS
connection to `/migration-backup/upload`. The server's usual pairing-token check
precedes this handler. The route additionally requires an unexpired private
`~/.config/pixel-voice/migration-backup-window.json` with a UUID `id` and an
`expiresAt` epoch time. It refuses an overlapping upload or overwriting a
received archive. Its default capacity is 64 GiB; crossing that fails the
transfer and removes the partial file rather than claiming success.

Archives go into `~/.config/pixel-os/device-backups/wireless-20261015/` with mode
0600. Only shared files outside the protected `Android` subtree and Pixel
Voice's own files, databases and preferences are included. Symbolic links are
skipped. Installed package versions, individual file hashes, source files
changed during copying and access errors are recorded in the archive's private
manifest. Reading live app files is not an atomic database backup.

Success requires matching stream SHA-256 on the phone and the Mac. Then run
`python3 pixel-os/scripts/verify-wireless-backup.py ARCHIVE.zip` to read every
entry and compare its length and SHA-256 with the manifest, check safe paths,
and verify the receiver receipt. This does not extract personal contents to
the workspace or send them to a model. A valid archive with skipped/changed
files must still report those gaps explicitly.

Close the receive window by removing `migration-backup-window.json`. Revoke
the temporary all-files access in Android, leave wireless debugging off, and
return the phone to its launcher. Do not delete the verified backup.

Rollback sources and the pre-change v182 installer are private under
`~/.config/pixel-os/device-backups/preparation-20261015/wireless-backup-rollback/`.
Normal rollback should retain phone app data: remove the two backup command
handlers and route, revert the special permission/activity, and build a higher
APK version rather than uninstalling Rose Agent.

Validation: the receive-window, exact-byte/checksum, duplicate upload and size
failure tests are in `server/migration-backup.test.mjs`. Existing push/poll
delivery tests also passed. A physical export must be verified separately;
unit tests alone do not confirm a completed backup.
