from multiprocessing import Process, Queue
import os
import subprocess

import time

import logging
import threading

from ros_node_manager.utils import get_ros_env

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

class NodeManager:
    def __init__(self):
        self.nodes: dict[str, Process] = {}
        self.status_queues: dict[str, Queue] = {}
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

        if name not in self.nodes:
            raise ValueError(f"Node '{name}' is not running.")

        process = self.nodes[name]

        process.terminate()
        process.join(timeout=5)

        logger.info(f"Node '{name}' with PID {process.pid} terminated.")
        del self.nodes[name]
        del self.status_queues[name]

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
        status_queue: Queue
    ) -> None:

        command = (
            ["ros2", "run", package, executable]
            if executable
            else ["ros2", "launch", package, launch_file]
        )

        if parameters:
            for key, value in parameters:
                command.extend(["--ros-args", "-p", f"{key}:={value}"])
        try:
            status_queue.put(f"Starting node: {name}")

            ros_env = get_ros_env("humble")
            process = subprocess.Popen(
                command, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True, 
                env={**os.environ, **ros_env}
            )

            while process.poll() is None:
                if process.stdout:
                    line = process.stdout.readline().strip()
                    if line:
                        status_queue.put(f"[{name}] OUT: {line}")

                if process.stderr:
                    err_line = process.stderr.readline().strip()
                    if err_line:
                        status_queue.put(f"[{name}] ERR: {err_line}")

                retcode = process.returncode
                status_queue.put(f"Node '{name}' exited with code {retcode}.")

        except Exception as e:
            status_queue.put(f"Node '{name}' encountered an error: {e}")
        finally:
            status_queue.put(f"Node '{name}' process terminated.")

    def _monitor_processes(self):
        while True:
            time.sleep(5)
            for name, process in list(self.nodes.items()):
                if not process.is_alive():
                    logger.warning(f"Node '{name}' with PID {process.pid} has stopped unexpectedly.")
                    self.nodes.pop(name)