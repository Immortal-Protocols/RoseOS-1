#!/bin/bash
# One-time guest setup for the dedicated Mustang VM. Run by the parent on that
# VM after a fresh OS Login session. It does not mint a runner token, set
# instance metadata, start a build, or register a runner.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
home=/mnt/disks/mustang-data/roseos
tools=$home/tools
user=mustang-runner
instance=5585425566393384049
uuid=4183bff8-6be4-46bb-a641-2193d19813ee
refuse() { echo "$1" >&2; exit 4; }
# Create only the two runner-owned directories the source job needs. The roseos
# parent stays as it is: root and mode 0755, and this function never chmods it.
ensure_runner_dir() {
  local name=$1 mode=$2
  local path=$home/$name owner mode_now
  case $name in
    sync-logs|roseos-pin) ;;
    *) refuse 'source dir name' ;;
  esac
  if [[ -L $path || ( -e $path && ! -d $path ) ]]; then
    refuse "$name"
  fi
  if [[ ! -d $path ]]; then
    sudo install -d -o "$user" -g "$user" -m "$mode" -- "$path" >/dev/null 2>&1 || refuse "$name"
  fi
  if [[ -L $path || ! -d $path ]]; then
    refuse "$name"
  fi
  owner=$(stat -c %U -- "$path") || refuse "$name"
  mode_now=$(stat -c %a -- "$path") || refuse "$name"
  [[ $owner == "$user" && $mode_now == ${mode#0} ]] || refuse "$name"
}
prepare_source_dirs() {
  [[ -d $home && ! -L $home && -d $home/source && ! -L $home/source && -d $home/runner && ! -L $home/runner ]] \
    || refuse 'roseos parent'
  ensure_runner_dir sync-logs 0700
  ensure_runner_dir roseos-pin 0750
}
fetch() {
  local url=$1 sha=$2 dest=$3
  if [[ -f "$dest" ]] && printf '%s  %s\n' "$sha" "$dest" | sha256sum --check --status; then
    return 0
  fi
  local tmp
  tmp=$(mktemp /tmp/mustang-fetch.XXXXXX)
  curl --fail --location --retry 2 --output "$tmp" "$url" || { rm -f "$tmp"; refuse 'download failed'; }
  printf '%s  %s\n' "$sha" "$tmp" | sha256sum --check --status || { rm -f "$tmp"; refuse 'checksum mismatch'; }
  if [[ "${MUSTANG_BOOTSTRAP_DIRECT:-}" == 1 ]]; then
    mv "$tmp" "$dest"
  else
    sudo install -o "$user" -g "$user" -m 0644 "$tmp" "$dest"
    rm -f "$tmp"
  fi
}
health() {
  local runner_home=$home/runner
  local node_bin=$tools/node/node-v24.22.2-linux-x64/bin/node
  local aapt_bin=$tools/android-sdk/build-tools/android-15/aapt
  local apksigner_bin=$tools/android-sdk/build-tools/android-15/apksigner
  local listener_bin=$runner_home/bin/Runner.Listener
  as_runner() {
    # Stay out of the operator home, then enter the runner home as that user.
    # sudo --chdir is rejected by the guest sudoers policy, so env performs the chdir.
    ( cd /tmp && sudo -H -u "$user" -- /usr/bin/env --chdir "$runner_home" -- "$@" )
  }
  require_runner_exec() {
    local label=$1 path=$2
    if ! as_runner test -x "$path"; then
      refuse "$label is not executable by $user: $path"
    fi
  }
  require_version() {
    local label=$1 expected="$2" out
    shift 2
    if ! out=$(as_runner "$@" 2>&1); then
      refuse "$label failed as $user in $runner_home: ${out:-no output}"
    fi
    out=${out//$'\r'/}
    out=${out%$'\n'}
    [[ "$out" == "$expected" ]] || refuse "$label version is '${out}', expected ${expected}"
  }
  require_runner_exec node "$node_bin"
  require_version node v24.22.2 "$node_bin" --version
  require_runner_exec aapt "$aapt_bin"
  require_version aapt 'Android Asset Packaging Tool, v0.2-11948202' "$aapt_bin" version
  require_runner_exec apksigner "$apksigner_bin"
  require_version apksigner 0.9 "$apksigner_bin" --version
  require_runner_exec 'runner listener' "$listener_bin"
  require_version 'runner listener' 2.338.2 "$listener_bin" --version
  require_runner_exec run.sh "$runner_home/run.sh"
}
if [[ "${1:-}" == --check-fetch ]]; then
  fetch "$2" "$3" "$4"
  exit 0
fi
if [[ "${1:-}" == --prepare-source-dirs ]]; then
  [[ -n ${MUSTANG_BOOTSTRAP_HOME:-} ]] || refuse 'roseos parent'
  home=$MUSTANG_BOOTSTRAP_HOME
  user=${MUSTANG_BOOTSTRAP_USER:-mustang-runner}
  prepare_source_dirs
  exit 0
fi
if [[ "${1:-}" == --check-health ]]; then
  [[ -n ${MUSTANG_BOOTSTRAP_HOME:-} ]] || refuse 'health check home is unset'
  home=$MUSTANG_BOOTSTRAP_HOME
  tools=$home/tools
  user=${MUSTANG_BOOTSTRAP_USER:-mustang-runner}
  health
  exit 0
fi
seen=$(curl --fail --silent --show-error --max-time 5 -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/id) || refuse 'instance identity unreadable'
[[ "$seen" == "$instance" ]] || refuse 'instance id differs'
seen=$(findmnt --noheadings --output UUID --target /mnt/disks/mustang-data) || refuse 'disk identity unreadable'
[[ "$seen" == "$uuid" ]] || refuse 'disk UUID differs'
seen=$(findmnt --noheadings --output TARGET --target /mnt/disks/mustang-data) || refuse 'disk mount unreadable'
[[ "$seen" == /mnt/disks/mustang-data ]] || refuse 'disk mount differs'
sudo tee /etc/apt/sources.list.d/mustang-noble.sources >/dev/null <<'EOF'
Types: deb
URIs: http://archive.ubuntu.com/ubuntu/
Suites: noble noble-updates noble-security
Components: main universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
sudo apt-get update
sudo apt-get install -y \
  build-essential gcc-multilib g++-multilib python3 git gnupg openssh-client diffutils \
  fontconfig fonts-dejavu-core hostname openssl rsync unzip zip curl ca-certificates \
  openjdk-21-jre-headless gperf git-lfs yarnpkg repo
if ! id -u "$user" >/dev/null 2>&1; then
  sudo useradd --system --create-home --home-dir "$home/runner" --shell /usr/sbin/nologin "$user"
fi
sudo install -d -o "$user" -g "$user" -m 0750 \
  "$home/source" "$home/rose-actions" "$home/runner" "$home/inputs" \
  "$tools/node" "$tools/android-sdk/downloads" "$tools/android-sdk/build-tools"
prepare_source_dirs
if [[ -n "$(find "$home/source" -mindepth 1 -print -quit 2>/dev/null || true)" ]]; then
  echo 'existing Mustang source retained'
fi
node=$tools/node/node-v24.22.2-linux-x64.tar.xz
sdk=$tools/android-sdk/downloads/build-tools_r35_linux.zip
runner=$home/runner/actions-runner-linux-x64-2.338.2.tar.gz
fetch https://nodejs.org/dist/v24.22.2/node-v24.22.2-linux-x64.tar.xz \
  48cc0e21e048790e54c0cf5749c2725005313d652651c40b7a21feb9697aaf75 "$node"
fetch https://dl.google.com/android/repository/build-tools_r35_linux.zip \
  bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88 "$sdk"
fetch https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-linux-x64-2.338.2.tar.gz \
  baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40 "$runner"
sudo rm -f \
  "$home/runner/.runner" "$home/runner/.credentials" \
  "$home/runner/.credentials_rsaparams" "$home/runner/.path"
sudo find "$home/runner" -maxdepth 1 -name 'jit.*' -type f -delete
sudo -u "$user" tar -xJf "$node" -C "$tools/node"
sudo -u "$user" unzip -qo "$sdk" -d "$tools/android-sdk/build-tools"
sudo -u "$user" tar -xzf "$runner" -C "$home/runner"
sudo install -o "$user" -g "$user" -m 0750 "$root/scripts/mustang-ephemeral-guest.sh" "$home/runner/mustang-ephemeral-guest.sh"
sudo install -m 0644 "$root/os/build-only/mustang-ephemeral-runner.service" /etc/systemd/system/mustang-ephemeral-runner.service
sudo systemctl daemon-reload
sudo systemctl enable mustang-ephemeral-runner.service
health
