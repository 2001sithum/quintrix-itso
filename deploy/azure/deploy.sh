#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────────────
# deploy/azure/deploy.sh — Deploy Quintrix ITSO to Azure Container Apps
# ────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────
RESOURCE_GROUP="${AZURE_RG:-quintrix-itso-rg}"
LOCATION="${AZURE_LOCATION:-centralindia}"
ACR_NAME="${AZURE_ACR:-quintrixitsoacr}"
ENVIRONMENT_NAME="${AZURE_ENV:-quintrix-itso-env}"
APP_NAME="${AZURE_APP:-quintrix-itso-app}"
IMAGE_NAME="quintrix-itso"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d%H%M%S)}"
TARGET_PORT=8000
CPU="${CPU:-2.0}"
MEMORY="${MEMORY:-4.0Gi}"
MIN_REPLICAS="${MIN_REPLICAS:-1}"
MAX_REPLICAS="${MAX_REPLICAS:-1}"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║    Quintrix ITSO — Azure Container Apps Deployment          ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Resource Group:    $RESOURCE_GROUP"
echo "║  Location:          $LOCATION"
echo "║  ACR:               $ACR_NAME"
echo "║  Environment:       $ENVIRONMENT_NAME"
echo "║  App Name:          $APP_NAME"
echo "║  Image:             $IMAGE_NAME:$IMAGE_TAG"
echo "║  CPU / Memory:      $CPU / $MEMORY"
echo "║  Replicas:          $MIN_REPLICAS - $MAX_REPLICAS"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Resource Group ─────────────────────────────────────────────────
echo "▸ [1/5] Ensuring resource group '$RESOURCE_GROUP' in '$LOCATION'..."
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --output none

# ── Step 2: Azure Container Registry & Login ──────────────────────────────
echo "▸ [2/5] Ensuring Azure Container Registry '$ACR_NAME'..."
az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --sku Basic \
  --admin-enabled true \
  --output none 2>/dev/null || true

ACR_LOGIN_SERVER=$(az acr show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --query loginServer \
  --output tsv)

echo "  ACR Login Server: $ACR_LOGIN_SERVER"
echo "  Authenticating Docker with ACR..."
az acr login --name "$ACR_NAME" --output none

# ── Step 3: Build & Push Image via Local Docker ────────────────────────────
FULL_IMAGE="$ACR_LOGIN_SERVER/$IMAGE_NAME:$IMAGE_TAG"
echo "▸ [3/5] Building & pushing image '$FULL_IMAGE' via Docker (linux/amd64)..."

BUILD_DIR=$(mktemp -d /tmp/quintrix_build.XXXXXX)
trap 'rm -rf "$BUILD_DIR"' EXIT

echo "  Checkpointing SQLite database..."
sqlite3 quintrix_itso.db "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true

echo "  Staging code to clean build directory ($BUILD_DIR)..."
cp deploy/azure/Dockerfile "$BUILD_DIR/Dockerfile"
cp requirements.txt server.py itso_system.py "$BUILD_DIR/"
cp -r engine "$BUILD_DIR/"
cp -r models "$BUILD_DIR/"
cp -r static "$BUILD_DIR/"
cp -r training "$BUILD_DIR/"
cp -r vendor_wheels "$BUILD_DIR/"
cp quintrix_itso.db "$BUILD_DIR/"
cp -r archive "$BUILD_DIR/"
mkdir -p "$BUILD_DIR/weights"
cp -r weights/* "$BUILD_DIR/weights/"

# Clean any pycache or macOS metadata
find "$BUILD_DIR" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -name ".DS_Store" -delete 2>/dev/null || true

echo "  Building Docker image for linux/amd64..."
docker build \
  --platform linux/amd64 \
  -t "$FULL_IMAGE" \
  "$BUILD_DIR"

echo "  Pushing Docker image to Azure Container Registry..."
docker push "$FULL_IMAGE"
docker tag "$FULL_IMAGE" "$ACR_LOGIN_SERVER/$IMAGE_NAME:latest"
docker push "$ACR_LOGIN_SERVER/$IMAGE_NAME:latest"

rm -rf "$BUILD_DIR"
trap - EXIT

# ── Step 4: Container Apps Environment ─────────────────────────────────────
echo "▸ [4/5] Ensuring Container Apps environment '$ENVIRONMENT_NAME'..."
az containerapp env create \
  --name "$ENVIRONMENT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --output none 2>/dev/null || true

# ── Step 5: Deploy Container App ──────────────────────────────────────────
ACR_PASSWORD=$(az acr credential show \
  --name "$ACR_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "passwords[0].value" \
  --output tsv)

echo "▸ [5/5] Deploying container app '$APP_NAME'..."
if az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "  Updating existing container app..."
  az containerapp update \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --image "$FULL_IMAGE" \
    --cpu "$CPU" \
    --memory "$MEMORY" \
    --min-replicas "$MIN_REPLICAS" \
    --max-replicas "$MAX_REPLICAS" \
    --output none
else
  echo "  Creating new container app..."
  az containerapp create \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --environment "$ENVIRONMENT_NAME" \
    --image "$FULL_IMAGE" \
    --registry-server "$ACR_LOGIN_SERVER" \
    --registry-username "$ACR_NAME" \
    --registry-password "$ACR_PASSWORD" \
    --target-port "$TARGET_PORT" \
    --ingress external \
    --cpu "$CPU" \
    --memory "$MEMORY" \
    --min-replicas "$MIN_REPLICAS" \
    --max-replicas "$MAX_REPLICAS" \
    --env-vars \
      "QUINTRIX_DB=/data/quintrix_itso.db" \
      "QUINTRIX_ARCHIVE=/data/archive" \
      "TORCH_HOME=/models/torch" \
      "QUINTRIX_MAX_JOBS=1" \
    --output none
fi

# ── Done ──────────────────────────────────────────────────────────────────
APP_URL=$(az containerapp show \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" \
  --output tsv)

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✅  Deployment complete!                                   ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  App URL: https://$APP_URL"
echo "║                                                             ║"
echo "║  Useful commands:                                           ║"
echo "║  • Logs:   az containerapp logs show \\                      ║"
echo "║              -n $APP_NAME -g $RESOURCE_GROUP                ║"
echo "║  • Stream: az containerapp logs show \\                      ║"
echo "║              -n $APP_NAME -g $RESOURCE_GROUP --follow       ║"
echo "║  • Scale:  az containerapp update \\                         ║"
echo "║              -n $APP_NAME -g $RESOURCE_GROUP \\              ║"
echo "║              --min-replicas 1 --max-replicas 2              ║"
echo "║  • Delete: az group delete -n $RESOURCE_GROUP --yes         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
