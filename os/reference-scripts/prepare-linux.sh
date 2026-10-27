#!/usr/bin/env bash
set -euo pipefail
# Build preparation only. Does not access or flash any physical device.
test "$(uname -m)" = x86_64
if [ "$(id -u)" = 0 ]; then SUDO=; else SUDO=sudo; fi
$SUDO apt-get update
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y repo git gnupg openssh-client python3 curl zip unzip rsync fontconfig libfreetype6 openssl
mkdir -p "$HOME/pixel-os/grapheneos-2026100600" "$HOME/pixel-os/keys"
curl -fsSL https://grapheneos.org/allowed_signers -o "$HOME/pixel-os/keys/grapheneos_allowed_signers"
cd "$HOME/pixel-os/grapheneos-2026100600"
repo init -u https://github.com/GrapheneOS/platform_manifest.git -b refs/tags/2026100600 --depth=1 --no-clone-bundle
cd .repo/manifests
git -c gpg.ssh.allowedSignersFile="$HOME/pixel-os/keys/grapheneos_allowed_signers" verify-tag 2026100600
cd ../..
repo sync -c -j4 --no-clone-bundle --fail-fast
