#!/usr/bin/env bash
# Runs ON THE PC (Git Bash). Pushes HEAD to the fork branch and updates the car right away.
# Usage: tools/deploy.sh [branch]      (default: master)
# Done = running commit on the Pi equals local HEAD and /api/state answers.
set -euo pipefail
BRANCH=${1:-master}
PI_HOST=${PI_HOST:-pi@192.168.178.131}
SSH="ssh -i $HOME/.ssh/picar_ed25519 -o BatchMode=yes $PI_HOST"

expected_commit=$(git rev-parse HEAD)
git push origin "HEAD:$BRANCH"
$SSH "sudo /home/pi/SunFounder_PiCar-V/tools/picar-update.sh $BRANCH" || true
running_commit=$($SSH 'git -C /home/pi/SunFounder_PiCar-V rev-parse HEAD')
$SSH 'cat /home/pi/picar-data/update-status.json'
if [ "$running_commit" = "$expected_commit" ] && curl -sf -o /dev/null --max-time 5 "http://${PI_HOST#*@}:8000/api/state"; then
    echo "DEPLOY OK: car runs $(git log -1 --format='%h %s' "$expected_commit")"
else
    echo "DEPLOY FAILED: car runs $running_commit, expected $expected_commit"; exit 1
fi
