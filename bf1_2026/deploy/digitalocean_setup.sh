#!/bin/bash
# =============================================================================
# BF1-2026 Quantitative System – DigitalOcean Setup Script
# Tested on Ubuntu 24.04 LTS
# =============================================================================
set -euo pipefail

echo "=== BF1-2026 DigitalOcean Setup ==="

# 1. System updates
echo "[1/8] Updating system packages..."
apt-get update && apt-get upgrade -y
apt-get install -y curl git ufw fail2ban

# 2. Install Docker
echo "[2/8] Installing Docker..."
curl -fsSL https://get.docker.com | sh
systemctl enable docker && systemctl start docker

# 3. Install Docker Compose
echo "[3/8] Installing Docker Compose..."
apt-get install -y docker-compose-plugin

# 4. Configure firewall
echo "[4/8] Configuring UFW firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw --force enable

# 5. Create app directory
echo "[5/8] Setting up application directory..."
mkdir -p /opt/bf1-2026
cd /opt/bf1-2026

echo "Please clone your repository here:"
echo "  git clone <your-repo-url> ."
echo "  cp .env.template .env"
echo "  # Edit .env with your production values"

# 6. Install Certbot for SSL
echo "[6/8] Installing Certbot..."
apt-get install -y certbot

echo "After DNS is configured, run:"
echo "  certbot certonly --standalone -d yourdomain.com"

# 7. PostgreSQL backup cron
echo "[7/8] Setting up database backup cron..."
mkdir -p /opt/bf1-backups

cat > /etc/cron.d/bf1-backup << 'CRON'
# Daily PostgreSQL backup at 4 AM
0 4 * * * root docker exec bf1_postgres pg_dump -U bf1user bf1_2026 | gzip > /opt/bf1-backups/bf1_$(date +\%Y\%m\%d).sql.gz
# Keep only last 30 days
0 5 * * * root find /opt/bf1-backups -name "*.sql.gz" -mtime +30 -delete
CRON

# 8. Systemd service
echo "[8/8] Creating systemd service..."
cat > /etc/systemd/system/bf1-2026.service << 'SERVICE'
[Unit]
Description=BF1-2026 Quantitative System
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/bf1-2026
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable bf1-2026

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Clone repo to /opt/bf1-2026"
echo "  2. Copy and edit .env: cp .env.template .env"
echo "  3. Start services: docker compose up -d"
echo "  4. Run migrations: docker exec bf1_api alembic upgrade head"
echo "  5. Verify: curl http://localhost:8000/health"
