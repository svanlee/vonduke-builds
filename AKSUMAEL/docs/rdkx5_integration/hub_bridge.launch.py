# =============================================================================
# hub_bridge.launch.py
# Run ON robocar-hub (192.168.0.156) to verify the RDK X5 stereonet stream
# and optionally launch RViz2 for visual confirmation.
#
# Prerequisites on hub:
#   export ROS_DOMAIN_ID=42   ← must match RDK X5
#   ROS2 Humble installed
#
# Usage:
#   ros2 launch hub_bridge.launch.py
#   ros2 launch hub_bridge.launch.py rviz:=true
#   ros2 launch hub_bridge.launch.py x5_ip:=192.168.0.200
# =============================================================================

import os
import time
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    LogInfo,
    ExecuteProcess,
    TimerAction,
    OpaqueFunction,
)
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node


# ── Default RViz2 config written to /tmp at launch time ──────────────────────
RVIZ_CONFIG = """\
Panels:
  - Class: rviz_common/Displays
    Name: Displays
  - Class: rviz_common/Views
    Name: Views
Visualization Manager:
  Displays:
    - Alpha: 1
      Autocompute Intensity Bounds: true
      Autocompute Value Bounds:
        Max Value: 8
        Min Value: 0
      Class: rviz_default_plugins/PointCloud2
      Color: 255; 255; 255
      Color Transformer: AxisColor
      Enabled: true
      Invert Rainbow: false
      Max Color: 255; 0; 0
      Max Intensity: 8
      Min Color: 0; 0; 255
      Min Intensity: 0
      Name: StereoNet Depth
      Position Transformer: XYZ
      Queue Size: 5
      Size (Pixels): 2
      Size (m): 0.01
      Style: Points
      Topic:
        Depth: 5
        Durability Policy: Volatile
        Filter size: 10
        History Policy: Keep Last
        Reliability Policy: Best Effort
        Value: /stereonet/depth
      Use Fixed Frame: true
      Use rainbow: true
      Value: true
    - Class: rviz_default_plugins/TF
      Enabled: true
      Name: TF
      Show Arrows: true
      Show Axes: true
      Show Names: false
  Global Options:
    Background Color: 48; 48; 48
    Fixed Frame: base_link
    Frame Rate: 30
  Views:
    Current:
      Class: rviz_default_plugins/Orbit
      Distance: 4
      Focal Point:
        X: 0
        Y: 0
        Z: 0
      Focal Shape Fixed Size: true
      Focal Shape Size: 0.05
      Invert Z Axis: false
      Name: Current View
      Near Clip Distance: 0.01
      Pitch: 0.55
      Yaw: 0.75
      Value: Orbit (rviz)
"""


def write_rviz_config(context, *args, **kwargs):
    """Write the RViz2 config file to /tmp and return empty action list."""
    config_path = "/tmp/aksumael_stereonet.rviz"
    with open(config_path, "w") as f:
        f.write(RVIZ_CONFIG)
    return []


def generate_launch_description():

    # ── Launch arguments ──────────────────────────────────────────────────────
    rviz_arg = DeclareLaunchArgument(
        "rviz", default_value="false",
        description="Launch RViz2 with PointCloud2 display (true/false)"
    )
    x5_ip_arg = DeclareLaunchArgument(
        "x5_ip", default_value="",
        description="RDK X5 IP address (informational, shown in log)"
    )
    timeout_arg = DeclareLaunchArgument(
        "liveness_timeout", default_value="10.0",
        description="Seconds to wait before declaring X5 stream absent"
    )

    rviz_enabled  = LaunchConfiguration("rviz")
    x5_ip         = LaunchConfiguration("x5_ip")
    liveness_timeout = LaunchConfiguration("liveness_timeout")

    # ── Startup log ───────────────────────────────────────────────────────────
    domain_id = os.environ.get("ROS_DOMAIN_ID", "NOT SET — export ROS_DOMAIN_ID=42")
    log_start = LogInfo(msg=[
        "\n",
        "╔══════════════════════════════════════════════════════════╗\n",
        "║  AKSUMAEL Hub Bridge — RDK X5 stereonet liveness check  ║\n",
        "╚══════════════════════════════════════════════════════════╝\n",
        "  Hub IP        : 192.168.0.156\n",
        "  ROS_DOMAIN_ID : ", domain_id, "\n",
        "  X5 IP         : ", x5_ip, "\n",
        "  Watching topic: /stereonet/depth  (sensor_msgs/PointCloud2)\n",
    ])

    # ── Liveness monitor node ─────────────────────────────────────────────────
    # A lightweight Python lifecycle node that watches /stereonet/depth and
    # logs the rate. Exits with code 0 when data is confirmed, non-zero on
    # timeout (so the launch system can propagate the failure).
    liveness_node = Node(
        package="topic_tools",
        executable="relay",
        name="stereonet_liveness",
        output="screen",
        arguments=["/stereonet/depth", "/aksumael/stereonet_relay"],
        # topic_tools/relay will fail to start if /stereonet/depth never appears —
        # this surfaces the error visibly in the launch log.
    )

    # ── Fallback: ros2 topic hz (runs in a subprocess, always available) ──────
    # This runs even if topic_tools is not installed.
    hz_check = ExecuteProcess(
        cmd=[
            "bash", "-c",
            "source /opt/ros/humble/setup.bash && "
            "export ROS_DOMAIN_ID=42 && "
            "echo '[HUB BRIDGE] Waiting for /stereonet/depth...' && "
            "timeout 15 ros2 topic hz /stereonet/depth --window 5 || "
            "echo '[HUB BRIDGE] TIMEOUT: /stereonet/depth not received. "
            "Check X5 service: journalctl --user -u stereonet -f'"
        ],
        output="screen",
    )

    # ── Optional RViz2 ────────────────────────────────────────────────────────
    write_config = OpaqueFunction(function=write_rviz_config)

    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
        name="rviz2",
        arguments=["-d", "/tmp/aksumael_stereonet.rviz"],
        output="screen",
        condition=IfCondition(rviz_enabled),
    )

    # Delay RViz2 by 3 s to let DDS discovery settle
    rviz_delayed = TimerAction(
        period=3.0,
        actions=[rviz_node],
    )

    # ── Diagnostic info printed after 5 s ─────────────────────────────────────
    diagnostic_log = TimerAction(
        period=5.0,
        actions=[
            ExecuteProcess(
                cmd=[
                    "bash", "-c",
                    "source /opt/ros/humble/setup.bash && "
                    "export ROS_DOMAIN_ID=42 && "
                    "echo '--- Active ROS2 topics (domain 42) ---' && "
                    "ros2 topic list 2>/dev/null && "
                    "echo '--- Nodes ---' && "
                    "ros2 node list 2>/dev/null"
                ],
                output="screen",
            )
        ],
    )

    return LaunchDescription([
        rviz_arg,
        x5_ip_arg,
        timeout_arg,
        log_start,
        write_config,
        hz_check,
        diagnostic_log,
        rviz_delayed,
    ])
