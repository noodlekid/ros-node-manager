from pydantic import BaseModel


class NodeRequest(BaseModel):
    """Model storing information about running nodes"""

    name: str
    package: str
    executable: str | None = None
    launch_file: str | None = None
    parameters: dict[str, str] | None = None
