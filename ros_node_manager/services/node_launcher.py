import subprocess
import psutil
import time
import logging
from queue import Queue
from typing import Dict, Optional

from ros_node_manager.models import NodeInfo, NodeEvent
from ros_node_manager.services.enviroment import merge_ros_env_with_system

logger = logging.getLogger(__name__)


class NodeLauncher:
    """
    Responsible for launching nodes via 'ros2 run' or 'ros2 launch'.
    Follows a minimal approach for clarity.
    """

    def __init__(self, default_timeout: float = 5.0):
        self.default_timeout = default_timeout

    def launch_node(
        self,
        name: str,
        package: str,
        executable: Optional[str] = None,
        launch_file: Optional[str] = None,
        parameters: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> NodeInfo:
        """
        Launch a node. If 'executable' is specified, we use 'ros2 run'.
        If 'launch_file' is specified, we use 'ros2 launch'.
        """
        # Validate arguments
        if bool(executable) == bool(launch_file):
            raise ValueError(
                "Exactly one of 'executable' or 'launch_file' must be provided."
            )

        is_launch_file = launch_file is not None
        cmd = self._build_command(package, executable, launch_file, parameters)
        env = merge_ros_env_with_system()

        logger.info(f"Launching node '{name}' with command: {' '.join(cmd)}")
        process = self._create_subprocess(cmd, env, name)

        events: Queue[NodeEvent] = Queue()
        events.put(NodeEvent(type_="status", message="Node process launched."))

        # Optionally discover child processes if we used a launch file
        child_procs = []
        launch_timeout = timeout or self.default_timeout
        if is_launch_file:
            child_procs = self._discover_children(
                process.pid, name, launch_timeout, events
            )

        # Construct NodeInfo
        node_info = NodeInfo(
            name=name,
            process=process,
            child_processes=child_procs,
            events_queue=events,
            is_launch_file=is_launch_file,
            state="running",
        )
        logger.info(f"Node '{name}' is now running (PID={process.pid}).")
        return node_info

    def _build_command(
        self,
        package: str,
        executable: Optional[str],
        launch_file: Optional[str],
        parameters: Optional[Dict[str, str]],
    ) -> list[str]:
        """
        Build the command array for subprocess.Popen, ensuring minimal complexity.
        """
        cmd: list[str] = []

        if launch_file:
            cmd = ["ros2", "launch", package, launch_file]
        else:
            assert executable
            cmd = ["ros2", "run", package, executable]

        # Attach parameters
        if parameters:
            for key, val in parameters.items():
                cmd += ["--ros-args", "-p", f"{key}:={val}"]
        return cmd

    def _create_subprocess(
        self, cmd: list[str], env: dict[str, str], node_name: str
    ) -> subprocess.Popen[str]:
        """
        Create the subprocess with standard error handling.
        """
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
                shell=False,
            )
        except FileNotFoundError as e:
            msg = f"Failed to start node '{node_name}': {e}"
            logger.error(msg)
            raise
        except Exception as e:
            logger.exception(f"Unexpected error launching node '{node_name}': {e}")
            raise
        return proc

    def _discover_children(
        self, pid: int, node_name: str, timeout: float, events: Queue[NodeEvent]
    ) -> list[psutil.Process]:
        """
        If using ros2 launch, attempt to find child processes.
        """
        child_procs = []
        try:
            parent_ps = psutil.Process(pid)
            deadline = time.time() + timeout
            while time.time() < deadline:
                children = parent_ps.children(recursive=True)
                if children:
                    child_procs = children
                    pid_list = [c.pid for c in child_procs]
                    logger.info(f"[{node_name}] Found child processes: {pid_list}")
                    events.put(
                        NodeEvent(type_="status", message=f"Children: {pid_list}")
                    )
                    break
                time.sleep(0.5)
        except psutil.NoSuchProcess:
            logger.warning(f"[{node_name}] Process died before child discovery.")
        except Exception as e:
            logger.exception(f"[{node_name}] Error discovering children: {e}")
        return child_procs
