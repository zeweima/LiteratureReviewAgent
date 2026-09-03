#!/usr/bin/env bash
# run_all.sh -- run the full synthesis pipeline (fine -> macro -> overall) for a
# track by calling the EXISTING CLIs in dependency order. Stops at the first
# failure (each step needs the previous step's manifest). Re-run without --fresh
# to resume (units with a final.pdf are skipped).
#
# Usage (cd's to its own dir so config paths resolve):
#   ./run_all.sh                                 # frontier, default models
#   ./run_all.sh knowledge
#   ./run_all.sh both                            # frontier then knowledge
#   ./run_all.sh frontier --fresh --review-model opus   # extra flags -> run_* steps
#   TITLE="My Title" ./run_all.sh
#   PY="conda run -n litkg python" ./run_all.sh  # if python isn't already the litkg one
set -euo pipefail
cd "$(dirname "$0")"

TRACK="${1:-frontier}"; shift || true
EXTRA=("$@")                       # forwarded to the run_* steps only
PY="${PY:-python}"                 # word-split intentionally (allows "conda run -n litkg python")

run_track() {
  local tk="$1" ttl="${TITLE:-}"
  if [[ -z "$ttl" ]]; then
    if [[ "$tk" == "frontier" ]]; then ttl="Tile Drainage: Research Frontier"
    else ttl="Tile Drainage: State of Knowledge"; fi
  fi
  echo "########## TRACK: $tk ##########"
  echo "=== [a] fine: prep + run ==="
  $PY prep_inputs.py --step fine    --track "$tk"
  $PY run_stage.py   --step fine    --track "$tk" ${EXTRA[@]+"${EXTRA[@]}"}
  echo "=== [b] macro: prep + run ==="
  $PY prep_inputs.py --step macro   --track "$tk"
  $PY run_stage.py   --step macro   --track "$tk" ${EXTRA[@]+"${EXTRA[@]}"}
  echo "=== [c] overall: prep + run ==="
  $PY prep_inputs.py --step overall --track "$tk" --title "$ttl"
  $PY run_overall.py --track "$tk" ${EXTRA[@]+"${EXTRA[@]}"}
  echo "=== done -> latex/agentic/$tk/overall/paper/main.pdf ==="
}

if [[ "$TRACK" == "both" ]]; then
  run_track frontier
  run_track knowledge
else
  run_track "$TRACK"
fi
