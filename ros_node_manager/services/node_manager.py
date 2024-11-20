import threading
import subprocess
import selectors

import os
import signal

import logging
import time

from typing import Dict, Optional, Any
from queue import Queue

from ros_node_manager.utils import get_ros_env


logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class NodeManager:
    def __init__(self):
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.monitor_thread = threading.Thread(target=self._monitor_processes, daemon=True)
        self.monitor_thread.start()
        logger.info("NodeManager initialized.")
        self._log_enviroment()

    def _log_enviroment(self):
        critical_vars = ['ROS_DOMAIN_ID', 'RMW_IMPLEMENTATION']
        logger.debug("Main process enviroment variables:")
        for var in critical_vars:
            logger.debug(f"{var}: {os.environ.get(var)}")

    def launch_node(
        self,
        name: str,
        package: str,
        executable: Optional[str] = None,
        launch_file: Optional[str] = None,
        parameters: Optional[Dict[str, str]] = None,
    ) -> None:
        with self.lock:
            if name in self.nodes:
                raise ValueError(f"Node '{name}' is already running.")

            if not executable and not launch_file:
                raise ValueError("Either 'executable' or 'launch_file' must be specified.")

            command = (
                ["ros2", "run", package, executable]
                if executable
                else ["ros2", "launch", package, launch_file]
            )

            if parameters:
                for key, value in parameters.items():
                    command.extend(["--ros-args", "-p", f"{key}:={value}"])

            ros_env = get_ros_env("humble")

            ros_env.setdefault('ROS_DOMAIN_ID', '0')
            ros_env.setdefault('RMW_IMPLEMENTATION', 'rmw_fastrtps_cpp')

            full_env = os.environ.copy()
            full_env.update(ros_env)
            logger.debug(f"Environment for node '{name}': {full_env}")
            
            
            logger.info(f"Launching node '{name}' with command: {' '.join(command)}")

            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=full_env,
                    start_new_session=True,
                )
            except Exception as e:
                logger.error(f"Failed to start node '{name}': {e}")
                raise


            status_queue = Queue()
            output_thread = threading.Thread(
                target=self._monitor_node_output,
                args=(name, process, status_queue),
                daemon=True,
            )
            output_thread.start()


            self.nodes[name] = {
                "process": process,
                "status_queue": status_queue,
                "output_thread": output_thread,
            }
            logger.info(f"Node '{name}' started with PID {process.pid}")

    def terminate_node(self, name: str):
        with self.lock:
            if name not in self.nodes:
                error_msg = f"Node '{name}' is not running."
                logger.error(error_msg)
                raise ValueError(error_msg)

            node_info = self.nodes[name]
            process = node_info["process"]

        try:
            pgid = os.getpgid(process.pid)
            logger.debug(f"Process Group ID (PGID) for node '{name}': {pgid}")


            os.killpg(pgid, signal.SIGINT)
            logger.info(f"Sent SIGINT to node '{name}' (PID {process.pid})")


            try:
                process.wait(timeout=5)
                logger.info(f"Node '{name}' terminated gracefully with SIGINT.")
            except subprocess.TimeoutExpired:
                logger.warning(f"Node '{name}' did not terminate with SIGINT; sending SIGKILL.")
                os.killpg(pgid, signal.SIGKILL)
                process.wait()
                logger.info(f"Node '{name}' forcefully terminated with SIGKILL.")

            # Clean up
            output_thread = node_info["output_thread"]
            output_thread.join(timeout=1)
            if output_thread.is_alive():
                logger.warning(f"Output thread for node '{name}' did not terminate.")

        except ProcessLookupError:
            logger.warning(f"Process for node '{name}' with PID {process.pid} does not exist.")
        except Exception as e:
            logger.exception(f"Error terminating node '{name}': {e}")
            raise
        finally:
            with self.lock:
                self.nodes.pop(name, None)
                logger.info(f"Node '{name}' removed from registry.")


    def _monitor_node_output(self, name: str, process: subprocess.Popen, status_queue: Queue):
        """
        Monitors the subprocess's stdout and stderr streams, logging output in real-time.

        Args:
            name (str): Name of the node.
            process (subprocess.Popen): The subprocess running the node.
            status_queue (Queue): Queue to store status messages.
        """
        sel = selectors.DefaultSelector()
        if process.stdout:
            sel.register(process.stdout, selectors.EVENT_READ, data='stdout')
        if process.stderr:
            sel.register(process.stderr, selectors.EVENT_READ, data='stderr')

        try:
            while True:
                events = sel.select(timeout=1.0)
                if not events:
                    if process.poll() is not None:
                        break
                    continue
                for key, _ in events:
                    fileobj = key.fileobj
                    data_type = key.data
                    line = fileobj.readline()
                    if not line:
                        sel.unregister(fileobj)
                        fileobj.close()
                        continue
                    line = line.strip()
                    if line:
                        if data_type == 'stdout':
                            logger.info(f"[{name}] OUT: {line}")
                        elif data_type == 'stderr':
                            logger.error(f"[{name}] ERR: {line}")
                        status_queue.put(line)
                        
            # read after murdering the process
            for key, _ in events:
                fileobj = key.fileobj
                for line in fileobj:
                    line = line.strip()
                    if line:
                        data_type = key.data
                        if data_type == 'stdout':
                            logger.info(f"[{name}] OUT: {line}")
                        elif data_type == 'stderr':
                            logger.error(f"[{name}] ERR: {line}")
                        status_queue.put(line)
        except Exception as e:
            logger.exception(f"Error reading output for node '{name}': {e}")
        finally:
            sel.close()
            logger.debug(f"Output streams for node '{name}' closed.")

    def _monitor_processes(self):
        """Monitors all running processes and removes any that have stopped unexpectedly."""
        while True:
            time.sleep(5)
            with self.lock:
                for name, node_info in list(self.nodes.items()):
                    process = node_info["process"]
                    if process.poll() is not None:
                        logger.warning(f"Node '{name}' has stopped unexpectedly.")
                        output_thread = node_info["output_thread"]
                        output_thread.join(timeout=1)
                        self.nodes.pop(name)

    def get_node_status(self, name: str) -> list:
        with self.lock:
            if name not in self.nodes:
                raise ValueError(f"Node '{name}' is not running.")

            status_queue = self.nodes[name]["status_queue"]
            messages = []
            while not status_queue.empty():
                messages.append(status_queue.get_nowait())
            return messages

    def list_nodes(self) -> list[str]:
        """
        Returns a list of currently running node names.

        Returns:
            List[str]: A list of node names.
        """
        with self.lock:
            node_list = list(self.nodes.keys())
            logger.debug(f"Currently running nodes: {node_list}")
            return node_list
