# =============================================================================
# gs130w_stereonet.launch.py
# ROS2 launch file — D-Robotics RDK X5 + GS130W stereo camera
# Target: RDK X5, TROS.b Humble
# Publishes:
#   /stereonet/depth        — sensor_msgs/PointCloud2  (BPU stereo depth)
#   /stereonet/depth_image  — sensor_msgs/Image        (disparity visualisation)
#   /image_combine_raw      — sensor_msgs/Image        (NV12, both eyes combined)
#
# GS130W camera specs (SC132GS dual, 1280x1080, 80 mm baseline, MIPI CSI-2):
#   fx = 640.0  fy = 640.0  (≈ 50° HFOV at 1280 px)
#   cx = 640.0  cy = 540.0  (principal point, image centre)
#   baseline = 0.080 m
# =============================================================================

import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo
from launch.substitutions import LaunchConfiguration, EnvironmentVariable
from launch_ros.actions import Node


# ── Camera intrinsics (GS130W defaults, 1280×1080) ───────────────────────────
GS130W_FX        = 640.0
GS130W_FY        = 640.0
GS130W_CX        = 640.0
GS130W_CY        = 540.0
GS130W_BASELINE  = 0.080   # metres
GS130W_WIDTH     = 1280
GS130W_HEIGHT    = 1080

# Distortion (k1, k2, p1, p2, k3) — assumed near-zero for global-shutter lens;
# calibrate and replace these values with board-specific results.
GS130W_DISTORTION = [0.0, 0.0, 0.0, 0.0, 0.0]


def generate_launch_description():

    # ── Launch arguments ──────────────────────────────────────────────────────
    log_level_arg = DeclareLaunchArgument(
        "log_level", default_value="warn",
        description="ROS2 log level for all nodes"
    )
    enable_viz_arg = DeclareLaunchArgument(
        "enable_viz", default_value="true",
        description="Publish depth_image visualisation topic"
    )
    mipi_device_arg = DeclareLaunchArgument(
        "mipi_device", default_value="GS130W",
        description="MIPI camera device type string for hobot_mipi_cam"
    )

    log_level   = LaunchConfiguration("log_level")
    enable_viz  = LaunchConfiguration("enable_viz")
    mipi_device = LaunchConfiguration("mipi_device")

    # ── Node 1: hobot_mipi_cam ────────────────────────────────────────────────
    # Drives the GS130W dual MIPI-CSI2 interface and publishes a combined
    # NV12 frame (left | right side-by-side) on /image_combine_raw.
    mipi_cam_node = Node(
        package="hobot_mipi_cam",
        executable="hobot_mipi_cam",
        name="hobot_mipi_cam",
        output="screen",
        parameters=[{
            # Camera device / sensor type
            "camera_type":       mipi_device,
            # Combined (stereo) output mode — single topic, NV12, side-by-side
            "out_format":        "nv12",
            "image_width":       GS130W_WIDTH * 2,   # 2560 (both eyes combined)
            "image_height":      GS130W_HEIGHT,       # 1080
            "fps":               30,
            # Publish single combined topic consumed by hobot_stereonet
            "io_method":         "ros",
            "pub_topic_name":    "/image_combine_raw",
            # Calibration / intrinsics
            "camera_fx":         GS130W_FX,
            "camera_fy":         GS130W_FY,
            "camera_cx":         GS130W_CX,
            "camera_cy":         GS130W_CY,
            "k1": GS130W_DISTORTION[0],
            "k2": GS130W_DISTORTION[1],
            "p1": GS130W_DISTORTION[2],
            "p2": GS130W_DISTORTION[3],
            "k3": GS130W_DISTORTION[4],
        }],
        arguments=["--ros-args", "--log-level", log_level],
        remappings=[],
    )

    # ── Node 2: hobot_stereonet ───────────────────────────────────────────────
    # Runs stereo depth inference on the BPU (10 TOPS).
    # Reads /image_combine_raw (NV12 side-by-side), emits PointCloud2 + depth image.
    stereonet_node = Node(
        package="hobot_stereonet",
        executable="hobot_stereonet",
        name="hobot_stereonet",
        output="screen",
        parameters=[{
            # ── Input ──────────────────────────────────────────────────────
            "sub_img_topic":      "/image_combine_raw",
            "img_type":           "nv12",
            # ── Camera geometry ────────────────────────────────────────────
            "camera_fx":          GS130W_FX,
            "camera_fy":          GS130W_FY,
            "camera_cx":          GS130W_CX,
            "camera_cy":          GS130W_CY,
            "baseline":           GS130W_BASELINE,
            # ── Depth / disparity parameters ───────────────────────────────
            "min_distance":       0.3,    # metres — closer than this is noise
            "max_distance":       8.0,    # metres — GS130W reliable stereo range
            "confidence_thresh":  0.6,    # BPU confidence gate
            # ── Output ─────────────────────────────────────────────────────
            "pub_pointcloud":     True,
            "pub_depth_image":    enable_viz,
            "pointcloud_topic":   "/stereonet/depth",
            "depth_image_topic":  "/stereonet/depth_image",
            # Frame ID used in PointCloud2 header — match Nav2 costmap frame
            "frame_id":           "stereo_link",
            # ── BPU / performance ──────────────────────────────────────────
            "model_file_name":    "",     # "" → use default bundled model
            "bpu_core":           2,      # 0=auto, 1=core0, 2=both cores
        }],
        arguments=["--ros-args", "--log-level", log_level],
        remappings=[
            # Explicit remaps (redundant with params above, kept for clarity)
            ("/stereonet_node/pointcloud", "/stereonet/depth"),
            ("/stereonet_node/depth",      "/stereonet/depth_image"),
        ],
    )

    # ── TF: stereo_link relative to base_link ─────────────────────────────────
    # Adjust x/y/z/yaw to match physical mount position on the robot.
    # This example mounts the camera 0.15 m forward of base_link, 0.12 m up.
    static_tf_node = Node(
        package="tf2_ros",
        executable="static_transform_publisher",
        name="stereo_link_tf",
        arguments=[
            "0.15",   # x (forward)
            "0.0",    # y (lateral)
            "0.12",   # z (up)
            "0",      # roll
            "0",      # pitch
            "0",      # yaw
            "base_link",
            "stereo_link",
        ],
    )

    return LaunchDescription([
        log_level_arg,
        enable_viz_arg,
        mipi_device_arg,
        LogInfo(msg="[AKSUMAEL] Starting GS130W + hobot_stereonet on RDK X5"),
        LogInfo(msg=[
            "Camera: ", str(GS130W_WIDTH), "x", str(GS130W_HEIGHT),
            "  baseline=", str(GS130W_BASELINE * 1000), " mm",
            "  fx=", str(GS130W_FX),
        ]),
        mipi_cam_node,
        stereonet_node,
        static_tf_node,
    ])
