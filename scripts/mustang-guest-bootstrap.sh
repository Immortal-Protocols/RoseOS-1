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
test "$(curl --fail --silent --show-error --max-time 5 -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/id)" = "$instance"
test "$(findmnt --noheadings --output UUID --target /mnt/disks/mustang-data)" = "$uuid"
test "$(findmnt --noheadings --output TARGET --target /mnt/disks/mustang-data)" = /mnt/disks/mustang-data
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
fetch() {
  local url=$1 sha=$2 dest=$3
  sudo -u "$user" curl --fail --location --retry 2 --output "$dest" "$url"
  printf '%s  %s\n' "$sha" "$dest" | sudo -u "$user" sha256sum --check --status
}
node=$tools/node/node-v24.22.2-linux-x64.tar.xz
sdk=$tools/android-sdk/downloads/build-tools_r35_linux.zip
runner=$home/runner/actions-runner-linux-x64-2.338.2.tar.gz
fetch https://nodejs.org/dist/v24.22.2/node-v24.22.2-linux-x64.tar.xz \
  48cc0e21e048790e54c0cf5749c2725005313d652651c40b7a21feb9697aaf75 "$node"
fetch https://dl.google.com/android/repository/build-tools_r35_linux.zip \
  bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88 "$sdk"
fetch https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-linux-x64-2.338.2.tar.gz \
  baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40 "$runner"
sudo -u "$user" tar -xJf "$node" -C "$tools/node"
sudo -u "$user" unzip -qo "$sdk" -d "$tools/android-sdk/build-tools"
sudo -u "$user" tar -xzf "$runner" -C "$home/runner"
sudo install -o "$user" -g "$user" -m 0750 "$root/scripts/mustang-ephemeral-guest.sh" "$home/runner/mustang-ephemeral-guest.sh"
sudo install -m 0644 "$root/os/build-only/mustang-ephemeral-runner.service" /etc/systemd/system/mustang-ephemeral-runner.service
sudo systemctl daemon-reload
sudo systemctl enable mustang-ephemeral-runner.service
test -x "$tools/node/node-v24.22.2-linux-x64/bin/node"
test -x "$tools/android-sdk/build-tools/android-15/aapt"
test -x "$tools/android-sdk/build-tools/android-15/apksigner"
test -x "$home/runner/run.sh"
