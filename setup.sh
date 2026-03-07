#!/usr/bin/env bash
# setup.sh — Run on the GCE instance to install deps and configure the service.
set -euo pipefail

echo "==> Installing FFmpeg and Python3..."
sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg python3

echo "==> Creating srtproxy user..."
sudo useradd -r -s /usr/sbin/nologin srtproxy 2>/dev/null || true

echo "==> Setting up /opt/srtproxy..."
sudo mkdir -p /opt/srtproxy/hls
sudo cp /tmp/srtproxy/server.py /opt/srtproxy/
sudo cp /tmp/srtproxy/index.html /opt/srtproxy/
sudo chown -R srtproxy:srtproxy /opt/srtproxy

echo "==> Installing systemd service..."
sudo cp /tmp/srtproxy/srtproxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable srtproxy
sudo systemctl restart srtproxy

echo "==> Done! Service status:"
sudo systemctl status srtproxy --no-pager
