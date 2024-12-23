#!/usr/bin/env bash
set -e

# Source ROS 2 Humble environment
source /opt/ros/humble/setup.bash

# Execute the passed command
exec "$@"
