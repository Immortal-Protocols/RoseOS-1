"""Pair a phone with the running Rose server from the terminal.

    .venv/bin/python -m rose.pair                 # show a QR code, wait until a phone uses it
    .venv/bin/python -m rose.pair --list          # paired phones
    .venv/bin/python -m rose.pair --revoke ID     # remove one (an id prefix is enough)

Talks to the dashboard's localhost pairing API (http://127.0.0.1:8091/api/pair/*);
Rose must be running (``python -m rose.app``, remote listener on). Scan the code
with Rose Agent (menu: Pair with Mac) or the phone's camera app.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
import urllib.error
import urllib.request

from . import qr


class ApiError(Exception):
    pass


def api(base: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read()).get("error") or exc.reason
        except ValueError:
            msg = exc.reason
        raise ApiError(f"{msg} (HTTP {exc.code})") from None
    except urllib.error.URLError as exc:
        raise ApiError(f"cannot reach Rose at {base} ({exc.reason}); is python -m rose.app running?") from None


def when(ms: int | None) -> str:
    if not ms:
        return "never"
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def list_devices(base: str) -> int:
    devices = api(base, "/api/pair/devices")["devices"]
    if not devices:
        print("No paired phones.")
        return 0
    for d in devices:
        state = "removed" if d["revoked"] else "paired"
        print(f"{d['id'][:16]}  {d['name']}  ({d['model'] or 'unknown model'})  {state} {when(d['pairedAt'])}, "
              f"last seen {when(d['lastSeen'])}" + (f" from {d['lastAddress']}" if d.get("lastAddress") else ""))
    return 0


def revoke(base: str, prefix: str) -> int:
    prefix = prefix.strip().lower()
    matches = [d for d in api(base, "/api/pair/devices")["devices"] if d["id"].startswith(prefix)]
    if len(matches) != 1:
        print(f"{'No' if not matches else 'More than one'} paired phone matches {prefix!r}.", file=sys.stderr)
        return 1
    api(base, "/api/pair/revoke", {"deviceId": matches[0]["id"]})
    print(f"Removed {matches[0]['name']}. It can no longer reach Rose until it is paired again.")
    return 0


def pair(base: str, wait: bool) -> int:
    inv = api(base, "/api/pair/new", {})
    print()
    print(qr.terminal(inv["uri"], border=2))
    print()
    print(f"Pair with {inv['serverName']}: scan this with Rose Agent (menu: Pair with Mac).")
    print(f"Addresses: {', '.join(inv['hosts'])}  (port {inv.get('port', '')})")
    mac_id = inv.get("pin", "")[:16]
    print(f"Mac ID: {' '.join(mac_id[i:i + 4] for i in range(0, len(mac_id), 4))}  (the phone shows the same ID before you tap Pair)")
    print(f"Code valid until {datetime.datetime.fromtimestamp(inv['expiresAt'] / 1000):%H:%M:%S}, once.")
    if not wait:
        return 0
    try:
        while True:
            time.sleep(1)
            st = api(base, "/api/pair/status")
            if st.get("pairedDeviceId"):
                print(f"Paired with {st.get('pairedDeviceName') or st['pairedDeviceId'][:16]}.")
                return 0
            if not st.get("pending"):
                reason = {"locked": "too many wrong attempts", "expired": "it expired"}.get(st.get("state"),
                                                                                         "a newer code replaced it")
                print(f"The code is no longer valid ({reason}). Run this again for a new one.", file=sys.stderr)
                return 1
    except KeyboardInterrupt:
        print()
        return 130


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m rose.pair", description="Pair a phone with the running Rose server.")
    p.add_argument("--url", default="http://127.0.0.1:8091", help="the dashboard (default %(default)s)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--list", action="store_true", help="list paired phones")
    g.add_argument("--revoke", metavar="DEVICE_ID", help="remove a paired phone (id or unique prefix)")
    p.add_argument("--no-wait", action="store_true", help="print the code and exit")
    args = p.parse_args(argv)
    base = args.url.rstrip("/")
    try:
        if args.list:
            code = list_devices(base)
        elif args.revoke:
            code = revoke(base, args.revoke)
        else:
            code = pair(base, wait=not args.no_wait)
    except ApiError as exc:
        print(f"rose.pair: {exc}", file=sys.stderr)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
