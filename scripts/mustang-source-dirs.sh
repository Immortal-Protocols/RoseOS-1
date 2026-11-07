# Directories the Mustang source job needs, created only for mustang-runner.
# mustang-guest-bootstrap.sh sources this after the instance, disk, and mount
# checks. It never chmods the roseos parent. A caller sets home and user.
refuse() { echo "$1" >&2; exit 4; }
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
