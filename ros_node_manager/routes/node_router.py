from fastapi import APIRouter, HTTPException, Depends, Request
from ros_node_manager.models import NodeRequest
from ros_node_manager.services import NodeManager

router = APIRouter()


def get_node_manager(request: Request) -> NodeManager:
    return request.app.state.node_manager


@router.post("/launch")
async def launch_node(
    request: NodeRequest, node_manager: NodeManager = Depends(get_node_manager)
):
    try:
        node_manager.launch_node(
            name=request.name,
            package=request.package,
            executable=request.executable,
            launch_file=request.launch_file,
            parameters=request.parameters,
        )
        return {"message": f"Node '{request.name}' lauched successfully."}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to launch node: {e}")


@router.post("/terminate")
async def terminate_node(
    name: str, node_manager: NodeManager = Depends(get_node_manager)
):
    try:
        node_manager.terminate_node(name=name)
        return {"message": f"Node '{name}' terminated successfully."}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to launch node: {e}")


@router.get("/{name}/status")
async def get_node_status(
    name: str, node_manager: NodeManager = Depends(get_node_manager)
):
    try:
        status = node_manager.get_node_status(name=name)
        return {"name": name, "status": status}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("")
async def list_nodes(node_manager: NodeManager = Depends(get_node_manager)):
    return {"nodes": list(node_manager.nodes.keys())}
