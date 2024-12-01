import subprocess


def get_ros_env(ros_distro: str) -> dict[str, str]:
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
