import logging
import subprocess
import psutil
from typing import Union

from ros_node_manager.models.node_info import NodeInfo, NodeEvent

logger = logging.getLogger(__name__)


class NodeMonitor:
    """
    Periodically scans NodeInfo objects to:
      - Discover new child processes
      - Detect if everything has died => remove from registry
    """

    def __init__(self, interval: float = 3.0):
        self.interval = interval

    def monitor(self, nodes: dict[str, NodeInfo]):
        """
        Called periodically from a background thread (in NodeManager).
        """
        for name, node_info in list(nodes.items()):
            try:
                self._update_child_processes(node_info)
                self._detect_unexpected_stop(nodes, node_info)
            except Exception as e:
                # Catch unexpected errors so we don't kill the entire loop.
                logger.exception(f"[{name}] Unexpected error in monitoring: {e}")
                # We do NOT remove the node here automatically,
                # because it might be a transient psutil glitch.

    def _update_child_processes(self, node_info: NodeInfo):
        parent_proc = node_info.process
        if parent_proc.poll() is None:
            # Parent is alive
            try:
                ps_parent = psutil.Process(parent_proc.pid)
                new_children = ps_parent.children(recursive=True)
                known_pids = {c.pid for c in node_info.child_processes}
                for ch in new_children:
                    if ch.pid not in known_pids:
                        node_info.child_processes.append(ch)
                        msg = f"[{node_info.name}] Discovered new child PID={ch.pid}"
                        logger.info(msg)
                        node_info.events_queue.put(
                            NodeEvent(type_="status", message=msg)
                        )
            except psutil.NoSuchProcess:
                # Parent disappeared
                logger.warning(f"[{node_info.name}] Parent vanished unexpectedly.")
            except Exception as e:
                logger.exception(
                    f"[{node_info.name}] Error updating child processes: {e}"
                )
        else:
            # Parent is already dead; _detect_unexpected_stop will handle next

            pass

    def _detect_unexpected_stop(self, nodes: dict[str, NodeInfo], node_info: NodeInfo):
        all_dead = all(
            self.is_dead(p) for p in ([node_info.process] + node_info.child_processes)
        )
        if all_dead:
            msg = f"Node '{node_info.name}' has stopped unexpectedly."
            logger.warning(msg)
            node_info.events_queue.put(NodeEvent(type_="status", message=msg))
            # Remove from registry
            nodes.pop(node_info.name, None)

    def is_dead(self, proc: Union[psutil.Process, subprocess.Popen[str]]) -> bool:
        """
        Returns True if the process is dead, False otherwise.
        """
        if isinstance(proc, psutil.Process):
            return not proc.is_running()
        return proc.poll() is not None
