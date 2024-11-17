from fastapi import FastAPI
from ros_node_manager.routes import node_router

app = FastAPI(title="Node Management API", version="1.0.0")

app.include_router(node_router.router, prefix="/nodes", tags=["Node Management"])

@app.on_event("startup")
async def startup_event():
    print("Starting Application...")

@app.on_event("shutdown")
async def shutdown_event():
    print("Shutting down application...")