import threading
import subprocess
import psutil
import selectors

import os
import signal

import logging
import time

from typing import Dict, Optional, Any
from queue import Queue

from ros_node_manager.utils import get_ros_env


logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


class NodeManager:
    def __init__(self):
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.monitor_thread = threading.Thread(
            target=self._monitor_processes, daemon=True
        )
        self.monitor_thread.start()
        logger.info("NodeManager initialized.")
        self._log_enviroment()

    def _log_enviroment(self):
        critical_vars = ["ROS_DOMAIN_ID", "RMW_IMPLEMENTATION"]
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

            if (not executable) != (not launch_file):
                raise ValueError(
                    "Either 'executable' or 'launch_file' must be specified."
                )

            if executable:
                command = ["ros2", "run", package, executable]
            elif launch_file:
                command = ["ros2", "launch", package, launch_file]
            else:
                raise RuntimeError("Neither 'executable' or 'launch_file' were defined")
            

            if parameters:
                for key, value in parameters.items():
                    command.extend(["--ros-args", "-p", f"{key}:={value}"])

            ros_env = get_ros_env("humble")

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

            status_queue: Queue[str] = Queue()
            output_thread = threading.Thread(
                target=self._monitor_node_output,
                args=(name, process, status_queue),
                daemon=True,
            )
            output_thread.start()
            if launch_file:
                # FIXME: Going to sleep here but this should be handled differently
                time.sleep(2)
                parent = psutil.Process(process.pid)
                child_processes = parent.children(recursive=True)
                logger.info(f"Node '{name}' launched with child processes: {[p.pid for p in child_processes]}")
            else:
                child_processes = []
            
            
            self.nodes[name] = {
                "process": process,
                "child_processes" : child_processes,
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
            child_processes = node_info.get("child_processes", [])

        try:
            for child in child_processes:
                try:
                    psutil.Process.send_signal(child, signal.SIGINT) 
                    logger.info(f"Sent SIGTERM to child process PID {child.pid} of '{name}'")
                except psutil.NoSuchProcess:
                    logger.warning(f"Child process PID {child.pid} of node '{name}' does not exist")

            pgid = os.getpgid(process.pid)
            logger.debug(f"Process Group ID (PGID) for node '{name}': {pgid}")

            os.killpg(pgid, signal.SIGINT)
            logger.info(f"Sent SIGINT to node '{name}' (PID {process.pid})")

            try:
                process.wait(timeout=5)
                logger.info(f"Node '{name}' terminated gracefully with SIGINT.")
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"Node '{name}' did not terminate with SIGINT; sending SIGKILL."
                )
                os.killpg(pgid, signal.SIGKILL)
                process.wait()
                logger.info(f"Node '{name}' forcefully terminated with SIGKILL.")


            for child in child_processes:
                if child.is_running():
                    try:
                        child.kill()
                        logger.info(f"Forcefully killed child process PID {child.pid} of node '{name}'")
                    except psutil.NoSuchProcess:
                        logger.warning(f"Child process PID {child.pid} of node '{name}' does not exist")


            # Clean up
            output_thread = node_info["output_thread"]
            output_thread.join(timeout=1)
            if output_thread.is_alive():
                logger.warning(f"Output thread for node '{name}' did not terminate.")

        except ProcessLookupError:
            logger.warning(
                f"Process for node '{name}' with PID {process.pid} does not exist."
            )
        except Exception as e:
            logger.exception(f"Error terminating node '{name}': {e}")
            raise
        finally:
            with self.lock:
                self.nodes.pop(name, None)
                logger.info(f"Node '{name}' removed from registry.")
    
    def _monitor_node_output(self, name: str, process: subprocess.Popen[Any], status_queue: Queue[str]):
        """
        Monitors the subprocess's stdout and stderr streams, logging output in real-time.

        Args:
            name (str): Name of the node.
            process (subprocess.Popen): The subprocess running the node.
            status_queue (Queue): Queue to store status messages.
        """
        sel = selectors.DefaultSelector()
        buffers = {'stdout': '', 'stderr': ''}

        # Set stdout and stderr to non-blocking mode and register them with the selector
        if process.stdout:
            os.set_blocking(process.stdout.fileno(), False)
            sel.register(process.stdout, selectors.EVENT_READ, data="stdout")
        if process.stderr:
            os.set_blocking(process.stderr.fileno(), False)
            sel.register(process.stderr, selectors.EVENT_READ, data="stderr")

        try:
            while True:
                events = sel.select(timeout=1.0)
                if not events:
                    # Check if the process has terminated
                    if process.poll() is not None:
                        break
                    continue

                for key, _ in events:
                    fileobj = key.fileobj
                    data_type = key.data
                    try:
                        # Read available data without blocking
                        data = os.read(fileobj.fileno(), 4096).decode()
                        if not data:
                            # EOF reached
                            sel.unregister(fileobj)
                            fileobj.close()
                            continue
                        buffers[data_type] += data
                        lines = buffers[data_type].split('\n')
                        buffers[data_type] = lines.pop()  # Save incomplete line for next read
                        for line in lines:
                            line = line.strip()
                            if line:
                                if data_type == "stdout":
                                    logger.info(f"[{name}] OUT: {line}")
                                elif data_type == "stderr":
                                    logger.error(f"[{name}] ERR: {line}")
                                status_queue.put(line)
                    except BlockingIOError:
                        # No data available right now
                        continue

            # Read any remaining data after the process has exited
            for key in sel.get_map().values():
                fileobj = key.fileobj
                data_type = key.data
                while True:
                    try:
                        data = os.read(fileobj.fileno(), 4096).decode()
                        if not data:
                            break
                        buffers[data_type] += data
                    except BlockingIOError:
                        break
                lines = buffers[data_type].split('\n')
                for line in lines:
                    line = line.strip()
                    if line:
                        if data_type == "stdout":
                            logger.info(f"[{name}] OUT: {line}")
                        elif data_type == "stderr":
                            logger.error(f"[{name}] ERR: {line}")
                        status_queue.put(line)
                sel.unregister(fileobj)
                fileobj.close()

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
                    child_processes = node_info.get("child_processes", [])
                    all_processes = [process] + child_processes
                    terminated = all(proc.poll() is not None for proc in all_processes)
                    if terminated:
                        logger.warning(f"Node '{name}' has stopped unexpectedly.")
                        output_thread = node_info["output_thread"]
                        output_thread.join(timeout=1)
                        self.nodes.pop(name) 

    def get_node_status(self, name: str) -> list[str]:
        with self.lock:
            if name not in self.nodes:
                raise ValueError(f"Node '{name}' is not running.")

            status_queue = self.nodes[name]["status_queue"]
            messages: list[str] = []
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
