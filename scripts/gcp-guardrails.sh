#!/usr/bin/env bash
# Hard cost guardrails for a billed GCP deployment.
#
# Applies, in order:
#   1. a budget with alert thresholds at 50/90/100%
#   2. Pub/Sub topic + Cloud Function that STOPS the VM when the cap is breached
#   3. a daily auto-shutdown schedule
#   4. an Artifact Registry cleanup policy (keep the 3 newest images)
#
# The breach action is STOP, not DELETE. Deleting the instance destroys its boot
# disk and with it every recording held in the Tier-3 review queue. A stopped VM
# costs only disk (~$0.04/GB/month). Pass GUARDRAIL_ACTION=delete to override —
# that is not reversible.
#
# Safe to re-run: every step is create-or-update.
set -euo pipefail

PROJECT=""; BUDGET_USD="5"; VM=""; ZONE=""; REGION="us-central1"
ACTION="${GUARDRAIL_ACTION:-stop}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)    PROJECT="$2"; shift 2 ;;
    --budget-usd) BUDGET_USD="$2"; shift 2 ;;
    --vm)         VM="$2"; shift 2 ;;
    --zone)       ZONE="$2"; shift 2 ;;
    --region)     REGION="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$PROJECT" && -n "$VM" && -n "$ZONE" ]] || {
  echo "usage: $0 --project P --vm NAME --zone ZONE [--budget-usd 5] [--region us-central1]" >&2
  exit 2
}

echo "project=$PROJECT vm=$VM zone=$ZONE cap=\$${BUDGET_USD} action=$ACTION"

BILLING_ID=$(gcloud billing projects describe "$PROJECT" \
  --format='value(billingAccountName)' 2>/dev/null | sed 's|billingAccounts/||')
if [[ -z "$BILLING_ID" ]]; then
  echo "ERROR: no billing account attached to $PROJECT." >&2
  echo "       Compute Engine needs one even for the free e2-micro." >&2
  echo "       See docs/COST_CONTROLS.md." >&2
  exit 1
fi
echo "billing account: $BILLING_ID"

gcloud services enable \
  cloudbilling.googleapis.com billingbudgets.googleapis.com \
  cloudfunctions.googleapis.com pubsub.googleapis.com \
  cloudscheduler.googleapis.com run.googleapis.com \
  --project="$PROJECT"

# --- 1/4 Pub/Sub topic the budget publishes to -----------------------------
TOPIC="budget-alerts"
gcloud pubsub topics describe "$TOPIC" --project="$PROJECT" >/dev/null 2>&1 \
  || gcloud pubsub topics create "$TOPIC" --project="$PROJECT"

# --- 2/4 Budget with a hard cap --------------------------------------------
BUDGET_NAME="itso-hard-cap"
EXISTING=$(gcloud billing budgets list --billing-account="$BILLING_ID" \
  --filter="displayName=$BUDGET_NAME" --format='value(name)' 2>/dev/null | head -1)

BUDGET_ARGS=(
  --display-name="$BUDGET_NAME"
  --budget-amount="${BUDGET_USD}USD"
  --threshold-rule=percent=0.5
  --threshold-rule=percent=0.9
  --threshold-rule=percent=1.0
  --filter-projects="projects/$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
  --notifications-rule-pubsub-topic="projects/$PROJECT/topics/$TOPIC"
)
if [[ -n "$EXISTING" ]]; then
  gcloud billing budgets update "$EXISTING" --billing-account="$BILLING_ID" "${BUDGET_ARGS[@]}"
else
  gcloud billing budgets create --billing-account="$BILLING_ID" "${BUDGET_ARGS[@]}"
fi

# --- 3/4 Cloud Function: on breach, stop the VM ----------------------------
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cat > "$WORK/main.py" <<'PY'
import base64, json, os
from googleapiclient import discovery

PROJECT = os.environ["GCP_PROJECT_ID"]
ZONE    = os.environ["VM_ZONE"]
VM      = os.environ["VM_NAME"]
ACTION  = os.environ.get("ACTION", "stop")


def handle(event, context):
    """Budget notifications arrive on every threshold crossing, so act only on
    a genuine breach — otherwise a 50% alert would stop a healthy service."""
    payload = json.loads(base64.b64decode(event["data"]).decode("utf-8"))
    cost = float(payload.get("costAmount", 0))
    budget = float(payload.get("budgetAmount", 0) or 0)
    if budget <= 0 or cost < budget:
        print(f"within budget: {cost:.2f}/{budget:.2f} — no action")
        return

    compute = discovery.build("compute", "v1", cache_discovery=False)
    if ACTION == "delete":
        # Destroys the boot disk and every held recording with it.
        print(f"BUDGET BREACHED {cost:.2f}/{budget:.2f} — DELETING {VM}")
        compute.instances().delete(project=PROJECT, zone=ZONE, instance=VM).execute()
    else:
        print(f"BUDGET BREACHED {cost:.2f}/{budget:.2f} — stopping {VM}")
        compute.instances().stop(project=PROJECT, zone=ZONE, instance=VM).execute()
PY
cat > "$WORK/requirements.txt" <<'PY'
google-api-python-client==2.149.0
PY

gcloud functions deploy itso-budget-guard \
  --project="$PROJECT" --region="$REGION" --gen2 --runtime=python311 \
  --source="$WORK" --entry-point=handle \
  --trigger-topic="$TOPIC" \
  --set-env-vars="GCP_PROJECT_ID=$PROJECT,VM_ZONE=$ZONE,VM_NAME=$VM,ACTION=$ACTION" \
  --memory=256Mi --timeout=60s --max-instances=3 --quiet

# --- 4/4 Nightly shutdown + registry retention -----------------------------
gcloud scheduler jobs describe itso-nightly-stop --location="$REGION" --project="$PROJECT" >/dev/null 2>&1 \
  && gcloud scheduler jobs delete itso-nightly-stop --location="$REGION" --project="$PROJECT" --quiet
gcloud scheduler jobs create http itso-nightly-stop \
  --project="$PROJECT" --location="$REGION" \
  --schedule="0 2 * * *" --time-zone="Etc/UTC" \
  --uri="https://compute.googleapis.com/compute/v1/projects/$PROJECT/zones/$ZONE/instances/$VM/stop" \
  --http-method=POST --oauth-service-account-email="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com" \
  --quiet || echo "  (scheduler job not created — check the compute SA has compute.instanceAdmin)"

cat > "$WORK/policy.json" <<'JSON'
[{"name":"keep-recent","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":3}},
 {"name":"delete-old","action":{"type":"Delete"},"condition":{"olderThan":"2592000s"}}]
JSON
gcloud artifacts repositories set-cleanup-policies quintrix \
  --project="$PROJECT" --location="$REGION" --policy="$WORK/policy.json" --quiet \
  || echo "  (cleanup policy skipped — repository 'quintrix' not found)"

echo
echo "Guardrails applied:"
echo "  budget cap        \$${BUDGET_USD} (alerts at 50/90/100%)"
echo "  on breach         ${ACTION} $VM"
echo "  nightly shutdown  02:00 UTC"
echo "  registry          keeps 3 newest images"
echo
echo "A stopped VM still bills for its disk. To stop paying entirely you must"
echo "delete the instance AND its disks — which destroys the archive. Snapshot first."
