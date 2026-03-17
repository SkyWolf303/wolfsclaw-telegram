#!/usr/bin/env bash
# DigitalOcean Ubuntu VPS setup for wolfsclaw-telegram bot
set -euo pipefail

echo "=== wolfsclaw-telegram VPS Setup ==="

# 1. System updates
echo "[1/6] Updating system packages…"
apt-get update -y && apt-get upgrade -y

# 2. Install Docker
echo "[2/6] Installing Docker…"
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
else
    echo "  Docker already installed."
fi

# 3. Install Docker Compose
echo "[3/6] Installing Docker Compose…"
if ! command -v docker-compose &>/dev/null; then
    apt-get install -y docker-compose-plugin
    # Fallback: standalone binary
    if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null; then
        COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep '"tag_name"' | head -1 | cut -d'"' -f4)
        curl -L "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
        chmod +x /usr/local/bin/docker-compose
    fi
else
    echo "  Docker Compose already installed."
fi

# 4. Install git
echo "[4/6] Installing git…"
apt-get install -y git

# 5. Clone repo and configure
REPO_DIR="/opt/wolfsclaw-telegram"
echo "[5/6] Setting up project in ${REPO_DIR}…"
if [ ! -d "$REPO_DIR" ]; then
    echo "  Please clone your repo to ${REPO_DIR}:"
    echo "    git clone <your-repo-url> ${REPO_DIR}"
    echo "  Then re-run this script."
    echo ""
    echo "  Or copy files manually:"
    echo "    mkdir -p ${REPO_DIR}"
    echo "    cp -r . ${REPO_DIR}/"
fi

if [ -d "$REPO_DIR" ]; then
    cd "$REPO_DIR"
    mkdir -p data

    if [ ! -f .env ]; then
        cp .env.example .env
        echo ""
        echo "  ⚠️  Edit .env with your API keys:"
        echo "    nano ${REPO_DIR}/.env"
        echo ""
    fi
fi

# 6. Docker auto-start on reboot
echo "[6/6] Enabling Docker auto-start…"
systemctl enable docker

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. cd ${REPO_DIR}"
echo "  2. Edit .env with your API keys"
echo "  3. docker compose up -d --build"
echo "  4. docker logs -f wolfsclaw-telegram"
echo ""
