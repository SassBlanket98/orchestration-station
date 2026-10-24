#!/bin/sh
# Install the station CLI for the current user.
# Optional: copy the user systemd unit. This script never enables or starts a unit.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
bin_dir="$HOME/.local/bin"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
src_bin="$here/station/station"
src_unit="$here/deploy/station-monitor.service"

with_unit=0
for arg in "$@"; do
    case "$arg" in
        --with-unit)
            with_unit=1
            ;;
        -h|--help)
            cat <<'EOF'
usage: sh install.sh [--with-unit]

Copies station/station to ~/.local/bin/station.

--with-unit also copies station-monitor.service to
~/.config/systemd/user/. Nothing is enabled or started.
EOF
            exit 0
            ;;
        *)
            echo "install.sh: unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

if [ ! -f "$src_bin" ]; then
    echo "install.sh: $src_bin not found. Build the station first (see README.md)." >&2
    exit 1
fi

mkdir -p "$bin_dir"
cp "$src_bin" "$bin_dir/station"
chmod 0755 "$bin_dir/station"
echo "installed: $bin_dir/station"

if [ "$with_unit" -eq 1 ]; then
    if [ ! -f "$src_unit" ]; then
        echo "install.sh: $src_unit not found." >&2
        exit 1
    fi
    mkdir -p "$unit_dir"
    cp "$src_unit" "$unit_dir/station-monitor.service"
    echo "copied unit: $unit_dir/station-monitor.service"
    echo "not enabled and not started. To run it yourself:"
    echo "  systemctl --user daemon-reload"
    echo "  systemctl --user enable --now station-monitor.service"
fi
