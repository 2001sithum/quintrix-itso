#!/usr/bin/env bash
# Upload model weights to the GCS bucket the CI build pulls them from.
#
# The .pt files are gitignored (170MB), so a CI checkout has none of them. A
# container built without them starts and serves happily but detects nothing —
# R3D-18 and the Track 1 threat detector both degrade to empty results. Weights
# are therefore shipped out of band, and the build fails if they are missing.
#
# Run once per bucket, and again whenever a model is retrained:
#   ./scripts/upload-weights.sh gs://my-bucket/quintrix-weights
set -euo pipefail

DEST="${1:-}"
if [[ -z "$DEST" ]]; then
  echo "usage: $0 gs://BUCKET/PREFIX" >&2
  exit 2
fi

cd "$(dirname "$0")/.."

REQUIRED=(
  weights/r3d18_ucfcrime.pt
  weights/yolov8s_suspicious.pt
  weights/sentiment_mlp.pt
)
OPTIONAL=(
  weights/yolov8s.pt              # Track 2 — ultralytics re-downloads if absent
  weights/sentiment_metrics.json
  weights/threshold_calibration.json
)

missing=0
for f in "${REQUIRED[@]}"; do
  [[ -f "$f" ]] || { echo "MISSING (required): $f" >&2; missing=1; }
done
[[ $missing -eq 0 ]] || { echo "Refusing to upload an incomplete set." >&2; exit 1; }

for f in "${REQUIRED[@]}" "${OPTIONAL[@]}"; do
  [[ -f "$f" ]] || { echo "skip (absent): $f"; continue; }
  echo "uploading $f ($(du -h "$f" | cut -f1))"
  gsutil -q cp "$f" "$DEST/$(basename "$f")"
done

echo
echo "Done. Point the workflow at this prefix:"
echo "  gh variable set GCP_WEIGHTS_URI --body '$DEST'"
