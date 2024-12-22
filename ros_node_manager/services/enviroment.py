import os
import logging
import subprocess

logger = logging.getLogger(__name__)


def merge_ros_env_with_system() -> dict[str, str]:
    """
    Returns a dictionary that merges system environment variables with
    the base ROS environment variables.
    """
    base_env = _get_ros_env(ros_distro="humble")
    final_env = os.environ.copy()
    final_env.update(base_env)

    logger.debug("Merged ROS environment with system environment.")
    return final_env


def _get_ros_env(ros_distro: str) -> dict[str, str]:
    setup_command = f"source /opt/ros/{ros_distro}/setup.sh && env"
    result = subprocess.run(
        ["bash", "-c", setup_command],
        capture_output=True,
        text=True,
        check=True,
    )
    env: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value

    return env
