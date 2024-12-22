from fastapi import FastAPI
from ros_node_manager.routes import node_router
from ros_node_manager.services import NodeManager


app = FastAPI(title="Node Management API", version="1.0.0")


@app.on_event("startup")
async def startup_event():
    print("Starting Application...")
    app.state.node_manager = NodeManager()


@app.on_event("shutdown")
async def shutdown_event():
    print("Shutting down application...")


app.include_router(node_router.router, prefix="/nodes", tags=["Node Management"])
