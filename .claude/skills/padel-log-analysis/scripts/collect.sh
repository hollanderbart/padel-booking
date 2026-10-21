#!/usr/bin/env bash
# Read-only snapshot of the Padel Booking addon on the HA host via SSH.
#
# Usage: collect.sh <out_dir> [log_lines]
#
# Writes into <out_dir>:
#   info.json            addon state/version/options (password fields removed on the host)
#   addon.log            last <log_lines> addon log lines, ANSI colours stripped
#   files.tsv            mtime (epoch) / size / name of everything in /config/padel
#   host.txt             host clock (epoch + local time) for "how long ago" calculations
#   <state>.json         last_run, .booking_state, future_bookings, booking_history, club_schedule
#   sessions.json        cookie/token expiry only — never cookie values or the access token
#
# Nothing on the host is modified. Secrets are filtered with jq on the host so they
# never reach this machine.
set -euo pipefail

OUT=${1:?usage: collect.sh <out_dir> [log_lines]}
LINES=${2:-5000}
HOST=${PADEL_HA_HOST:-root@192.168.2.5}
KEY=${PADEL_HA_KEY:-$HOME/.ssh/id_ed25519_ha}
SLUG=${PADEL_ADDON_SLUG:-a30a93bf_padel_booking}
DIR=/config/padel

# Array, not a string: zsh does not word-split "$SSH"
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 "$HOST")

mkdir -p "$OUT"

if ! "${SSH[@]}" true 2>"$OUT/ssh_error.txt"; then
    echo "SSH naar $HOST mislukt:" >&2
    cat "$OUT/ssh_error.txt" >&2
    echo "Connection refused → Terminal & SSH addon draait niet of heeft geen poort (zie CLAUDE.md)." >&2
    exit 2
fi
rm -f "$OUT/ssh_error.txt"

"${SSH[@]}" "ha apps info $SLUG --raw-json | jq '.data | {state, boot, watchdog, version, version_latest, update_available,
    options: (.options | with_entries(select(.key | test(\"password\") | not)))}'" > "$OUT/info.json"

"${SSH[@]}" "ha apps logs $SLUG -n $LINES" | sed $'s/\x1b\\[[0-9;]*m//g' > "$OUT/addon.log"

"${SSH[@]}" "cd $DIR && for f in * .*; do [ -f \"\$f\" ] && stat -c '%Y	%s	%n' \"\$f\"; done; true" > "$OUT/files.tsv"

"${SSH[@]}" "date +%s; date '+%Y-%m-%d %H:%M:%S %Z'" > "$OUT/host.txt"

for f in last_run.json .booking_state.json future_bookings.json booking_history.json club_schedule.json; do
    "${SSH[@]}" "cat $DIR/$f 2>/dev/null" > "$OUT/${f#.}" || true
    [ -s "$OUT/${f#.}" ] || rm -f "$OUT/${f#.}"
done

MP=$("${SSH[@]}" "jq -c '[.[]? | select(.name == \"meetandplay_session\") | {domain, expires}]' \
    $DIR/.meetandplay_cookies.json 2>/dev/null" || true)
PT=$("${SSH[@]}" "jq -c '{expiry, has_user_id: (.user_id != null)}' $DIR/.playtomic_token.json 2>/dev/null" || true)
printf '{"meetandplay_session": %s, "playtomic_token": %s}\n' "${MP:-null}" "${PT:-null}" > "$OUT/sessions.json"

echo "Snapshot in $OUT ($(wc -l < "$OUT/addon.log" | tr -d ' ') logregels)"
