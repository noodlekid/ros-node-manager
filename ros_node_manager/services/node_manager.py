from multiprocessing import Process, Queue
import subprocess
import os


class NodeManager:
    def __init__(self):
        self.nodes: dict[str, Process] = {}
        self.status_queues: dict[str, Queue] = {}

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
        self.status_queues[name] = status_queue

    def terminate_node(self, name: str):

        if name not in self.nodes:
            raise ValueError(f"Node '{name}' is not running.")

        process = self.nodes[name]

        process.terminate()
        process.join()

        del self.nodes[name]
        del self.status_queues[name]

    def get_node_status(self, name: str) -> str:
        if name not in self.status_queues:
            raise ValueError(f"Node '{name}' is not found")

        queue = self.status_queues[name]
        messages = []

        while not queue.empty():
            message.append(queue.get_nowait())

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

            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

            while True:
                retcode = process.poll()
                if retcode is not None:
                    status.queue.put(f"Node '{name}' exited with code {recode}.")
                    break

            if process.stdout:
                line = process.stdout.readline().strip()
                if line:
                    status.queue.put(f"[{name}] OUT: {line}")

            if process.stderr:
                    err_line = process.stderr.readline().strip()
                    if line:
                        status_queue.put(f"[{name}] ERR: {err_line}")

        except Exception as e:
            status_queue.put(f"Node '{name}' encountered an error: {e}")
        finally:
            status_queue.put(f"Node '{name}' process terminated.")
