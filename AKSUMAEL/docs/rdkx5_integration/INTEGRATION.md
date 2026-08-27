# AKSUMAEL — RDK X5 + GS130W Stereo Depth Integration Guide

**Fleet:** AKSUMAEL  
**Hub:** robocar-hub · HP Victus · Ubuntu · ROS2 Humble · `192.168.0.156`  
**Edge device:** D-Robotics RDK X5 · Sunrise 5 SoC · 10 TOPS BPU · TROS.b Humble  
**Camera:** RDK Stereo Camera GS130W (dual SC132GS · 1280×1080 · 80 mm baseline · MIPI)  
**Depth stack:** `hobot_stereonet` → `sensor_msgs/PointCloud2` → Nav2 costmap  

---

## Architecture overview

```
RDK X5 (WiFi 6)
  ├─ GS130W  ──MIPI──►  hobot_mipi_cam  ──/image_combine_raw──►  hobot_stereonet (BPU)
  │                                                                       │
  │                                              /stereonet/depth (PointCloud2)
  │                                              /stereonet/depth_image (Image)
  │                                                                       │
  └──────────────── ROS2 DDS (domain 42, WiFi 6) ────────────────────────┘
                                                                          │
robocar-hub (192.168.0.156)                                               │
  ├─ hub_bridge.launch.py  ◄──── liveness check ────────────────────────►│
  ├─ Nav2 costmap  ◄──────────── /stereonet/depth ──────────────────────►│
  └─ RViz2  ◄─────────────────── /stereonet/depth ──────────────────────►│
```

**Data rate:** 15–25 FPS PointCloud2 at 1280×1080 stereo resolution  
**Latency:** ~40–60 ms end-to-end (BPU inference + WiFi 6)  
**Effective range:** 0.3 m – 8.0 m (Nav2 costmap uses 0.1–3.0 m gate)

---

## Step 1 — Flash RDK X5 with TROS.b Humble

1. Download the latest TROS.b Humble image from the D-Robotics developer portal:  
   **https://developer.d-robotics.cc/en/rdkx5**  
   Navigate to: **Downloads → RDK X5 → Ubuntu 22.04 + TROS Humble**

2. Flash to SD card or eMMC with `balenaEtcher` or `dd`:
   ```bash
   # Example: flash to SD card at /dev/sdX
   sudo dd if=tros-humble-rdkx5-*.img of=/dev/sdX bs=4M status=progress conv=fsync
   ```

3. Boot the X5, complete first-run setup (set hostname, WiFi credentials).

4. Verify TROS.b:
   ```bash
   source /opt/tros/humble/setup.bash
   ros2 --version   # should print "ros2 cli information: humble"
   ```

> **Reference:** [RDK X5 Quick Start](https://developer.d-robotics.cc/en/rdkx5/quick_start)  
> **TROS docs:** [https://developer.d-robotics.cc/rdk_doc/en/](https://developer.d-robotics.cc/rdk_doc/en/)

---

## Step 2 — Connect the GS130W via MIPI

The GS130W uses a dual MIPI CSI-2 interface — one lane pair per eye.

1. Power off the X5.
2. Connect the GS130W flat-flex ribbon to the **CAM0/CAM1 MIPI connector** on the X5 carrier board. Refer to the X5 hardware manual for pinout — the GS130W ships with the correct cable for the RDK X5 dev board.
3. Power on the X5.
4. Confirm the kernel sees the sensor:
   ```bash
   dmesg | grep -i "sc132\|gs130\|mipi\|csi"
   # Expected: sc132gs detected / MIPI CSI-2 stream started
   ```

> **GS130W datasheet:** Available via D-Robotics partner portal or bundled with the camera kit.

---

## Step 3 — Run rdkx5_setup.sh on the X5

Copy the integration package to the X5 and run the setup script. The script handles apt installation, DOMAIN_ID, launch file staging, and the systemd service.

```bash
# On your dev machine — copy files to X5
scp -r ./rdkx5_integration/ sunrise@<X5_IP>:~/aksumael/

# SSH onto the X5
ssh sunrise@<X5_IP>

# Run setup (do NOT use sudo — the systemd user service runs as your user)
cd ~/aksumael/rdkx5_integration/
chmod +x rdkx5_setup.sh
./rdkx5_setup.sh
```

The script will:
- Add the D-Robotics TROS apt repo and install `hobot_stereonet` + `hobot_mipi_cam`
- Write `export ROS_DOMAIN_ID=42` to `~/.bashrc`
- Copy `gs130w_stereonet.launch.py` to `~/.ros/launch/`
- Create and enable `~/.config/systemd/user/stereonet.service`
- Start the service and poll for `/stereonet/depth` (up to 30 s)

**Expected output at the end:**
```
════════════════════════════════════════════════════════════
  AKSUMAEL — RDK X5 Setup Summary
════════════════════════════════════════════════════════════
  X5 IP address  : 192.168.0.XXX
  ROS_DOMAIN_ID  : 42
  Service        : stereonet.service
  ✓  /stereonet/depth is LIVE
════════════════════════════════════════════════════════════
```

**If the depth topic doesn't appear:**
```bash
# Check service logs
journalctl --user -u stereonet -f

# Manual launch for debugging
source /opt/tros/humble/setup.bash
export ROS_DOMAIN_ID=42
ros2 launch hobot_stereonet gs130w_stereonet.launch.py
```

---

## Step 4 — Set ROS_DOMAIN_ID=42 on the Hub

On **robocar-hub (192.168.0.156)**:

```bash
echo 'export ROS_DOMAIN_ID=42' >> ~/.bashrc
source ~/.bashrc
```

Confirm it's active for the current shell and any Nav2 launch sessions.  
If you launch Nav2 via `systemd` or a wrapper script, add `Environment=ROS_DOMAIN_ID=42` to that unit's `[Service]` section.

**Optional — CycloneDDS for cross-subnet reliability:**

If the X5 (WiFi) and hub (Ethernet) are on different subnets or DDS multicast is blocked:

```bash
# On both machines
sudo apt install ros-humble-rmw-cyclonedds-cpp
echo 'export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp' >> ~/.bashrc

# Create /etc/cyclonedds/config.xml with explicit peer addresses
sudo mkdir -p /etc/cyclonedds
sudo tee /etc/cyclonedds/config.xml << 'EOF'
<CycloneDDS>
  <Domain>
    <General>
      <AllowMulticast>false</AllowMulticast>
      <Peers>
        <Peer address="192.168.0.156"/>
        <Peer address="<X5_IP>"/>
      </Peers>
    </General>
  </Domain>
</CycloneDDS>
EOF
echo 'export CYCLONEDDS_URI=file:///etc/cyclonedds/config.xml' >> ~/.bashrc
source ~/.bashrc
```

---

## Step 5 — Run hub_bridge.launch.py to Verify Cross-Machine Topics

On the hub, in a new terminal:

```bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=42

ros2 launch hub_bridge.launch.py x5_ip:=<X5_IP>
```

**What to look for:**
```
[hz_check] average rate: 20.134 Hz
            min: 0.040s  max: 0.055s  std dev: 0.003s  window: 5
```

A non-zero Hz reading confirms the PointCloud2 stream is crossing the DDS boundary from X5 → hub.

**Troubleshooting:**
```bash
# List all topics visible on domain 42
ros2 topic list

# Check message content
ros2 topic echo /stereonet/depth --once | head -30

# Check TF tree (stereo_link must be present)
ros2 run tf2_tools view_frames
```

---

## Step 6 — Apply hub_nav2_costmap_patch.yaml to Nav2

Open your Nav2 params file (typically `<your_nav2_bringup>/params/nav2_params.yaml`) and merge the content from `hub_nav2_costmap_patch.yaml`.

**Minimal merge for local costmap:**

```yaml
local_costmap:
  local_costmap:
    ros__parameters:
      plugins: ["obstacle_layer", "inflation_layer", "stereonet_obstacles"]

      stereonet_obstacles:
        plugin: "nav2_costmap_2d::ObstacleLayer"
        enabled: true
        observation_sources: stereonet_cloud
        stereonet_cloud:
          topic: /stereonet/depth
          data_type: PointCloud2
          sensor_frame: stereo_link
          obstacle_max_range: 3.0
          obstacle_min_range: 0.1
          min_obstacle_height: 0.02
          max_obstacle_height: 2.00
          raytrace_max_range: 3.5
          marking: true
          clearing: true
```

**Restart Nav2** after applying:
```bash
# If using a systemd service
sudo systemctl restart nav2.service

# If launched manually
ros2 launch <your_nav2_bringup> bringup_launch.py
```

**Confirm the layer is active:**
```bash
ros2 service call /local_costmap/get_parameters rcl_interfaces/srv/GetParameters \
  "{names: ['stereonet_obstacles.enabled']}"
```

---

## Step 7 — Verify in RViz2

```bash
# On the hub
export ROS_DOMAIN_ID=42
ros2 launch hub_bridge.launch.py rviz:=true
```

RViz2 opens with a pre-configured PointCloud2 display subscribed to `/stereonet/depth`.

**What you should see:**
- A coloured point cloud (blue=near, red=far) updating at 15–25 FPS
- Points disappear and reappear correctly as objects move in/out of the GS130W FOV
- The cloud is anchored at `stereo_link` in the TF tree, which is 0.15 m forward + 0.12 m above `base_link`

**If the cloud is offset/misaligned:**  
Edit the `static_transform_publisher` arguments in `gs130w_stereonet.launch.py` to match the actual physical camera mount position on your robot chassis.

**Performance targets:**

| Metric | Expected |
|---|---|
| `ros2 topic hz /stereonet/depth` | 15–25 Hz |
| Point count per frame | ~50 k–200 k points |
| Latency (BPU + WiFi) | 40–80 ms |
| Nav2 costmap update | < 200 ms behind sensor |

---

## Service management (X5)

```bash
# Status
systemctl --user status stereonet

# Restart (e.g., after reconnecting camera)
systemctl --user restart stereonet

# View live logs
journalctl --user -u stereonet -f

# Disable autostart
systemctl --user disable stereonet
```

---

## Next step — dstereo_occnet (3D occupancy)

Once the stereo depth pipeline is stable, the optional second layer is `dstereo_occnet` — a BPU-accelerated 3D occupancy grid. It currently targets ZED-2i but can be adapted for the GS130W with a custom intrinsics config.

Repository: https://github.com/D-Robotics/dstereo_occnet

Integration approach:
1. Build `dstereo_occnet` on the X5 with TROS.b Humble
2. Feed `/image_combine_raw` as the stereo source (same topic as hobot_stereonet)
3. Publish the occupancy grid on `/stereonet/occupancy`
4. Add an `OccupancyGridLayer` to the Nav2 global costmap

---

## File reference

| File | Purpose | Runs on |
|---|---|---|
| `rdkx5_setup.sh` | One-shot X5 bootstrap — apt, DOMAIN_ID, systemd service | RDK X5 |
| `gs130w_stereonet.launch.py` | hobot_mipi_cam + hobot_stereonet + TF | RDK X5 |
| `hub_nav2_costmap_patch.yaml` | Nav2 costmap layer for `/stereonet/depth` | robocar-hub |
| `hub_bridge.launch.py` | Cross-machine liveness check + optional RViz2 | robocar-hub |
| `INTEGRATION.md` | This guide | — |

---

## Key references

- [D-Robotics RDK X5 Developer Portal](https://developer.d-robotics.cc/en/rdkx5)
- [hobot_stereonet (GitHub)](https://github.com/D-Robotics/hobot_stereonet)
- [dstereo_occnet (GitHub)](https://github.com/D-Robotics/dstereo_occnet)
- [TROS.b Humble docs](https://developer.d-robotics.cc/rdk_doc/en/)
- [Nav2 Obstacle Layer docs](https://docs.nav2.org/configuration/packages/costmap-plugins/obstacle.html)
- [CycloneDDS cross-subnet config](https://cyclonedds.io/docs/cyclonedds/latest/config/config-file-reference.html)
