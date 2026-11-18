"""Offline Linux build facilities, supplied explicitly by the builder owner.

This is not the standalone service in app_builder.host. Nothing constructs these
providers by default, provisions a host, or downloads a toolchain. The host must
keep its toolchain, crate cache and system runtime immutable to jobs, including
while pins are checked and commands run. A digest is a supplied trust decision,
not one learned from the same installation at build time.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

# BubblewrapIsolation lives in rose.app_builder.isolation, shared by every build that runs project code;
# it is re-exported here, where hosts and tests have always imported it.
from ..isolation import BubblewrapIsolation, BuildFailed, Isolation, _canonical  # noqa: F401
from ..server_build import RustTools


def toolchain_digest(root: Path) -> str:
    """Pin format v1: SHA-256 of sorted JSON-line [path, mode, content-sha256]
    records, including directories (digest null) and the root (path ""). Paths
    are POSIX relative names, modes are stat.S_IMODE integers. JSON uses ASCII,
    compact separators and a trailing newline per record. Links/special files
    are refused, not followed; nothing in the tree is executed.
    """
    root = _canonical(root)
    if not root.is_dir():
        raise ValueError('a plain toolchain directory is required')
    records = []
    for folder, dirs, files in os.walk(root, followlinks=False, onerror=_walk_error):
        for path in [Path(folder), *(Path(folder) / n for n in files)]:
            mode = path.lstat().st_mode
            if stat.S_ISREG(mode):
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1 << 20), b''):
                        digest.update(block)
                content = digest.hexdigest()
            elif stat.S_ISDIR(mode):
                content = None
            else:
                raise ValueError('toolchain links and special files are refused')
            records.append((path.relative_to(root).as_posix() if path != root else '',
                            stat.S_IMODE(mode), content))
        # os.walk does not visit symlink directories; reject those explicitly.
        if any(not stat.S_ISDIR((Path(folder) / n).lstat().st_mode) for n in dirs):
            raise ValueError('toolchain links and special files are refused')
    digest = hashlib.sha256(b'rose-rust-toolchain-v1\n')
    for record in sorted(records):
        digest.update((json.dumps(record, ensure_ascii=True, separators=(',', ':')) + '\n').encode('ascii'))
    return digest.hexdigest()


def _walk_error(error):
    raise error


@dataclass(kw_only=True)
class PinnedRustTools(RustTools):
    """An unpacked, non-proxy RustTools whose entire tree matches an external pin.
    Rechecked on every available() call; no cached successful verification.
    """
    expected_sha256: str

    def available(self) -> str | None:
        if not isinstance(self.expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.expected_sha256):
            return 'The builder has no valid externally supplied Rust toolchain pin.'
        try:
            root = _canonical(self.toolchain)
            if toolchain_digest(root) != self.expected_sha256:
                return 'The builder\'s Rust toolchain does not match its supplied pin.'
            missing = super().available()
            if missing:
                return missing
            if any(not os.access(p, os.X_OK) or p.stat().st_mode & (stat.S_ISUID | stat.S_ISGID)
                   for p in (self.cargo, self.rustc)):
                return 'The builder\'s pinned Rust tools must be executable and unprivileged.'
        except (OSError, ValueError, RuntimeError):
            return 'The builder\'s Rust toolchain could not be verified as a plain pinned tree.'
        return None
