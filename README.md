# ROS2 Node Manager
This is a tool that enables ROS2 node management by exposing a few web APIs. 

### TODO
1. Fault recovery
2. Syncing preexising nodes 
3. API reference document

## Installation
This program relies leverages the poetry dependency manager and its use is encouraged for pain free installation and usage

### Depenancies
1. Python 3.12
2. [Poetry](https://python-poetry.org/docs/#installing-with-the-official-installer) 

### Usage 
In order for the ROS2 node manager to be able to manage nodes the ROS2 sdk must be sources on machine or container in which you are running this endpoint. 

```bash
poetry run uvicorn ros_node_manager.main:app --host=0.0.0.0
```
This command will start the router on your host machines IP 

Now you should be ready to manage your nodes!

A request to start a node could look like this!
```bash
curl +X POST "http://localhost:8000/nodes/launch" -d '{"name": "talker_node_example", "package":"demo_nodes_cpp", "executable" : "talker"}' -H "Content-Type: application/json"
```

### API Reference
Right now the following web APIs are exposed

`/nodes/launch`: POST requests to launch a node or nodes using launch files, or a plain executable. With parameters if desired

See example in the usage section.

`/nodes/terminate`:  POST requests with a single parameter for the name of the node, you can hit this endpoint by writing a simple query like this 

```bash
curl -X POST "http://localhost:8000/nodes/terminate?name=test_node"
```

Termination of nodes can only occur if they were started using this node manager. 

`/nodes/{name}/status`: GET request to get basic status on a running node process.
