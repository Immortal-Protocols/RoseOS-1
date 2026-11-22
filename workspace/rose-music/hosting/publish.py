#!/usr/bin/env python3
"""Publish the Rose Music library to its Chariot agent.

    python3 publish.py --songs ~/rose-music-songs --agent rose-music [--dry-run]

The songs folder holds NN.mp3, covers/NN.jpg and generation.json (from
../songs/generate.py and covers.py). This builds a static site:

    health                 "ok", Chariot's health path
    catalog.json           the library (schema rose-music-catalog/v1)
    tracks/NN.mp3          audio
    covers/NN.jpg          1024px art; covers/NN.thumb.jpg 320px

and serves it from the agent with BusyBox httpd on 127.0.0.1:8090 behind
Chariot's agent-app gateway (https://agent-<id>.apps.chariots.sh). The site is
uploaded to an incoming folder and swapped in whole; the server is restarted
with Chariot's published launcher and app.json is republished. Re-running is
safe and also restarts the server after the agent has been woken.

Needs: the Chariot CLI logged in, ffmpeg/ffprobe on PATH. Nothing secret is
read or printed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8090
PEAKS = 160
ARTIST = "Rose Music Ensemble"
ALBUM = "Glass Orchard"

# Chariot's agent-app launcher, verbatim (ProtocolsBackend 8dc39b86 chariot/scripts/launch_agent_app.sh),
# the same text and sha256 the app builder pins in prototypes/voice-phone-demo/rose/app_builder/app_host.py.
LAUNCHER_SHA256 = "584d15de31c63cdd94ddec3bf73d654f0052fdface011fec33d5d3d7934f322a"


def launcher() -> str:
    src = open(os.path.join(HERE, "../../../prototypes/voice-phone-demo/rose/app_builder/app_host.py")).read()
    text = src.split("LAUNCHER = '''", 1)[1].split("'''", 1)[0]
    if hashlib.sha256(text.encode()).hexdigest() != LAUNCHER_SHA256:
        sys.exit("launcher text does not match its pinned sha256")
    return text


START = """#!/chariot/bin/sh
here=$(cd "$(/chariot/bin/dirname "$0")" && pwd)
exec /chariot/bin/httpd -f -p 127.0.0.1:${PORT:-%d} -h "$here/www" -c "$here/httpd.conf"
""" % PORT

HTTPD_CONF = ".mp3:audio/mpeg\n.json:application/json\n.jpg:image/jpeg\n"

# Runs in the agent as `sh -s`. Every tool by its /chariot/bin path.
SWAP = r"""
set -eu
B=/chariot/bin
app="$HOME/workspace/.chariot-app"
cd "$app"
test -d incoming/www || { echo "missing=incoming"; exit 3; }
$B/printf '%s' "$START_B64" | $B/base64 -d > start.tmp && $B/chmod 700 start.tmp && $B/mv -f start.tmp start
$B/printf '%s' "$CONF_B64" | $B/base64 -d > httpd.conf
$B/rm -f app.json
# Stop only a server the launcher is running (it holds run.lock).
if ! $B/flock -n run.lock $B/true; then
  $B/pkill -f "httpd -f -p 127.0.0.1:$PORT" || true
  i=0; while ! $B/flock -n run.lock $B/true && [ $i -lt 30 ]; do $B/sleep 1; i=$((i+1)); done
fi
$B/rm -rf www.old
[ -d www ] && $B/mv www www.old
$B/mv incoming/www www
$B/rm -rf incoming www.old
"""

AFTER = r"""
B=/chariot/bin
app="$HOME/workspace/.chariot-app"
i=0
while [ $i -lt 30 ]; do
  r=$($B/printf 'GET /health HTTP/1.0\r\n\r\n' | $B/timeout 3 $B/nc -w 2 127.0.0.1 $PORT 2>/dev/null | $B/head -1 || true)
  case "$r" in *" 200 "*) break;; esac
  $B/sleep 1; i=$((i+1))
done
case "$r" in *" 200 "*) ;; *) echo "failed=health"; $B/tail -5 "$app/app.log"; exit 4;; esac
$B/printf '%s' '{"format":"chariot-agent-app/v1","port":PORTNUM,"health_path":"/health"}' > "$app/app.json.tmp"
$B/mv -f "$app/app.json.tmp" "$app/app.json"
echo "published"
$B/df -h "$HOME" | $B/tail -1
"""


def ssh(agent: str, script: str, stdin_tar: bytes | None = None, timeout=900) -> str:
    if stdin_tar is None:
        p = subprocess.run(["chariot", "ssh", agent, "--", "/chariot/bin/sh", "-s"], input=script.encode(),
                           capture_output=True, timeout=timeout)
    else:
        # The tar goes as stdin to a fixed command; the agent's app folder is the only target.
        p = subprocess.run(["chariot", "ssh", agent, "--", "/chariot/bin/sh", "-c",
                            "'/chariot/bin/rm -rf $HOME/workspace/.chariot-app/incoming && "
                            "/chariot/bin/mkdir -p $HOME/workspace/.chariot-app/incoming && "
                            "/chariot/bin/tar -xf - -C $HOME/workspace/.chariot-app/incoming && echo received'"],
                           input=stdin_tar, capture_output=True, timeout=timeout)
    out = p.stdout.decode(errors="replace")
    if p.returncode != 0:
        sys.exit(f"agent command failed ({p.returncode}): {out[-800:]} {p.stderr.decode(errors='replace')[-400:]}")
    return out


def probe_url(agent: str) -> str:
    out = ssh(agent, 'printf "app_url=%s\\n" "$CHARIOT_APP_URL"\n')
    for line in out.splitlines():
        if line.startswith("app_url=https://"):
            return line.split("=", 1)[1].strip().rstrip("/")
    sys.exit("agent reported no CHARIOT_APP_URL")


def peaks(path: str, n: int = PEAKS) -> list[float]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "4000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    import array
    a = array.array("h", raw)
    step = max(1, len(a) // n)
    vals = []
    for i in range(n):
        seg = a[i * step:(i + 1) * step]
        if not seg:
            vals.append(0.0)
            continue
        vals.append((sum(x * x for x in seg[::4]) / max(1, len(seg[::4]))) ** 0.5)
    top = max(vals) or 1.0
    return [round(v / top, 3) for v in vals]


def duration_ms(path: str) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout
    return int(float(out.strip()) * 1000)


def build_site(songs: str, site: str, base_url: str) -> dict:
    gen = json.load(open(os.path.join(songs, "generation.json")))
    www = os.path.join(site, "www")
    for d in ("tracks", "covers"):
        os.makedirs(os.path.join(www, d), exist_ok=True)
    tracks = []
    for key in sorted(gen["tracks"], key=int):
        t = gen["tracks"][key]
        n = int(key)
        nn = f"{n:02d}"
        audio = os.path.join(songs, t["file"])
        shutil.copyfile(audio, os.path.join(www, "tracks", f"{nn}.mp3"))
        cover = os.path.join(songs, "covers", f"{nn}.jpg")
        has_cover = os.path.exists(cover)
        if has_cover:
            shutil.copyfile(cover, os.path.join(www, "covers", f"{nn}.jpg"))
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", cover, "-vf", "scale=320:320", "-q:v", "4",
                            os.path.join(www, "covers", f"{nn}.thumb.jpg")], check=True)
        data = open(audio, "rb").read()
        tracks.append({
            "id": f"t{nn}",
            "number": n,
            "title": t["title"],
            "artist": ARTIST,
            "durationMs": duration_ms(audio),
            "bpm": t["bpm"],
            "key": t["key"],
            "audio": f"tracks/{nn}.mp3",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "cover": f"covers/{nn}.jpg" if has_cover else None,
            "thumb": f"covers/{nn}.thumb.jpg" if has_cover else None,
            "peaks": peaks(audio),
        })
    catalog = {
        "schema": "rose-music-catalog/v1",
        "name": "Rose Music",
        "album": ALBUM,
        "baseUrl": base_url,
        "generatedBy": {"model": gen.get("model"), "lm": gen.get("lm"), "acestepCommit": gen.get("acestep_commit")},
        "publishedAt": int(time.time() * 1000),
        "tracks": tracks,
    }
    with open(os.path.join(www, "catalog.json"), "w") as f:
        json.dump(catalog, f, separators=(",", ":"))
    with open(os.path.join(www, "health"), "w") as f:
        f.write("ok\n")
    return catalog


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--songs", required=True)
    ap.add_argument("--agent", default="rose-music")
    ap.add_argument("--dry-run", action="store_true", help="build the site locally and stop")
    args = ap.parse_args()

    base_url = "https://example.invalid" if args.dry_run else probe_url(args.agent)
    with tempfile.TemporaryDirectory() as site:
        catalog = build_site(os.path.abspath(os.path.expanduser(args.songs)), site, base_url)
        total = sum(t["bytes"] for t in catalog["tracks"])
        print(f"site: {len(catalog['tracks'])} tracks, {total / 1e6:.1f} MB")
        if args.dry_run:
            print(json.dumps({k: v for k, v in catalog.items() if k != "tracks"}, indent=1))
            return 0
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            tar.add(os.path.join(site, "www"), arcname="www")
        print(ssh(args.agent, "", stdin_tar=buf.getvalue()).strip())

    import base64
    env = (f"PORT={PORT}\nSTART_B64='{base64.b64encode(START.encode()).decode()}'\n"
           f"CONF_B64='{base64.b64encode(HTTPD_CONF.encode()).decode()}'\n")
    ssh(args.agent, env + SWAP)
    ssh(args.agent, launcher())
    print(ssh(args.agent, f"PORT={PORT}\n" + AFTER.replace("PORTNUM", str(PORT))).strip())

    # From outside, through Chariot's gateway.
    last = None
    for _ in range(20):
        try:
            with urllib.request.urlopen(base_url + "/catalog.json", timeout=20) as r:
                live = json.load(r)
            if live.get("publishedAt") == catalog["publishedAt"]:
                print(f"live: {base_url}/catalog.json ({len(live['tracks'])} tracks)")
                return 0
        except Exception as e:  # gateway may take a moment to pick up app.json
            last = e
        time.sleep(3)
    print(f"published in the agent, but the public URL did not serve the new catalog: {last!r}", file=sys.stderr)
    return 5


if __name__ == "__main__":
    sys.exit(main())
