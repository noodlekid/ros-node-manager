import os
import subprocess
import threading
import time
import psutil
import logging
from typing import Dict, Optional
from ros_node_manager.models import NodeInfo, NodeEvent
from ros_node_manager.services.node_launcher import NodeLauncher
from ros_node_manager.services.node_status_monitor import NodeMonitor
from ros_node_manager.services.node_output_monitor import OutputMonitor

import signal

logger = logging.getLogger(__name__)


class NodeManager:
    """
    High-level orchestrator for node launching, termination,
    and background monitoring.
    """

    def __init__(
        self,
        default_timeout: float = 5.0,
        monitor_interval: float = 3.0,
        verbosity: int = 0,
    ):
        self.nodes: Dict[str, NodeInfo] = {}
        self._lock = threading.Lock()

        self.launcher = NodeLauncher(default_timeout=default_timeout)
        self.monitor = NodeMonitor(interval=monitor_interval)
        self.output_monitor = OutputMonitor()  # optional
        self.verbosity = verbosity

        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info("NodeManager initialized.")

    def _monitor_loop(self):
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
                timeout=timeout,
            )
            self.nodes[name] = node_info

        # Start capturing stdout/stderr only if verbosity is high
        if self.verbosity > 1 and hasattr(self, "output_monitor"):
            self.output_monitor.start_capture(node_info)

        return node_info

    def terminate_node(self, name: str, grace_timeout: float = 5.0):
        with self._lock:
            if name not in self.nodes:
                logger.warning(f"Node '{name}' not found.")
                return
            node_info = self.nodes[name]

        self._terminate_process_tree(node_info, grace_timeout)
        with self._lock:
            self.nodes.pop(name, None)
            logger.info(f"[{name}] Removed from registry after termination.")

    def _terminate_process_tree(self, node_info: NodeInfo, grace_timeout: float):
        main_proc = node_info.process
        children = node_info.child_processes
        logger.info(f"Terminating '{node_info.name}' (PID={main_proc.pid})")

        # Send SIGINT to children first
        for child in children:
            try:
                if child.is_running():
                    child.send_signal(signal.SIGINT)
            except Exception as e:
                logger.exception(f"Error sending SIGINT to child {child.pid}: {e}")

        # SIGINT the parent
        try:
            pgid = os.getpgid(main_proc.pid)
            os.killpg(pgid, signal.SIGINT)
            ret = main_proc.wait(timeout=grace_timeout)
            logger.info(f"[{node_info.name}] Exited with code {ret}")
        except subprocess.TimeoutExpired:
            logger.warning(f"[{node_info.name}] Force killing after {grace_timeout}s.")
            os.killpg(pgid, signal.SIGKILL)  # type: ignore
            main_proc.wait()
        except Exception as e:
            logger.exception(f"[{node_info.name}] Error during termination: {e}")

        # Cleanup leftover children (SIGKILL if necessary)
        for child in children:
            if child.is_running():
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass

    def list_nodes(self) -> list[str]:
        with self._lock:
            return list(self.nodes.keys())

    def get_node_status(self, name: str) -> list[NodeEvent]:
        with self._lock:
            if name not in self.nodes:
                raise ValueError(f"Node '{name}' not found.")
            events_q = self.nodes[name].events_queue

        events: list[NodeEvent] = []
        while not events_q.empty():
            events.append(events_q.get_nowait())
        return events
