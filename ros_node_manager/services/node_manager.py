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
from dataclasses import dataclass
from ros_node_manager.utils import get_ros_env


logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class NodeEvent:
    type: str
    message: str
    stream: Optional[str] = None


@dataclass
class NodeInfo:
    process: subprocess.Popen[Any]
    child_processes: list[psutil.Process]
    events_queue: Queue[NodeEvent]
    output_thread: threading.Thread
    state: str = ""


class NodeManager:
    def __init__(self):
        self.nodes: Dict[str, NodeInfo] = {}

        self._lock = threading.Lock()
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
        timeout: float = 5.0,
    ) -> None:
        with self._lock:
            if name in self.nodes:
                raise ValueError(f"Node '{name}' is already running.")

            if (not executable) == (not launch_file):
                raise ValueError(
                    "Must specify exactly one of 'executable' or 'launch_file'"
                )

            if executable:
                command = ["ros2", "run", package, executable]
            else:
                command = ["ros2", "launch", package, launch_file]

            if parameters:
                for key, value in parameters.items():
                    command.extend(["--ros-args", "-p", f"{key}:={value}"])

            ros_env = get_ros_env("humble")

            full_env = os.environ.copy()
            full_env.update(ros_env)
            logger.debug(f"Environment for node '{name}': {full_env}")
            logger.info(f"Launching node '{name}' with command: {' '.join(command)}")
            
            process = None
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

            events_queue: Queue[NodeEvent] = Queue()
            events_queue.put(NodeEvent(type="status", message="Node started."))

            output_thread = threading.Thread(
                target=self._monitor_node_output,
                args=(name, process, events_queue),
                daemon=True,
            )
            output_thread.start()

            child_processes = []
            if launch_file:
                parent = psutil.Process(process.pid)
                deadline = time.time() + timeout
                while time.time() < deadline:
                    children = parent.children(recursive=True)
                    if children:
                        child_processes = children
                        break
                    time.sleep(0.5)

                if not child_processes:
                    events_queue.put(NodeEvent(type="status", message="No child process detected within timeout"))
                    logger.warning(
                        f"No child processes found for node '{name}' after waiting."
                    )
                else:
                    pids = [p.pid for p in child_processes]
                    events_queue.put(NodeEvent(type="status", message=f"Child processes found: {pids}"))
                    logger.info(
                        f"Node '{name}' launched with child processes: {[p.pid for p in child_processes]}"
                    )
            else:
                child_processes = []
            
            events_queue.put(NodeEvent(type="status", message="Node running"))
            new_node = NodeInfo(process, child_processes, events_queue, output_thread, state="running")
            self.nodes[name] = new_node
            logger.info(f"Node '{name}' started with PID {process.pid}")

    def terminate_node(self, name: str):
        with self._lock:
            if name not in self.nodes:
                error_msg = f"Node '{name}' is not running."
                logger.error(error_msg)
                raise ValueError(error_msg)

            node_info = self.nodes[name]
            process = node_info.process
            child_processes = node_info.child_processes
            events_queue = node_info.events_queue

        try:
            for child_proc in child_processes:
                try:
                    child_proc.send_signal(signal.SIGINT)
                    logger.info(
                        f"Sent SIGINT to child process PID {child_proc.pid} of '{name}'"
                    )
                except psutil.NoSuchProcess:
                    logger.warning(
                        f"Child process PID {child_proc.pid} of node '{name}' does not exist"
                    )

            pgid = os.getpgid(process.pid)
            logger.debug(f"Process Group ID (PGID) for node '{name}': {pgid}")

            os.killpg(pgid, signal.SIGINT)
            logger.info(f"Sent SIGINT to node '{name}' (PID {process.pid})")

            try:
                process.wait(timeout=5)
                logger.info(f"Node '{name}' terminated gracefully with SIGINT.")
                events_queue.put(NodeEvent(type="status", message="Node termintated gracefully"))
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"Node '{name}' did not terminate with SIGINT; sending SIGKILL."
                )
                os.killpg(pgid, signal.SIGKILL)
                process.wait()
                logger.info(f"Node '{name}' forcefully terminated with SIGKILL.")
                events_queue.put(NodeEvent(type="status", message="Node terminated forcefully"))

            for child in child_processes:
                if child.is_running():
                    try:
                        child.kill()
                        logger.info(
                            f"Forcefully killed child process PID {child.pid} of node '{name}'"
                        )
                    except psutil.NoSuchProcess:
                        logger.warning(
                            f"Child process PID {child.pid} of node '{name}' does not exist"
                        )

            # Clean up
            output_thread = node_info.output_thread
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
            with self._lock:
                self.nodes.pop(name, None)
                logger.info(f"Node '{name}' removed from registry.")


    def _monitor_node_output(self, name: str, process: subprocess.Popen[Any], events_queue: Queue[NodeEvent]):
        """
        Monitors the subprocess's stdout and stderr streams, logging output in real-time as structured events.
        """
        sel = selectors.DefaultSelector()
        buffers = {'stdout': '', 'stderr': ''}

        # Make stdout and stderr non-blocking if available, and register them with the selector
        if process.stdout:
            os.set_blocking(process.stdout.fileno(), False)
            sel.register(process.stdout, selectors.EVENT_READ, data="stdout")
        if process.stderr:
            os.set_blocking(process.stderr.fileno(), False)
            sel.register(process.stderr, selectors.EVENT_READ, data="stderr")

        try:
            while True:
                events = sel.select(timeout=1.0)
                # If no events, check if process ended
                if not events and process.poll() is not None:
                    break

                for key, _ in events:
                    fileobj = key.fileobj
                    data_type = key.data
                    try:
                        data = os.read(fileobj.fileno(), 4096).decode()
                    except BlockingIOError:
                        # No data available right now, try again next iteration
                        continue
                    except Exception as e:
                        # Log any unexpected read errors
                        logger.exception(f"Unexpected error reading output for node '{name}' from {data_type}: {e}")
                        events_queue.put(NodeEvent(type="status", message=f"Error reading output: {e}"))
                        # Unregister and close this stream to prevent further issues
                        sel.unregister(fileobj)
                        fileobj.close()
                        continue

                    if not data:
                        # EOF reached
                        sel.unregister(fileobj)
                        fileobj.close()
                        continue

                    buffers[data_type] += data
                    lines = buffers[data_type].split('\n')
                    buffers[data_type] = lines.pop()  # save incomplete line
                    for line in lines:
                        line = line.strip()
                        if line:
                            evt = NodeEvent(type="log", message=line, stream=data_type)
                            events_queue.put(evt)
                            if data_type == "stdout":
                                logger.info(f"[{name}] OUT: {line}")
                            else:
                                logger.error(f"[{name}] ERR: {line}")

            # Process any leftover data after exit
            for key in list(sel.get_map().values()):
                fileobj = key.fileobj
                data_type = key.data
                while True:
                    try:
                        data = os.read(fileobj.fileno(), 4096)
                    except BlockingIOError:
                        # Nothing more to read now
                        break
                    except Exception as e:
                        logger.exception(f"Unexpected error reading leftover output for node '{name}': {e}")
                        events_queue.put(NodeEvent(type="status", message=f"Error reading leftover output: {e}"))
                        break

                    if not data:
                        # EOF or no more data
                        break

                    # Decode and process leftover data
                    try:
                        decoded = data.decode()
                    except Exception as e:
                        logger.exception(f"Decoding error for node '{name}' output: {e}")
                        events_queue.put(NodeEvent(type="status", message=f"Decoding error: {e}"))
                        break

                    buffers[data_type] += decoded
                # Process any final lines in buffer
                lines = buffers[data_type].split('\n')
                for line in lines:
                    line = line.strip()
                    if line:
                        evt = NodeEvent(type="log", message=line, stream=data_type)
                        events_queue.put(evt)
                        if data_type == "stdout":
                            logger.info(f"[{name}] OUT: {line}")
                        else:
                            logger.error(f"[{name}] ERR: {line}")

                # Clean up
                try:
                    sel.unregister(fileobj)
                except KeyError:
                    # Already unregistered
                    pass
                fileobj.close()

        except Exception as e:
            # Catch any unexpected top-level exceptions in the monitoring loop
            logger.exception(f"Error reading output for node '{name}': {e}")
            events_queue.put(NodeEvent(type="status", message=f"Error reading output: {e}"))
        finally:
            sel.close()
            logger.debug(f"Output streams for node '{name}' closed.")
            # Once we reach here, we've stopped reading. Signal that monitoring ended.
            events_queue.put(NodeEvent(type="status", message="Node output monitoring ended."))


    def _monitor_processes(self):
        """Monitors all running processes and removes any that have stopped unexpectedly."""
        while True:
            time.sleep(5)
            with self._lock:
                for name, node_info in list(self.nodes.items()):
                    process = node_info.process
                    child_processes = node_info.child_processes
                    all_processes = [process] + child_processes
                    terminated = all(proc.poll() is not None for proc in all_processes)

                    if terminated:
                        logger.warning(f"Node '{name}' has stopped unexpectedly.")
                        # Record a status event before removal
                        node_info.events_queue.put(
                            NodeEvent(type="status", message="Node stopped unexpectedly")
                        )
                        # Wait for output thread to finish
                        node_info.output_thread.join(timeout=1)
                        self.nodes.pop(name, None)
    
    def get_node_events(self, name: str) -> list[NodeEvent]:
        with self._lock:
            if name not in self.nodes:
                raise ValueError(f"Node '{name}' is not running.")
            events_queue = self.nodes[name].events_queue

        messages: list[NodeEvent] = []
        while not events_queue.empty():
            messages.append(events_queue.get_nowait())
        return messages

    def list_nodes(self) -> list[str]:
        """
        Returns a list of currently running node names.

        Returns:
            List[str]: A list of node names.
        """
        with self._lock:
            node_list = list(self.nodes.keys())
            logger.debug(f"Currently running nodes: {node_list}")
            return node_list
