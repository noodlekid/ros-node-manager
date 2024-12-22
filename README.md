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

### API Reference
#### /nodes/launch
POST requests to launch a node or nodes using launch files, or a plain executable, with parameters if desired.

See the example in the usage section.

#### /nodes/terminate
POST requests with a single parameter for the name of the node. You can hit this endpoint by writing a simple query like this:

```bash
curl -X POST "http://localhost:8000/nodes/terminate?name=test_node"
```
Termination of nodes can only occur if they were started using this node manager.

#### /nodes/{name}/status
GET request to get basic status on a running node process.

#### /nodes
GET request to list all managed nodes.
