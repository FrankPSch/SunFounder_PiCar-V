#!/bin/sh
# Runs ON THE PI as root (manual deploy over SSH, or picar-update.timer).
# Usage: picar-update.sh [branch]   (default: release)
# Fetches the branch from the fork; if the commit changed: checkout, restart picar, wait for a healthy
# /api/state; on failure roll back to the previous commit. Result goes to $STATUS_FILE and the journal.
REPO=/home/pi/SunFounder_PiCar-V
BRANCH=${1:-release}
STATUS_FILE=/home/pi/picar-data/update-status.json
HEALTH_URL=http://127.0.0.1:8000/api/state
HEALTH_TIMEOUT_S=90

as_pi() { runuser -u pi -- "$@"; }

say() {  # German, on the first USB speaker like events.audio_device (internal cards: events.INTERNAL_AUDIO_CARDS)
    speaker=$(aplay -l 2>/dev/null | sed -n 's/^card [0-9]*: \([A-Za-z0-9_]*\) \[.*\], device \([0-9]*\):.*/\1 \2/p' \
              | grep -v -e '^Headphones ' -e '^vc4hdmi ' | head -n 1 | sed 's/\(.*\) \(.*\)/plughw:CARD=\1,DEV=\2/')
    espeak-ng -v de -s 140 --stdout "$1" 2>/dev/null | aplay -q ${speaker:+-D "$speaker"} 2>/dev/null || true
}

write_status() {  # result, from, to, message
    mkdir -p "$(dirname "$STATUS_FILE")"
    printf '{"time": "%s", "branch": "%s", "result": "%s", "from": "%s", "to": "%s", "message": "%s"}\n' \
        "$(date -Iseconds)" "$BRANCH" "$1" "$2" "$3" "$4" > "$STATUS_FILE"
    echo "picar-update: $1 $2 -> $3 $4"
}

wait_healthy() {
    elapsed=0
    while [ $elapsed -lt $HEALTH_TIMEOUT_S ]; do
        if curl -sf -o /dev/null --max-time 3 "$HEALTH_URL"; then return 0; fi
        sleep 3; elapsed=$((elapsed + 3))
    done
    return 1
}

cd "$REPO" || exit 1
old_commit=$(as_pi git rev-parse HEAD)
if ! as_pi git fetch -q origin "$BRANCH"; then
    write_status fetch-failed "$old_commit" "" "git fetch origin $BRANCH failed"; exit 1
fi
new_commit=$(as_pi git rev-parse FETCH_HEAD)
if [ "$old_commit" = "$new_commit" ]; then
    write_status up-to-date "$old_commit" "$new_commit" ""; exit 0
fi
if ! as_pi git checkout -q --detach "$new_commit"; then
    write_status checkout-failed "$old_commit" "$new_commit" "local changes in the work tree?"; exit 1
fi
systemctl stop picar  # frees the speaker
say "Update startet. Ich bin gleich wieder da."
systemctl start picar
if wait_healthy; then
    write_status updated "$old_commit" "$new_commit" ""
    sleep 5  # after the car's own "Ich bin bereit" (api.STARTUP_MESSAGE): the speaker plays one sound at a time
    say "Update fertig."
    exit 0
fi
as_pi git checkout -q --detach "$old_commit"
systemctl restart picar
if wait_healthy; then
    write_status rolled-back "$old_commit" "$new_commit" "new commit not healthy within ${HEALTH_TIMEOUT_S}s"
    say "Update fehlgeschlagen. Die alte Version läuft wieder."
else
    write_status broken "$old_commit" "$new_commit" "rollback also unhealthy - check journalctl -u picar"
    say "Update fehlgeschlagen. Ich laufe nicht mehr richtig."
fi
exit 1
