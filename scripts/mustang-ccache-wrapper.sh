#!/bin/bash
# CC_WRAPPER receives the compiler and its arguments from the pinned Soong.
set -eu
cache_root=$1
cache_namespace=$2
shift 2
# Ninja deliberately filters most environment variables. Establish the complete
# local cache configuration here; never inherit a remote cache or sloppy checks.
for cache_variable in ${!CCACHE_@}; do unset "$cache_variable"; done
export CCACHE_CONFIGPATH="$cache_root/ccache.conf"
export CCACHE_DIR="$cache_root/objects"
export CCACHE_COMPILERCHECK=content
export CCACHE_BASEDIR="$PWD"
export CCACHE_NAMESPACE="$cache_namespace"
exec "$cache_root/ccache-4.12.2" "$1" "-fdebug-prefix-map=$PWD=." "${@:2}"
