import io
import os
import selectors
import logging
import threading
import psutil
from typing import cast

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
                logger.exception(
                    f"[{name}] Unexpected error in monitoring: {e}"
                )
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
                        node_info.events_queue.put(NodeEvent(type_="status", message=msg))
            except psutil.NoSuchProcess:
                # Parent disappeared
                logger.warning(f"[{node_info.name}] Parent vanished unexpectedly.")
            except Exception as e:
                logger.exception(f"[{node_info.name}] Error updating child processes: {e}")
        else:
            # Parent is already dead; _detect_unexpected_stop will handle next

            pass

    def _detect_unexpected_stop(self, nodes: dict[str, NodeInfo], node_info: NodeInfo):
        all_procs = [node_info.process] + node_info.child_processes
        # If they all have .poll() != None, they're dead
        if all(proc.poll() is not None for proc in all_procs):
            msg = f"Node '{node_info.name}' has stopped unexpectedly."
            logger.warning(msg)
            node_info.events_queue.put(NodeEvent(type_="status", message=msg))
            # Remove from registry
            nodes.pop(node_info.name, None)


class OutputMonitor:
    """
    Captures stdout/stderr in a thread for a given node, 
    storing lines in node_info.events_queue as 'log' NodeEvents.
    """

    def __init__(self):
        pass

    def start_capture(self, node_info: NodeInfo):
        t = threading.Thread(
            target=self._capture_output,
            args=(node_info,),
            daemon=True
        )
        # Optionally store t in node_info if we want to join later
        # node_info.output_thread = t
        t.start()

    def _capture_output(self, node_info: NodeInfo):
        process = node_info.process
        events_queue = node_info.events_queue

        sel = selectors.DefaultSelector()
        buffers = {"stdout": "", "stderr": ""}

        # Register stdout/stderr (non-blocking)
        try:
            if process.stdout:
                os.set_blocking(process.stdout.fileno(), False)
                sel.register(process.stdout, selectors.EVENT_READ, data="stdout")
            if process.stderr:
                os.set_blocking(process.stderr.fileno(), False)
                sel.register(process.stderr, selectors.EVENT_READ, data="stderr")
        except Exception as e:
            logger.exception(f"[{node_info.name}] Failed to set non-blocking I/O: {e}")
            events_queue.put(NodeEvent(type_="error", message=str(e)))
            return  # Can't capture output if we fail here

        try:
            while True:
                events = sel.select(timeout=1.0)
                if not events and process.poll() is not None:
                    # No events, parent ended
                    break

                for key, _ in events:
                    fileobj = cast(io.TextIOWrapper, key.fileobj)
                    stream_type = key.data
                    try:
                        data = os.read(fileobj.fileno(), 4096)
                    except BlockingIOError:
                        continue
                    except Exception as e:
                        logger.exception(
                            f"[{node_info.name}] Error reading {stream_type}: {e}"
                        )
                        events_queue.put(NodeEvent(type_="error", message=str(e), stream=stream_type))
                        sel.unregister(fileobj)
                        fileobj.close()
                        continue

                    if not data:
                        # EOF
                        sel.unregister(fileobj)
                        fileobj.close()
                        continue

                    # decode text
                    try:
                        text_data = data.decode()
                    except UnicodeDecodeError as e:
                        text_data = data.decode(errors="replace")
                        logger.warning(
                            f"[{node_info.name}] Decode error on {stream_type}: {e}. Using 'replace' mode."
                        )

                    # process lines
                    buffers[stream_type] += text_data
                    lines = buffers[stream_type].split("\n")
                    buffers[stream_type] = lines.pop()  # leftover partial line
                    for line in lines:
                        line = line.strip()
                        if line:
                            evt = NodeEvent(type_="log", message=line, stream=stream_type)
                            events_queue.put(evt)
                            if stream_type == "stdout":
                                logger.info(f"[{node_info.name}] OUT: {line}")
                            else:
                                logger.error(f"[{node_info.name}] ERR: {line}")
        finally:
            # Cleanup
            sel.close()
            events_queue.put(NodeEvent(type_="status", message="Output capture finished."))
            logger.debug(f"[{node_info.name}] Output capture thread ended.")
