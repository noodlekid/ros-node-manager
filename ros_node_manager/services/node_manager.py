from multiprocessing import Process, Queue
import os

import subprocess
import selectors
import time
import signal 
import logging
import threading

from ros_node_manager.utils import get_ros_env

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

class NodeManager:
    def __init__(self):
        self.nodes: dict[str, Process] = {}
        self.status_queues: dict[str, Queue] = {}
        self.lock = threading.Lock()
        self.monitor_thread = threading.Thread(target=self._monitor_processes, daemon=True)
        self.monitor_thread.start()

    def launch_node(
        self,
        name: str,
        package: str,
        executable: str | None = None,
        launch_file: str | None = None,
        parameters: dict[str, str] | None = None,
    ) -> None:

        if name in self.nodes:
            raise ValueError(f"Node '{name}' is already running.")

        if not executable and not launch_file:
            raise ValueError("Either 'executable' or 'launch_file' must be specified.")

        status_queue = Queue()
        process = Process(
            target=self._node_worker,
            args=(name, package, executable, launch_file, parameters, status_queue),
            daemon=True,
        )

        process.start()

        self.nodes[name] = process
        logger.info(f"Node '{name}' started with PID {process.pid}.")

        self.status_queues[name] = status_queue

    def terminate_node(self, name: str):
        with self.lock:
            if name not in self.nodes:
                raise ValueError(f"Node '{name}' is not running.")

            process = self.nodes[name]
            if process.is_alive():
                threading.Thread(
                    target=self._wait_for_termination,
                    args=(name, process),
                    daemon=True,
                ).start()
                logger.info(f"Termination initiated for node '{name}' with PID {process.pid}.")
            else:
                logger.warning(f"Node '{name}' was not alive when terminate was called.")

    def _wait_for_termination(self, name: str, process: Process):
        process.join(timeout=5)
        if process.is_alive():
            logger.error(f"Node '{name}' did not terminate within timeout. Force killing subprocess.")
            # Instead of killing the process group, target the subprocess directly
            try:
                os.kill(process.pid, signal.SIGKILL)  # Kill only this specific process
                process.join(timeout=5)
            except ProcessLookupError:
                logger.warning(f"Process for node '{name}' with PID {process.pid} was already terminated.")
        
        if process.is_alive():
            logger.error(f"Failed to terminate node '{name}' with PID {process.pid}")
            raise RuntimeError(f"Node '{name}' did not terminate as expected.")
        else:
            logger.info(f"Node '{name}' with PID {process.pid} terminated successfully.")

        # Remove node after ensuring termination
        with self.lock:
            self.nodes.pop(name, None)
            self.status_queues.pop(name, None)
        
    def get_node_status(self, name: str) -> list[str]:
        if name not in self.status_queues:
            raise ValueError(f"Node '{name}' is not found")

        queue = self.status_queues[name]
        messages = []

        while not queue.empty():
            messages.append(queue.get_nowait())

        return messages

    @staticmethod
    def _node_worker(
        name: str,
        package: str,
        executable: str | None,
        launch_file: str | None,
        parameters: dict[str, str] | None,
        status_queue: Queue,
    ) -> None:
        command = (
            ["ros2", "run", package, executable]
            if executable
            else ["ros2", "launch", package, launch_file]
        )

        if parameters:
            for key, value in parameters.items():
                command.extend(["--ros-args", "-p", f"{key}:={value}"])

        process = None
        try:
            status_queue.put(f"Starting node: {name}")
            ros_env = get_ros_env("humble")
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, **ros_env},
            )

            sel = selectors.DefaultSelector()

            if process.stdout:
                sel.register(process.stdout.fileno(), selectors.EVENT_READ, data="stdout")
            if process.stderr:
                sel.register(process.stderr.fileno(), selectors.EVENT_READ, data="stderr")

            while True:
                retcode = process.poll()
                if retcode is not None:  # Process has exited
                    status_queue.put(f"Node '{name}' exited with code {retcode}.")
                    break

                for key, _ in sel.select(timeout=1):
                    fd = key.fileobj
                    stream_type = key.data
                    data = os.read(fd, 4096).decode("utf-8")  # Non-blocking read
                    if data:
                        for line in data.splitlines():
                            if stream_type == "stdout":
                                status_queue.put(f"[{name}] OUT: {line}")
                            elif stream_type == "stderr":
                                status_queue.put(f"[{name}] ERR: {line}")

        except Exception as e:
            status_queue.put(f"Node '{name}' encountered an error: {e}")
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.kill(process.pid, signal.SIGKILL)  # Kill only this specific process
                    status_queue.put(f"Node '{name}' subprocess forcefully killed.")
            status_queue.put(f"Node '{name}' process terminated.")

    def _monitor_processes(self):
        while True:
            time.sleep(5)
            with self.lock:
                for name, process in list(self.nodes.items()):
                    if not process.is_alive():
                        logger.warning(f"Node '{name}' with PID {process.pid} has stopped unexpectedly.")
                        self.nodes.pop(name)
                        self.status_queues.pop(name)