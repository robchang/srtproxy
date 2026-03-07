#!/usr/bin/env bash
# deploy.sh — Create a GCE e2-micro instance and deploy the SRT proxy.
set -euo pipefail

INSTANCE_NAME="${INSTANCE_NAME:-srtproxy}"
ZONE="${ZONE:-us-west1-b}"
MACHINE_TYPE="e2-micro"
IMAGE_FAMILY="debian-12"
IMAGE_PROJECT="debian-cloud"
PORT=9090
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Checking gcloud CLI..."
if ! command -v gcloud &>/dev/null; then
    echo "ERROR: gcloud CLI not found. Install it from https://cloud.google.com/sdk/docs/install"
    exit 1
fi

ACTIVE_PROJECT=$(gcloud config get-value project 2>/dev/null)
if [ -z "$ACTIVE_PROJECT" ]; then
    echo "ERROR: No active GCP project. Run: gcloud config set project YOUR_PROJECT_ID"
    exit 1
fi
echo "    Using project: $ACTIVE_PROJECT"

# --- Create firewall rule (idempotent) ---
echo "==> Ensuring firewall rule for port $PORT..."
if ! gcloud compute firewall-rules describe allow-srtproxy &>/dev/null 2>&1; then
    gcloud compute firewall-rules create allow-srtproxy \
        --direction=INGRESS \
        --action=ALLOW \
        --rules=tcp:$PORT \
        --source-ranges=0.0.0.0/0 \
        --target-tags=srtproxy \
        --description="Allow HTTP access to SRT proxy on port $PORT"
    echo "    Firewall rule created."
else
    echo "    Firewall rule already exists."
fi

# --- Create instance (if not exists) ---
echo "==> Checking for existing instance '$INSTANCE_NAME'..."
if gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" &>/dev/null 2>&1; then
    echo "    Instance already exists. Redeploying..."
else
    echo "==> Creating e2-micro instance '$INSTANCE_NAME' in $ZONE..."
    gcloud compute instances create "$INSTANCE_NAME" \
        --zone="$ZONE" \
        --machine-type="$MACHINE_TYPE" \
        --image-family="$IMAGE_FAMILY" \
        --image-project="$IMAGE_PROJECT" \
        --tags=srtproxy \
        --boot-disk-size=10GB \
        --boot-disk-type=pd-standard
    echo "    Instance created. Waiting for SSH to be ready..."
    sleep 15
fi

# --- Upload files ---
echo "==> Uploading project files..."
gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --command="mkdir -p /tmp/srtproxy"
gcloud compute scp \
    "$PROJECT_DIR/server.py" \
    "$PROJECT_DIR/index.html" \
    "$PROJECT_DIR/setup.sh" \
    "$PROJECT_DIR/srtproxy.service" \
    "$INSTANCE_NAME":/tmp/srtproxy/ \
    --zone="$ZONE"

# --- Run setup ---
echo "==> Running setup on instance..."
gcloud compute ssh "$INSTANCE_NAME" --zone="$ZONE" --command="bash /tmp/srtproxy/setup.sh"

# --- Get external IP ---
EXTERNAL_IP=$(gcloud compute instances describe "$INSTANCE_NAME" \
    --zone="$ZONE" \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

echo ""
echo "=========================================="
echo " SRT Proxy deployed successfully!"
echo " Open in browser: http://$EXTERNAL_IP:$PORT"
echo "=========================================="
echo ""
echo "Useful commands:"
echo "  View logs:    gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --command='sudo journalctl -u srtproxy -f'"
echo "  Restart:      gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --command='sudo systemctl restart srtproxy'"
echo "  Stop VM:      gcloud compute instances stop $INSTANCE_NAME --zone=$ZONE"
echo "  Start VM:     gcloud compute instances start $INSTANCE_NAME --zone=$ZONE"
echo "  Delete VM:    gcloud compute instances delete $INSTANCE_NAME --zone=$ZONE"
