import threading
import subprocess
import psutil

import os
import signal
import logging
import time
from typing import Dict, Optional

from enum import IntEnum
from ros_node_manager.services.node_launcher import NodeLauncher
from ros_node_manager.services.node_monitor import NodeMonitor, OutputMonitor
from ros_node_manager.models import NodeInfo, NodeEvent

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

class VerbosityLevels(IntEnum):
    NORMAL = 1
    DEBUG = 2

class NodeManager:
    def __init__(self, default_timeout: float = 5.0, monitor_interval: float = 5.0, verbosity: int = 0):
        self.nodes: Dict[str, NodeInfo] = {}
        self._lock = threading.Lock()

        self.launcher = NodeLauncher(default_timeout=default_timeout)
        self.monitor = NodeMonitor(interval=monitor_interval)
        self.output_monitor = OutputMonitor()
        self.verbosity = verbosity

        self.monitor_thread = threading.Thread(
            target=self._monitor_worker, daemon=True
        )
        self.monitor_thread.start()
        logger.info("NodeManager initialized.")

    def _monitor_worker(self):
        while True:
            time.sleep(self.monitor.interval)
            with self._lock:
                self.monitor.monitor(self.nodes)

    def launch_node(
        self,
        name: str,
        package: str,
        executable: Optional[str] = None,
        launch_file: Optional[str] = None,
        parameters: Optional[Dict[str, str]] = None,
        timeout: float = 5.0,
    ) -> NodeInfo:
        with self._lock:
            if name in self.nodes:
                raise RuntimeError(f"Node '{name}' is already running.")
            
            node_info = self.launcher.launch_node(
                name=name,
                package=package,
                executable=executable,
                launch_file=launch_file,
                parameters=parameters, 
                timeout=timeout
            )
            self.nodes[name] = node_info

            if self.verbosity > VerbosityLevels.NORMAL:
                self.output_monitor.start_capture(node_info)

            return node_info
    
    def list_nodes(self) -> list[str]:
        with self._lock:
            return list(self.nodes.keys())
        
    

    def terminate_node(self, name: str, grace_timeout: float = 5.0):
        """
        Attempts graceful termination (SIGINT) of the node and all discovered children.
        If the parent process does not exit within grace_timeout, we use SIGKILL.
        """
        with self._lock:
            if name not in self.nodes:
                logger.warning(f"Node '{name}' not found for termination.")
                return
            node_info = self.nodes[name]

        process = node_info.process
        child_processes = node_info.child_processes
        events_queue = node_info.events_queue

        logger.info(f"Terminating node '{name}' (PID={process.pid})")

        try:
            # 1) SIGINT to children
            for child in child_processes:
                if child.is_running():
                    try:
                        child.send_signal(signal.SIGINT)
                        logger.debug(f"[{name}] Sent SIGINT to child PID={child.pid}")
                    except psutil.NoSuchProcess:
                        logger.debug(f"[{name}] Child PID={child.pid} already gone.")
                    except Exception as e:
                        logger.exception(f"[{name}] Error sending SIGINT to child: {e}")

            # 2) SIGINT to parent process group
            try:
                pgid = os.getpgid(process.pid)
                os.killpg(pgid, signal.SIGINT)
                logger.debug(f"[{name}] Sent SIGINT to PGID={pgid}")

                ret_code = process.wait(timeout=grace_timeout)
                logger.info(f"[{name}] Terminated gracefully with exit code={ret_code}")
                events_queue.put(NodeEvent(type_="status", message="Terminated gracefully."))
            except subprocess.TimeoutExpired:
                logger.warning(f"[{name}] Did not terminate in {grace_timeout}s, sending SIGKILL.")
                try:
                    os.killpg(pgid, signal.SIGKILL)
                    process.wait()  # wait for kill
                except Exception as e:
                    logger.exception(f"[{name}] Failed to force-kill: {e}")
                else:
                    logger.info(f"[{name}] Forcefully ki lled.")
                    events_queue.put(NodeEvent(type_="status", message="Terminated forcefully."))
            except psutil.NoSuchProcess:
                logger.info(f"[{name}] Already gone before SIGINT.")
            except ProcessLookupError:
                logger.info(f"[{name}] Process or group not found.")
            except Exception as e:
                logger.exception(f"[{name}] Unexpected error during termination: {e}")

            # 3) Ensure child processes are not still running
            for child in child_processes:
                if child.is_running():
                    try:
                        child.kill()
                        logger.debug(f"[{name}] Force-killed child PID={child.pid}")
                    except psutil.NoSuchProcess:
                        pass

        finally:
            # Clean up
            with self._lock:
                self.nodes.pop(name, None)
                logger.info(f"[{name}] Removed from registry after termination.")

    
    def get_node_status(self, name: str) -> list[NodeEvent]:
        with self._lock:
            if name not in self.nodes:
                raise ValueError(f"Node '{name}' is not running.")
            events_queue = self.nodes[name].events_queue

        messages: list[NodeEvent] = []
        while not events_queue.empty():
            messages.append(events_queue.get_nowait())
        return messages
