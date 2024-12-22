# ROS2 Node Manager

A utility for managing ROS2 node processes remotely through a web API using FastAPI.

## Features

- Launch and terminate ROS2 nodes
- Query the status of running nodes
- List all managed nodes

## TODO
API reference document

## Installation

This program leverages the Poetry dependency manager, and its use is encouraged for a pain-free installation and usage.

### Dependencies

1. Python 3.12
2. [Poetry](https://python-poetry.org/docs/#installing-with-the-official-installer)
3. ROS2 Humble (has only been tested with humble)

### Usage

In order for the ROS2 Node Manager to be able to manage nodes, the ROS2 SDK must be sourced on the machine or container in which you are running this endpoint.

```bash
poetry run uvicorn ros_node_manager.main:app --host=0.0.0.0
```

This command will start the router on your host machine's IP.

Now you should be ready to manage your nodes!

A request to start a node could look like this:
```bash
curl -X POST "http://localhost:8000/nodes/launch" -d '{"name": "talker_node_example", "package": "demo_nodes_cpp", "executable": "talker"}' -H "Content-Type: application/json"
```

# Node Manager API Reference

This sectoin provides an overview of the available API endpoints for managing ROS nodes using the Node Manager. These endpoints allow you to launch, terminate, and monitor nodes effectively. The API closely mimics the functionality of the ROS 2 CLI tools, making it easy for users familiar with ROS 2 commands to transition to this API.

## Base URL

All endpoints are available under the base URL: `/nodes`

## Endpoints

### 1. Launch Node

- **URL**: `/nodes/launch`
- **Method**: `POST`
- **Description**: Launches a new ROS node.
- **Similar to**: `ros2 run <package> <executable>` or `ros2 launch <package> <launch_file>`
- **Request Body** (JSON):
  ```json
  {
    "name": "string",                // Unique name of the node
    "package": "string",             // ROS package containing the node
    "executable": "string",          // Executable file to run (optional)
    "launch_file": "string",         // Launch file to use (optional)
    "parameters": {                   // Key-value pairs for parameters (optional)
      "param1": "value1",
      "param2": "value2"
    }
  }
  ```
- **Responses**:
  - `200 OK`: Node launched successfully.
  - `400 Bad Request`: Validation failed (e.g., node name already exists).
  - `500 Internal Server Error`: Failed to launch the node.

**Notes**:
- This endpoint allows you to launch a node just like the ROS 2 CLI commands (`ros2 run` or `ros2 launch`). You can choose between providing an executable or a launch file.

### 2. Terminate Node

- **URL**: `/nodes/terminate`
- **Method**: `POST`
- **Description**: Terminates a running ROS node.
- **Similar to**: Using `ros2 lifecycle` to bring nodes down or stopping individual processes manually.
- **Query Parameters**:
  - **`name`**: The name of the node to terminate (required).
- **Responses**:
  - `200 OK`: Node terminated successfully.
  - `400 Bad Request`: Node not found or already stopped.
  - `500 Internal Server Error`: Failed to terminate the node.

**Notes**:
- This endpoint mimics terminating nodes in ROS 2, allowing you to target nodes by their unique name and bring them down as needed.

### 3. Get Node Status

- **URL**: `/nodes/{name}/status`
- **Method**: `GET`
- **Description**: Retrieves the status messages from a specific node.
- **Path Parameters**:
  - **`{name}`**: The name of the node whose status is being requested (required).
- **Similar to**: `ros2 node info <node_name>` for getting information about a running node.
- **Responses**:
  - `200 OK`: Returns the current status of the node.
  - `404 Not Found`: Node not found.

**Response Example**:
  ```json
  {
    "name": "example_node",
    "status": [
      "[example_node] OUT: Node started successfully",
      "[example_node] ERR: Warning: connection lost"
    ]
  }
  ```

**Notes**:
- This endpoint can be used to monitor the state of individual nodes, similar to how `ros2 node info` provides insights into a node’s activities.

### 4. List All Nodes

- **URL**: `/nodes`
- **Method**: `GET`
- **Description**: Lists all nodes currently managed by the Node Manager.
- **Similar to**: `ros2 node list` to see all active nodes.
- **Responses**:
  - `200 OK`: Returns a list of all node names.

**Response Example**:
  ```json
  {
    "nodes": ["node1", "node2", "node3"]
  }
  ```

**Notes**:
- This endpoint provides functionality similar to `ros2 node list`, allowing you to get a quick overview of all currently running nodes.

## Error Handling

- **400 Bad Request**: Indicates that the request was invalid (e.g., node name already exists or incorrect parameters).
- **404 Not Found**: The requested node was not found.
- **500 Internal Server Error**: An unexpected error occurred while processing the request.

## Example Usage

You can test the API using tools like Postman or `curl`. For example, to launch a node:

```bash
curl -X POST "http://localhost:8000/nodes/launch" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "example_node",
    "package": "example_package",
    "executable": "example_executable"
  }'
```

