from dataclasses import dataclass
from typing import Optional
from queue import Queue

import subprocess
import psutil


@dataclass
class NodeEvent:
    type_: str
    message: str
    stream: Optional[str] = None


@dataclass
class NodeInfo:
    name: str
    process: subprocess.Popen[str]
    child_processes: list[psutil.Process]
    events_queue: Queue[NodeEvent]
    is_launch_file: bool
    state: str
