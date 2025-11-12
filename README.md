# agrarian-deployment-dashboard

A web-based dashboard for deploying and managing applications on Kubernetes clusters. This service provides a user-friendly interface for discovering applications from container registries and deploying them to your cluster.

## Features

- **Application Discovery**: Automatically discovers applications from GitHub Packages (GHCR)
- **Kubernetes Deployment**: Deploy applications to Kubernetes clusters with custom configurations
- **YAML Generation**: Automatic Kubernetes manifest generation
- **Repository Integration**: Reads application metadata from GitHub repositories
- **Node Selection**: Deploy applications to specific cluster nodes
- **Replica Management**: Configure application replicas and scaling

## Quick Start

1. **Install dependencies**:
   ```bash
   pip install fastapi uvicorn kubernetes requests
   ```

2. **Configure environment variables**:
   ```bash
   export GITHUB_TOKEN=your_github_token
   export K8S_NAMESPACE=default
   export K8S_IN_CLUSTER=false  # Set to true if running inside cluster
   ```

3. **Start the backend**:
   ```bash
   cd backend
   python3 -m uvicorn fastapi_app:app --host 0.0.0.0 --port 8002
   ```

4. **Start the frontend** (in another terminal):
   ```bash
   cd frontend
   python3 -m http.server 8025
   ```

5. **Access the dashboard**: http://localhost:8025

## Architecture

- **Backend** (Port 8002): FastAPI service handling deployment logic
- **Frontend** (Port 8025): Static HTML/CSS/JavaScript interface

## API Endpoints

- `/api/health` - Health check
- `/api/applications` - List available applications
- `/api/apps` - Application management endpoints
- `/api/deploy` - Deploy applications
- `/api/nodes` - Get cluster nodes

## Configuration

The dashboard requires:
- GitHub token for accessing GitHub Packages
- Kubernetes cluster access (kubeconfig or in-cluster config)
- Container registry access (GitHub Packages/GHCR)

## License

Part of the Agrarian Ecosystem project.


