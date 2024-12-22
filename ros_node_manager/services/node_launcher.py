import subprocess
import psutil
import time
import logging
from queue import Queue
from typing import Dict, Optional

from .enviroment import merge_ros_env_with_system
from ros_node_manager.models import NodeInfo, NodeEvent

logger = logging.getLogger(__name__)


class NodeLauncher:
    """
    Responsible solely for launching nodes (either `ros2 run` or `ros2 launch`)
    and doing an initial child process discovery.
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
        Launch either `ros2 run` (if executable is given)
        or `ros2 launch` (if launch_file is given).
        Returns a `NodeInfo` object with initial process references.
        """
        if (executable is None and launch_file is None) or (executable and launch_file):
            raise ValueError("Specify exactly one of 'executable' or 'launch_file'.")

        is_launch_file = launch_file is not None

        # Build the command
        if is_launch_file:
            command = ["ros2", "launch", package, launch_file]
        else:
            # type check assitance
            assert executable is not None
            command = ["ros2", "run", package, executable]

        # Append parameters
        if parameters:
            for k, v in parameters.items():
                command.extend(["--ros-args", "-p", f"{k}:={v}"])

        env = merge_ros_env_with_system()

        logger.info(f"Launching node '{name}' with command: {' '.join(command)}")
        for var in ["ROS_DOMAIN_ID", "RMW_IMPLEMENTATION"]:
            logger.debug(f"{var}={env.get(var)}")

        # Create the subprocess
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
        except FileNotFoundError:
            msg = f"Failed to start node '{name}': command not found."
            logger.exception(msg)
            raise RuntimeError(msg)
        except Exception as e:
            msg = f"Failed to start node '{name}': {e}"
            logger.exception(msg)
            raise RuntimeError(msg)

        events_queue: Queue[NodeEvent] = Queue()
        events_queue.put(NodeEvent(type_="status", message="Node started."))

        child_procs = []
        launch_timeout = timeout if timeout is not None else self.default_timeout

        if is_launch_file:
            try:
                parent_ps = psutil.Process(process.pid)
                deadline = time.time() + launch_timeout
                while time.time() < deadline:
                    children = parent_ps.children(recursive=True)
                    if children:
                        child_procs = children
                        break
                    time.sleep(0.5)
                if child_procs:
                    pids = [c.pid for c in child_procs]
                    logger.info(f"[{name}] Found initial child processes: {pids}")
                    events_queue.put(
                        NodeEvent(
                            type_="status",
                            message=f"Discovered initial children: {pids}",
                        )
                    )
                else:
                    warning_msg = f"[{name}] No child processes detected within {launch_timeout} sec."
                    logger.warning(warning_msg)
                    events_queue.put(NodeEvent(type_="warning", message=warning_msg))
            except psutil.NoSuchProcess:
                # Could happen if parent disappeared instantly
                error_msg = f"[{name}] Launch process died immediately."
                logger.error(error_msg)
                events_queue.put(NodeEvent(type_="error", message=error_msg))
            except Exception as e:
                # Catch any unexpected psutil errors
                error_msg = f"[{name}] Error discovering child processes: {e}"
                logger.exception(error_msg)
                events_queue.put(NodeEvent(type_="error", message=error_msg))

        node_info = NodeInfo(
            name=name,
            process=process,
            child_processes=child_procs,
            events_queue=events_queue,
            is_launch_file=is_launch_file,
            state="running",
        )

        logger.info(f"Node '{name}' is now running (PID={process.pid}).")
        return node_info
