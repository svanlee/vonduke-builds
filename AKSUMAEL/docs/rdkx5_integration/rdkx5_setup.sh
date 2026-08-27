#!/usr/bin/env bash
# =============================================================================
# rdkx5_setup.sh — RDK X5 setup for AKSUMAEL / hobot_stereonet integration
# Run ON THE RDK X5 after flashing TROS.b Humble.
# Target: D-Robotics RDK X5, Ubuntu 22.04, TROS.b Humble
# Hub:    robocar-hub @ 192.168.0.156, ROS2 Humble, ROS_DOMAIN_ID=42
# =============================================================================

set -euo pipefail

TROS_SETUP="/opt/tros/humble/setup.bash"
DOMAIN_ID=42
SERVICE_NAME="stereonet"
CUSTOM_LAUNCH_SRC="$(dirname "$(realpath "$0")")/gs130w_stereonet.launch.py"
CUSTOM_LAUNCH_DST="$HOME/.ros/launch/gs130w_stereonet.launch.py"
BUNDLED_LAUNCH="/opt/tros/humble/share/hobot_stereonet/launch/x5/gs130w_stereonet.launch.py"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GRN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YLW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERR ]${NC}  $*" >&2; }

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [[ "$(id -u)" -eq 0 ]]; then
    warn "Running as root — systemd --user services require a normal user session."
    warn "Re-run as the regular user if you intend to use the systemd service."
fi

if [[ ! -f "$TROS_SETUP" ]]; then
    error "TROS.b Humble not found at $TROS_SETUP. Flash the board and retry."
    exit 1
fi

info "RDK X5 AKSUMAEL stereonet setup — starting"
echo "  Board hostname : $(hostname)"
echo "  OS             : $(lsb_release -ds 2>/dev/null || grep PRETTY /etc/os-release | cut -d= -f2)"
echo "  TROS setup     : $TROS_SETUP"
echo "  ROS_DOMAIN_ID  : $DOMAIN_ID"
echo

# ── Step 1: Configure TROS apt sources ───────────────────────────────────────
info "Step 1/6 — Configuring D-Robotics TROS apt repository"

if ! grep -r "sunrise.rdkos.com" /etc/apt/sources.list* &>/dev/null; then
    sudo bash -c 'echo "deb [arch=arm64] http://sunrise.rdkos.com/rdk/apt focal main" \
        > /etc/apt/sources.list.d/tros.list'
    curl -fsSL http://sunrise.rdkos.com/rdk/keys/sunrise.gpg \
        | sudo gpg --dearmor -o /usr/share/keyrings/sunrise-archive-keyring.gpg 2>/dev/null \
        || warn "GPG key import failed — ignore if packages install correctly."
fi

sudo apt-get update -q

# ── Step 2: Install hobot_stereonet and dependencies ─────────────────────────
info "Step 2/6 — Installing hobot_stereonet, hobot_mipi_cam, and ROS2 deps"

sudo apt-get install -y \
    tros-humble-hobot-stereonet \
    tros-humble-hobot-mipi-cam \
    tros-humble-hobot-image-publisher \
    ros-humble-sensor-msgs \
    ros-humble-pcl-conversions \
    ros-humble-pcl-ros

info "Packages installed successfully"

# ── Step 3: Set ROS_DOMAIN_ID globally ───────────────────────────────────────
info "Step 3/6 — Setting ROS_DOMAIN_ID=$DOMAIN_ID (must match robocar-hub)"

BASHRC="$HOME/.bashrc"
if ! grep -q "ROS_DOMAIN_ID" "$BASHRC"; then
    {
        echo ""
        echo "# AKSUMAEL fleet — must match robocar-hub (192.168.0.156)"
        echo "export ROS_DOMAIN_ID=$DOMAIN_ID"
    } >> "$BASHRC"
    info "ROS_DOMAIN_ID=$DOMAIN_ID added to ~/.bashrc"
else
    sed -i "s/export ROS_DOMAIN_ID=.*/export ROS_DOMAIN_ID=$DOMAIN_ID/" "$BASHRC"
    info "ROS_DOMAIN_ID updated to $DOMAIN_ID in ~/.bashrc"
fi
export ROS_DOMAIN_ID=$DOMAIN_ID

# ── Step 4: Install launch file ───────────────────────────────────────────────
info "Step 4/6 — Staging launch file"

mkdir -p "$HOME/.ros/launch"

if [[ -f "$CUSTOM_LAUNCH_SRC" ]]; then
    cp "$CUSTOM_LAUNCH_SRC" "$CUSTOM_LAUNCH_DST"
    info "Copied custom launch from: $CUSTOM_LAUNCH_SRC"
    LAUNCH_TO_USE="$CUSTOM_LAUNCH_DST"
elif [[ -f "$BUNDLED_LAUNCH" ]]; then
    info "Using bundled TROS launch: $BUNDLED_LAUNCH"
    LAUNCH_TO_USE="$BUNDLED_LAUNCH"
else
    warn "No launch file found at either expected location."
    warn "Place gs130w_stereonet.launch.py in $HOME/.ros/launch/ manually."
    LAUNCH_TO_USE="$CUSTOM_LAUNCH_DST"
fi

# ── Step 5: Create and enable systemd user service ────────────────────────────
info "Step 5/6 — Creating systemd user service: ${SERVICE_NAME}.service"

mkdir -p "$HOME/.config/systemd/user"

cat > "$HOME/.config/systemd/user/${SERVICE_NAME}.service" << EOF
[Unit]
Description=hobot_stereonet PointCloud2 publisher (AKSUMAEL / RDK X5)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment="ROS_DOMAIN_ID=${DOMAIN_ID}"
Environment="HOME=${HOME}"

ExecStart=/bin/bash -c '\
    source ${TROS_SETUP} && \
    export ROS_DOMAIN_ID=${DOMAIN_ID} && \
    ros2 launch hobot_stereonet gs130w_stereonet.launch.py \
        2>&1 | tee /tmp/stereonet.log'

Restart=on-failure
RestartSec=5

StandardOutput=journal
StandardError=journal
SyslogIdentifier=stereonet

[Install]
WantedBy=default.target
EOF

# Allow service to survive logout
loginctl enable-linger "$(whoami)" 2>/dev/null \
    || warn "loginctl enable-linger failed — run: sudo loginctl enable-linger $(whoami)"

systemctl --user daemon-reload
systemctl --user enable "${SERVICE_NAME}.service"
systemctl --user start  "${SERVICE_NAME}.service"

info "Service enabled and started"
systemctl --user status "${SERVICE_NAME}.service" --no-pager -l || true

# ── Step 6: Verify /stereonet/depth is being published ───────────────────────
info "Step 6/6 — Waiting for /stereonet/depth topic (up to 30 s)…"

# shellcheck disable=SC1090
source "$TROS_SETUP"
export ROS_DOMAIN_ID=$DOMAIN_ID

FOUND=0
for i in $(seq 1 6); do
    sleep 5
    echo -n "  Attempt $i/6 … "
    if ros2 topic list 2>/dev/null | grep -q "/stereonet/depth"; then
        echo "FOUND"
        FOUND=1
        break
    else
        echo "not yet"
    fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════════════════════"
echo "  AKSUMAEL — RDK X5 Setup Summary"
echo "════════════════════════════════════════════════════════════"

X5_IP=$(hostname -I | awk '{print $1}')
echo "  X5 IP address  : $X5_IP"
echo "  ROS_DOMAIN_ID  : $DOMAIN_ID"
echo "  Service        : ${SERVICE_NAME}.service"
echo "  Launch file    : $LAUNCH_TO_USE"
echo "  Log file       : /tmp/stereonet.log"
echo

if [[ $FOUND -eq 1 ]]; then
    echo -e "  ${GRN}✓  /stereonet/depth is LIVE${NC}"
    echo
    echo "  On robocar-hub (192.168.0.156), verify with:"
    echo "    export ROS_DOMAIN_ID=$DOMAIN_ID"
    echo "    ros2 topic hz /stereonet/depth"
    echo "    ros2 topic echo /stereonet/depth --once | head -20"
else
    echo -e "  ${RED}✗  /stereonet/depth not detected (service may still be starting)${NC}"
    echo
    echo "  Diagnose with:"
    echo "    journalctl --user -u $SERVICE_NAME -f"
    echo "    cat /tmp/stereonet.log"
    echo
    echo "  Manual launch (debugging):"
    echo "    source $TROS_SETUP"
    echo "    export ROS_DOMAIN_ID=$DOMAIN_ID"
    echo "    ros2 launch hobot_stereonet gs130w_stereonet.launch.py"
fi

echo "════════════════════════════════════════════════════════════"
