# fastapi_app.py
from __future__ import annotations

import os
import sys
import asyncio
import logging
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from fastapi import Query

# Import local configuration and components
from config import settings
from kube_client import KubeClient
from adapters import KubeDeployAdapters

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Inicializar cliente del orquestador
# Try to initialize Kubernetes client, but don't fail if it's not available
try:
    orchestrator_client = KubeClient(namespace="default", in_cluster=False)
    adapters = KubeDeployAdapters(orchestrator_client)
    logger.info("✅ Kubernetes client initialized")
except Exception as e:
    logger.warning(f"⚠️ Kubernetes client initialization failed: {e}. Some features may not work.")
    orchestrator_client = None
    adapters = None

# Load GitHub token from environment file
from dotenv import load_dotenv

load_dotenv('github_config.env')

# Get GitHub token from environment (set in github_config.env or environment variable)
github_token = os.getenv("GITHUB_TOKEN")

# Try to import token_manager if it exists
try:
    from token_manager import token_manager
    token_manager.start_auto_renewal()
    # Get token from token_manager if available (overrides env var)
    if hasattr(token_manager, 'get_token'):
        token_from_manager = token_manager.get_token()
        if token_from_manager:
            github_token = token_from_manager
except ImportError:
    pass  # token_manager doesn't exist, use environment variable

# Inicializar integración con GitHub Packages - use local github_integration.py
from github_integration import GitHubPackagesIntegration
github_integration = GitHubPackagesIntegration(github_token=github_token)
logger.info(f"GitHub integration initialized with token: {'configured' if github_token else 'NOT configured'}")

# -----------------------------------------------------------------------------
# FastAPI app + CORS
# -----------------------------------------------------------------------------
app = FastAPI(title="Kube Deployer Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# Static files
# -----------------------------------------------------------------------------
import os
frontend_path = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
app.mount("/static", StaticFiles(directory=frontend_path), name="static")
app.mount("/assets", StaticFiles(directory=os.path.join(frontend_path, "assets")), name="assets")

# Serve frontend at root
@app.get("/")
async def serve_frontend():
    """Serve the frontend HTML file."""
    return FileResponse(os.path.join(frontend_path, "src", "index.html"))

# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------
class NSBody(BaseModel):
    ns: str

class EnvKV(BaseModel):
    key: str
    value: str

class Plan(BaseModel):
    namespace: str = Field(..., description="Target namespace")
    app: str = Field(..., description="App template key from /api/apps")
    imageTag: str = Field(default="latest")
    replicas: int = Field(default=1, ge=1)
    node: Optional[str] = Field(default=None, description="Specific node name, or None/'Any node'")
    env: List[EnvKV] = Field(default_factory=list)
    
    class Config:
        # Permitir valores null para node
        populate_by_name = True

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _core() :
    from kubernetes import client
    return client.CoreV1Api()

def _apps() :
    from kubernetes import client
    return client.AppsV1Api()

def _load_kube():
    """Load Kubernetes configuration."""
    try:
        from kubernetes import config
        config.load_incluster_config()
    except:
        try:
            from kubernetes import config
            config.load_kube_config()
        except:
            pass

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/api/health")
async def api_health():
    return {"status": "healthy"}

# -----------------------------------------------------------------------------
# Deploy endpoints
# -----------------------------------------------------------------------------
@app.post("/api/deploy")
async def api_deploy(plan: Plan, request: Request):
    """Deploy application using orchestrator client."""
    deployment_id = f"{plan.app}-{int(datetime.now().timestamp())}"
    user_ip = request.client.host if request.client else "unknown"
    
    try:
        logger.info(f"🚀 Starting deployment: {plan.app} (ID: {deployment_id})")
        logger.info(f"📋 Deployment details: namespace={plan.namespace}, replicas={plan.replicas}, node={plan.node}")
        
        # Generate deployment manifest
        manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": f"{plan.app}-{int(datetime.now().timestamp())}", "namespace": plan.namespace},
        "spec": {
            "replicas": plan.replicas,
            "selector": {"matchLabels": {"app": plan.app}},
            "template": {
                "metadata": {"labels": {"app": plan.app}},
                "spec": {
                    "containers": [{
                        "name": plan.app,
                            "image": f"ghcr.io/agrarian-application-repository/{plan.app}:{plan.imageTag}",
                        "imagePullPolicy": "Always",
                        "ports": [{"containerPort": 8080}],
                    }]
                },
            },
        },
    }
        
        if plan.node and plan.node.lower() not in ("any node", "any", "all"):
            manifest["spec"]["template"]["spec"]["nodeSelector"] = {"kubernetes.io/hostname": plan.node}
            logger.info(f"🎯 Node selector set to: {plan.node}")
        
        # Deploy directly using Kubernetes client
        logger.info(f"📡 Deploying to Kubernetes...")
        
        try:
            from kubernetes import client, config
            try:
                config.load_incluster_config()
            except:
                config.load_kube_config()
            
            apps_v1 = client.AppsV1Api()
            
            # Create deployment object
            deployment = client.V1Deployment(
                api_version="apps/v1",
                kind="Deployment",
                metadata=client.V1ObjectMeta(
                    name=manifest["metadata"]["name"],
                    namespace=plan.namespace
                ),
                spec=client.V1DeploymentSpec(
                    replicas=plan.replicas,
                    selector=client.V1LabelSelector(
                        match_labels=manifest["spec"]["selector"]["matchLabels"]
                    ),
                    template=client.V1PodTemplateSpec(
                        metadata=client.V1ObjectMeta(
                            labels=manifest["spec"]["template"]["metadata"]["labels"]
                        ),
                        spec=client.V1PodSpec(
                            containers=[
                                client.V1Container(
                                    name=manifest["spec"]["template"]["spec"]["containers"][0]["name"],
                                    image=manifest["spec"]["template"]["spec"]["containers"][0]["image"],
                                    image_pull_policy=manifest["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"],
                                    ports=[client.V1ContainerPort(container_port=p["containerPort"]) 
                                           for p in manifest["spec"]["template"]["spec"]["containers"][0]["ports"]]
                                )
                            ]
                        )
                    )
                )
            )
            
            # Add node selector if specified
            if plan.node and plan.node.lower() not in ("any node", "any", "all"):
                deployment.spec.template.spec.node_selector = {"kubernetes.io/hostname": plan.node}
            
            # Create the deployment
            api_response = apps_v1.create_namespaced_deployment(
                body=deployment,
                namespace=plan.namespace
            )
            logger.info(f"✅ Deployment created: {api_response.metadata.name}")
            
        except Exception as deploy_error:
            logger.error(f"❌ Error deploying to Kubernetes: {deploy_error}")
            raise
        
        logger.info(f"✅ Deployment completed successfully: {deployment_id}")
        
        return {
            "success": True,
            "deployment_id": deployment_id,
            "status": "success",
            "message": f"Application {plan.app} deployed successfully to namespace {plan.namespace}",
            "manifest": manifest,
            "deployment_name": f"{plan.app}-deployment"
        }
        
    except Exception as e:
        logger.error(f"❌ Error deploying {plan.app}: {e}")
        raise HTTPException(500, f"Deploy failed: {e}")

@app.post("/api/generate-yaml")
async def generate_yaml(plan: Plan):
    """Generar YAML automáticamente basado en la configuración del plan."""
    try:
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": plan.app, "namespace": plan.namespace},
            "spec": {
                "replicas": plan.replicas,
                "selector": {"matchLabels": {"app": plan.app}},
                "template": {
                    "metadata": {"labels": {"app": plan.app}},
                    "spec": {
                        "containers": [{
                            "name": plan.app,
                            "image": f"ghcr.io/agrarian-application-repository/{plan.app}:{plan.imageTag}",
                            "imagePullPolicy": "Always",
                            "ports": [{"containerPort": 8080}],
                        }]
                    },
                },
            },
        }
        
        if plan.node and plan.node.lower() not in ("any node", "any", "all"):
            manifest["spec"]["template"]["spec"]["nodeSelector"] = {"kubernetes.io/hostname": plan.node}
        
        return {
            "yaml": manifest,
            "success": True
        }
    except Exception as e:
        logger.error(f"Error generating YAML: {e}")
        raise HTTPException(500, f"Failed to generate YAML: {e}")

# -----------------------------------------------------------------------------
# Static config for UI
# -----------------------------------------------------------------------------
@app.get("/api/apps")
def api_apps() -> List[str]:
    """Get list of available applications from GitHub Packages"""
    try:
        logger.info("API /api/apps called - starting package retrieval")
        # Get packages from GitHub
        # The integration now automatically discovers packages by scanning repositories,
        # which catches manually linked packages without needing hardcoded names
        packages = github_integration.get_available_packages()
        logger.info(f"get_available_packages() returned {len(packages)} packages")
        if packages:
            # Extract app names from package names
            # Package name is already just the app name (e.g., "plant-app"), not the full image path
            app_names = []
            for package in packages:
                app_name = package.name
                app_names.append(app_name)
            logger.info(f"Retrieved {len(app_names)} applications from GitHub Packages: {app_names}")
            return sorted(app_names)
        else:
            logger.warning("GitHub Packages returned empty list")
            return []
    except Exception as e:
        logger.error(f"Error fetching applications from GitHub: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch applications from GitHub: {str(e)}. Please check GitHub token configuration.")

def _read_config_from_repo(package_name: str, github_token: str, repository_info: Optional[Dict] = None) -> Dict[str, str]:
    """Read README.md file from the package's repository and extract testbed, usecase, and developer info"""
    import re
    
    default_config = {
        "testbed": "Unknown",
        "usecase": "Unknown",
        "developer": "Unknown"
    }
    
    try:
        org = github_integration.github_org
        
        # Try to get repository name from repository_info if available
        repo_name = None
        if repository_info:
            repo = repository_info.get("repository")
            if repo:
                repo_owner = repo.get("owner", {}).get("login", "")
                repo_name_from_pkg = repo.get("name", "")
                # If owner matches org, use the repo name
                if repo_owner.lower() == org.lower():
                    repo_name = repo_name_from_pkg
        
        # If no repo name from package, try package name as repo name
        if not repo_name:
            repo_name = package_name
        
        # Try to get the README.md file from the repository
        # GitHub org names are case-insensitive, but use lowercase for consistency
        org_lower = org.lower()
        readme_url = f"https://api.github.com/repos/{org_lower}/{repo_name}/contents/README.md"
        headers = {
            "Accept": "application/vnd.github.v3.raw",
            "Authorization": f"token {github_token}"
        }
        
        response = requests.get(readme_url, headers=headers, timeout=5)
        if response.status_code == 200:
            readme_content = response.text
            
            # Debug: log the relevant line for troubleshooting
            for line in readme_content.split('\n'):
                if 'testbed' in line.lower() or 'use case' in line.lower() or 'developed by' in line.lower():
                    logger.info(f"README line for {package_name}: {line[:200]}")
            
            # Parse README.md for the new format:
            # "The application is designed for testbed [_testbed number_] and for the [_your use case_] use case. It was developed by [_your organization's name_]."
            
            config = default_config.copy()
            
            # Extract testbed: "designed for testbed [3]" or "designed for testbed 3" - should return just the number
            # Pattern matches: "testbed [3]", "testbed 3", "testbed [1]" - extracts just the number
            testbed_pattern = r'designed\s+for\s+testbed\s+(?:\[([^\]]+)\]|(\d+))|testbed\s+(?:\[([^\]]+)\]|(\d+))'
            testbed_match = re.search(testbed_pattern, readme_content, re.IGNORECASE)
            if testbed_match:
                # Get the matched value (could be from any group)
                testbed_value = testbed_match.group(1) or testbed_match.group(2) or testbed_match.group(3) or testbed_match.group(4)
                if testbed_value:
                    # Remove underscores and brackets, extract number
                    testbed_clean = re.sub(r'[\[\]_]', '', testbed_value).strip()
                    # Extract number if present - return just the number, not "testbed-X"
                    testbed_num = re.search(r'(\d+)', testbed_clean)
                    if testbed_num:
                        config['testbed'] = testbed_num.group(1)  # Just the number, not "testbed-X"
            
            # Extract use case: "for the [IoT Monitoring] use case" or "for the example use case" - extract text between "the" and "use case"
            # Pattern matches: "for the [something] use case" or "for the something use case" - extracts the content
            usecase_pattern = r'for\s+the\s+(?:\[([^\]]+)\]|([^\]]+?))\s+use\s+case'
            usecase_match = re.search(usecase_pattern, readme_content, re.IGNORECASE)
            if usecase_match:
                usecase_value = usecase_match.group(1) or usecase_match.group(2)
                if usecase_value:
                    config['usecase'] = re.sub(r'[\[\]_]', '', usecase_value).strip()
            else:
                # Fallback: try to match "the [something] use case" or "the something use case" without "for"
                usecase_pattern_fallback = r'the\s+(?:\[([^\]]+)\]|([^\]]+?))\s+use\s+case'
                usecase_match_fallback = re.search(usecase_pattern_fallback, readme_content, re.IGNORECASE)
                if usecase_match_fallback:
                    usecase_value = usecase_match_fallback.group(1) or usecase_match_fallback.group(2)
                    if usecase_value:
                        config['usecase'] = re.sub(r'[\[\]_]', '', usecase_value).strip()
            
            # Extract developer: "It was developed by [Sateliot]." or "It was developed by Sateliot." - extract text after "developed by"
            # Pattern matches: "It was developed by [something]" or "It was developed by something" or "developed by [something]" or "developed by something"
            developer_pattern = r'(?:It\s+was\s+)?developed\s+by\s+(?:\[([^\]]+)\]|([^.\n]+))'
            developer_match = re.search(developer_pattern, readme_content, re.IGNORECASE)
            if developer_match:
                developer_value = developer_match.group(1) or developer_match.group(2)
                if developer_value:
                    config['developer'] = re.sub(r'[\[\]_]', '', developer_value).strip()
            
            logger.info(f"✅ Read README.md for {package_name} (repo: {repo_name}): testbed={config['testbed']}, usecase={config['usecase']}, developer={config['developer']}")
            return config
        else:
            logger.debug(f"README.md not found for {package_name} (repo: {repo_name}, status {response.status_code}), using defaults")
            return default_config
    except Exception as e:
        logger.debug(f"Error reading README.md for {package_name}: {e}, using defaults")
        return default_config

@app.get("/api/applications")
def api_applications() -> List[Dict[str, Any]]:
    """Get list of available applications with full details for frontend"""
    try:
        logger.info("API /api/applications called - starting package retrieval")
        # Get packages from GitHub - we need to get repository info too
        github_token = os.getenv("GITHUB_TOKEN")
        
        # Get packages with repository info using GraphQL
        all_packages_with_repos = []
        try:
            org = github_integration.github_org
            has_next_page = True
            cursor = None
            
            while has_next_page:
                if cursor:
                    graphql_query = """
                    {
                      organization(login: "%s") {
                        packages(first: 100, packageType: DOCKER, after: "%s") {
                          nodes {
                            name
                            repository {
                              name
                              owner {
                                login
                              }
                            }
                          }
                          pageInfo {
                            hasNextPage
                            endCursor
                          }
                        }
                      }
                    }
                    """ % (org, cursor)
                else:
                    graphql_query = """
                    {
                      organization(login: "%s") {
                        packages(first: 100, packageType: DOCKER) {
                          nodes {
                            name
                            repository {
                              name
                              owner {
                                login
                              }
                            }
                          }
                          pageInfo {
                            hasNextPage
                            endCursor
                          }
                        }
                      }
                    }
                    """ % org
                
                graphql_response = requests.post(
                    "https://api.github.com/graphql",
                    headers={
                        "Authorization": f"token {github_token}",
                        "Content-Type": "application/json"
                    },
                    json={"query": graphql_query},
                    timeout=30
                )
                
                if graphql_response.status_code == 200:
                    data = graphql_response.json()
                    if "errors" in data:
                        break
                    
                    if "data" in data and "organization" in data["data"]:
                        packages_data = data["data"]["organization"].get("packages", {})
                        graphql_packages = packages_data.get("nodes", [])
                        page_info = packages_data.get("pageInfo", {})
                        
                        for pkg in graphql_packages:
                            all_packages_with_repos.append({
                                "name": pkg.get("name"),
                                "repository": pkg.get("repository")
                            })
                        
                        has_next_page = page_info.get("hasNextPage", False)
                        cursor = page_info.get("endCursor")
                    else:
                        break
                else:
                    break
        except Exception as e:
            logger.warning(f"Could not get repository info from GraphQL: {e}")
        
        # Create a map of package name to repository info
        package_repo_map = {pkg["name"]: pkg.get("repository") for pkg in all_packages_with_repos}
        
        # Get packages from GitHub
        # The integration now automatically discovers packages by scanning repositories,
        # which catches manually linked packages without needing hardcoded names
        packages = github_integration.get_available_packages()
        logger.info(f"get_available_packages() returned {len(packages)} packages")
        if packages:
            # Convert to frontend format with full details
            applications = []
            
            for package in packages:
                # Create a better description from the name
                description = package.description
                if not description or description == f"{package.name} application":
                    # Generate a better description from the name
                    name_parts = package.name.replace('-app', '').replace('-', ' ').split()
                    description = ' '.join(word.capitalize() for word in name_parts) + " Application"
                
                # Get repository info for this package
                # First check the map from GraphQL, but also try direct lookup for manually linked packages
                repository_info = {"repository": package_repo_map.get(package.name)}
                
                # If no repository info from GraphQL, try to get it directly from the package
                if not repository_info.get("repository") and github_token:
                    try:
                        # Try to get package details directly - this works for manually linked packages
                        pkg_url = f"https://api.github.com/orgs/{github_integration.github_org}/packages/container/{package.name}"
                        pkg_response = requests.get(
                            pkg_url,
                            headers={"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"},
                            timeout=5
                        )
                        if pkg_response.status_code == 200:
                            pkg_data = pkg_response.json()
                            repo_data = pkg_data.get("repository")
                            if repo_data:
                                repository_info = {"repository": repo_data}
                                logger.info(f"✅ Found repository for manually linked package {package.name}: {repo_data.get('name')}")
                    except Exception as e:
                        logger.debug(f"Could not get repository info for {package.name}: {e}")
                
                # Read README.md from repository to extract testbed, usecase, and developer info
                config = _read_config_from_repo(package.name, github_token, repository_info) if github_token else {
                    "testbed": "Unknown",
                    "usecase": "Unknown",
                    "developer": "Unknown"
                }
                
                applications.append({
                    "name": package.name,
                    "description": description,
                    "image": package.image,
                    "registry": package.registry,
                    "version": package.version,
                    "created_at": package.created_at,
                    "size": package.size,
                    "testbed": config["testbed"],
                    "usecase": config["usecase"],
                    "developer": config["developer"]
                })
            logger.info(f"Retrieved {len(applications)} applications from GitHub Packages")
            return applications
        else:
            logger.warning("GitHub Packages returned empty list")
            return []
    except Exception as e:
        logger.error(f"Error fetching applications from GitHub: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch applications from GitHub: {str(e)}. Please check GitHub token configuration.")

@app.get("/api/namespaces")
def api_namespaces() -> List[str]:
    """Get list of available namespaces"""
    return ["default", "kube-system", "kube-public", "kube-node-lease"]

@app.get("/api/nodes")
async def api_nodes() -> List[str]:
    """Obtener lista de nodos disponibles desde Kubernetes."""
    try:
        # Use Kubernetes client directly to get nodes
        from kubernetes import client, config
        try:
            config.load_incluster_config()
        except:
            config.load_kube_config()
        
        v1 = client.CoreV1Api()
        nodes = v1.list_node()
        
        node_names = []
        for node in nodes.items:
            node_names.append(node.metadata.name)
        
        logger.info(f"✅ Obtenidos {len(node_names)} nodos desde Kubernetes: {node_names}")
        return node_names
        
    except Exception as e:
        logger.error(f"❌ Error obteniendo nodos desde Kubernetes: {e}")
        # Fallback a nodos estáticos si Kubernetes no responde
        logger.warning("⚠️ Usando nodos de fallback")
        return [
            "agrarian-cloud-node",
            "agrarian-edge-node-1", 
            "agrarian-edge-node-2",
            "agrarian-satellite-node",
            "agrarian-iot-node"
        ]

# -----------------------------------------------------------------------------
# History & Logs
# -----------------------------------------------------------------------------
@app.get("/api/history/deployments")
async def get_deployment_history():
    """Obtener historial de deployments desde Kubernetes."""
    try:
        import subprocess
        import json
        import os
        from datetime import datetime, timezone
        
        # Configure environment for kubectl
        env = os.environ.copy()
        env["PATH"] = f"{os.environ.get('HOME')}/.local/bin:{os.environ.get('PATH')}"
        
        # Get actual deployments from Kubernetes
        result = subprocess.run(
            ["kubectl", "get", "deployments", "--all-namespaces", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env
        )
        
        if result.returncode != 0:
            logger.error(f"❌ Failed to get deployments: {result.stderr}")
            return []
        
        data = json.loads(result.stdout)
        deployments = data.get("items", [])
        
        # Format deployments for frontend
        formatted_history = []
        for deployment in deployments:
            metadata = deployment.get("metadata", {})
            spec = deployment.get("spec", {})
            status = deployment.get("status", {})
            
            # Get creation timestamp
            creation_timestamp = metadata.get("creationTimestamp", "")
            
            # Calculate age
            age = "N/A"
            if creation_timestamp:
                try:
                    # Parse ISO timestamp
                    created_time = datetime.fromisoformat(creation_timestamp.replace('Z', '+00:00'))
                    now = datetime.now(timezone.utc)
                    diff = now - created_time
                    
                    # Format age
                    if diff.days > 0:
                        age = f"{diff.days}d"
                    elif diff.seconds > 3600:
                        age = f"{diff.seconds // 3600}h"
                    elif diff.seconds > 60:
                        age = f"{diff.seconds // 60}m"
                    else:
                        age = f"{diff.seconds}s"
                except Exception as e:
                    logger.warning(f"Could not parse timestamp {creation_timestamp}: {e}")
                    age = "N/A"
            
            # Get container image
            containers = spec.get("template", {}).get("spec", {}).get("containers", [])
            image = containers[0].get("image", "undefined") if containers else "undefined"
            
            # Get node selector
            node_selector = spec.get("template", {}).get("spec", {}).get("nodeSelector", {})
            node = node_selector.get("kubernetes.io/hostname", "any") if node_selector else "any"
            
            formatted_history.append({
                'timestamp': creation_timestamp,
                'reason': 'Deployment',
                'message': f"Deployment {metadata.get('name', '')} in namespace {metadata.get('namespace', '')}",
                'namespace': metadata.get("namespace", ""),
                'name': metadata.get("name", ""),
                'deployment_id': metadata.get("name", ""),
                'status': "SUCCESS" if status.get("readyReplicas", 0) > 0 else "PENDING",
                'replicas': spec.get("replicas", 0),
                'image_tag': image,
                'node': node,
                'user_ip': "system",
                'age': age,
                'created': age  # For the "Created" column
            })
        
        # Sort by creation time (newest first)
        formatted_history.sort(key=lambda x: x['timestamp'], reverse=True)
        
        logger.info(f"📋 Retrieved {len(formatted_history)} deployment records from Kubernetes")
        return formatted_history
        
    except Exception as e:
        logger.error(f"Error obteniendo historial de deployments: {e}")
        return []

@app.get("/api/logs/{namespace}/{pod_name}")
async def get_pod_logs(namespace: str, pod_name: str, lines: int = 100):
    """Obtener logs de un pod específico."""
    return {"logs": f"Logs for pod {pod_name} in namespace {namespace} (mock data)"}

# -----------------------------------------------------------------------------
# Monitoring & Metrics
# -----------------------------------------------------------------------------
@app.get("/api/metrics/deployments")
async def get_deployment_metrics():
    """Obtener métricas de deployments."""
    try:
        # Usar estadísticas del sistema de logging
        # Deployment stats not available in this service
        stats = {
            "total_deployments": 0,
            "successful_deployments": 0,
            "failed_deployments": 0
        }
        
        # Formatear métricas para el frontend
        metrics = {
            "total_deployments": stats.get("total_deployments", 0),
            "successful_deployments": stats.get("successful_deployments", 0),
            "failed_deployments": stats.get("failed_deployments", 0),
            "apps_deployed": stats.get("apps_deployed", []),
            "recent_deployments": stats.get("recent_deployments", [])
        }
        
        logger.info(f"📊 Retrieved deployment metrics: {metrics['total_deployments']} total, {metrics['successful_deployments']} successful")
        return metrics
        
    except Exception as e:
        logger.error(f"Error obteniendo métricas de deployments: {e}")
        return {
            "total_deployments": 0,
            "successful_deployments": 0,
            "failed_deployments": 0,
            "apps_deployed": [],
            "recent_deployments": []
        }

@app.get("/api/metrics/pods")
async def get_pod_metrics():
    """Obtener métricas de pods."""
    try:
        # Obtener métricas generales del cluster
        metrics = await orchestrator_client.get_metrics("pod", "5m")
        return metrics
    except Exception as e:
        logger.error(f"Error obteniendo métricas de pods: {e}")
        return []

# -----------------------------------------------------------------------------
# Template Management
# -----------------------------------------------------------------------------
@app.get("/api/templates/available")
async def get_available_templates():
    """Obtener templates disponibles desde GitHub Packages."""
    try:
        # Get packages from GitHub
        packages = github_integration.get_available_packages()
        if packages:
            templates = []
            for package in packages:
                # Extract app name from package name
                app_name = package.name.replace('ghcr.io/agrarian-application-repository/', '').replace(':latest', '')
                templates.append({
                    "name": app_name,
                    "description": package.description or f"{app_name.replace('-', ' ').title()} Application",
                    "image": package.image,
                    "ports": package.ports
                })
            logger.info(f"Retrieved {len(templates)} templates from GitHub Packages")
            return {"templates": templates}
        else:
            # Fallback to mock data
            logger.warning("GitHub Packages not available, using fallback templates")
            return {
                "templates": [
                    {
                        "name": "temperature-collector-app",
                        "description": "Temperature Data Collector Application",
                        "image": "ghcr.io/agrarian-application-repository/temperature-collector-app:latest",
                        "ports": [8080]
                    },
                    {
                        "name": "cloud-node-app", 
                        "description": "Agrarian Cloud Node Application",
                        "image": "ghcr.io/agrarian-application-repository/cloud-node-app:latest",
                        "ports": [8080]
                    },
                    {
                        "name": "iot-device-app",
                        "description": "IoT Device Management Application", 
                        "image": "ghcr.io/agrarian-application-repository/iot-device-app:latest",
                        "ports": [8080]
                    },
                    {
                        "name": "satellite-node-app",
                        "description": "Satellite Node Application",
                        "image": "ghcr.io/agrarian-application-repository/satellite-node-app:latest",
                        "ports": [8080]
                    },
                    {
                        "name": "test-agrarian-app",
                        "description": "Test Agrarian Application",
                        "image": "ghcr.io/agrarian-application-repository/test-agrarian-app:latest",
                        "ports": [8080]
                    }
                ]
            }
    except Exception as e:
        logger.error(f"Error fetching templates from GitHub: {e}")
        # Fallback to mock data
        return {
            "templates": [
                {
                    "name": "temperature-collector-app",
                    "description": "Temperature Data Collector Application",
                    "image": "ghcr.io/agrarian-application-repository/temperature-collector-app:latest",
                    "ports": [8080]
                },
                {
                    "name": "cloud-node-app", 
                    "description": "Agrarian Cloud Node Application",
                    "image": "ghcr.io/agrarian-application-repository/cloud-node-app:latest",
                    "ports": [8080]
                },
                {
                    "name": "iot-device-app",
                    "description": "IoT Device Management Application", 
                    "image": "ghcr.io/agrarian-application-repository/iot-device-app:latest",
                    "ports": [8080]
                },
                {
                    "name": "satellite-node-app",
                    "description": "Satellite Node Application",
                    "image": "ghcr.io/agrarian-application-repository/satellite-node-app:latest",
                    "ports": [8080]
                },
                {
                    "name": "test-agrarian-app",
                    "description": "Test Agrarian Application",
                    "image": "ghcr.io/agrarian-application-repository/test-agrarian-app:latest",
                    "ports": [8080]
                }
            ]
        }

@app.get("/api/templates")
async def get_templates():
    """Obtener templates disponibles."""
    return await get_available_templates()

@app.post("/api/templates/refresh")
async def refresh_templates():
    """Forzar actualización de templates desde GitHub Packages."""
    try:
        # Get fresh templates from GitHub
        templates = await get_available_templates()
        
        return {
            "success": True,
            "message": "Templates actualizados correctamente",
            "count": len(templates.get("templates", [])),
            "templates": templates
        }
    except Exception as e:
        logger.error(f"Error actualizando templates: {e}")
        return {
            "success": False,
            "message": f"Error actualizando templates: {e}",
            "templates": []
        }

# -----------------------------------------------------------------------------
# Enhanced Deployment Management
# -----------------------------------------------------------------------------
@app.get("/api/deployments/detailed")
async def get_detailed_deployments():
    """Obtener deployments con información detallada desde Kubernetes."""
    try:
        import subprocess
        import json
        import os
        from datetime import datetime, timezone
        
        # Configure environment for kubectl
        env = os.environ.copy()
        env["PATH"] = f"{os.environ.get('HOME')}/.local/bin:{os.environ.get('PATH')}"
        
        # Get actual deployments from Kubernetes
        result = subprocess.run(
            ["kubectl", "get", "deployments", "--all-namespaces", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env
        )
        
        if result.returncode != 0:
            logger.error(f"❌ Failed to get deployments: {result.stderr}")
            return []
        
        data = json.loads(result.stdout)
        deployments = data.get("items", [])
        
        # Format deployments for frontend
        detailed_deployments = []
        for deployment in deployments:
            metadata = deployment.get("metadata", {})
            spec = deployment.get("spec", {})
            status = deployment.get("status", {})
            
            # Get container image
            containers = spec.get("template", {}).get("spec", {}).get("containers", [])
            image = containers[0].get("image", "undefined") if containers else "undefined"
            
            # Get replica counts
            replicas = spec.get("replicas", 0)
            ready_replicas = status.get("readyReplicas", 0)
            available_replicas = status.get("availableReplicas", 0)
            
            # Calculate status
            if ready_replicas == replicas and replicas > 0:
                status_text = "Ready"
            elif ready_replicas > 0:
                status_text = "Partial"
            else:
                status_text = "Pending"
            
            # Get creation timestamp
            creation_timestamp = metadata.get("creationTimestamp", "")
            
            detailed_deployments.append({
                "name": metadata.get("name", "unknown"),
                "namespace": metadata.get("namespace", "default"),
                "replicas": replicas,
                "ready": f"{ready_replicas}/{replicas}",
                "available": available_replicas,
                "updated": creation_timestamp,
                "status": status_text,
                "deployment_id": metadata.get("name", ""),
                "image_tag": image,
                "node": "any",  # We can enhance this later
                "created": creation_timestamp,
                "labels": metadata.get("labels", {}),
                "image": image  # For the Image column
            })
        
        # Sort by creation time (newest first)
        detailed_deployments.sort(key=lambda x: x['created'], reverse=True)
        
        logger.info(f"📋 Retrieved {len(detailed_deployments)} detailed deployment records from Kubernetes")
        return detailed_deployments
        
    except Exception as e:
        logger.error(f"Error obteniendo deployments: {e}")
        return []

@app.get("/api/deployment/{namespace}/{name}/details")
async def get_deployment_details(namespace: str, name: str):
    """Obtener detalles completos de un deployment específico."""
    return {
                "name": name,
        "namespace": namespace,
        "status": "Not Found",
        "message": "Deployment not found (mock data)"
    }

@app.post("/api/deployment/{namespace}/{name}/scale")
async def scale_deployment(namespace: str, name: str, request: Request):
    """Escalar un deployment en Kubernetes."""
    try:
        import subprocess
        import os
        import json
        
        # Get replicas from request body
        body = await request.json()
        replicas = body.get("replicas")
        
        if replicas is None:
            return {
                "success": False,
                "message": "Replicas parameter is required",
                "error": "Missing replicas in request body"
            }
        
        # Configure environment for kubectl
        env = os.environ.copy()
        env["PATH"] = f"{os.environ.get('HOME')}/.local/bin:{os.environ.get('PATH')}"
        
        # Scale the deployment using kubectl
        result = subprocess.run(
            ["kubectl", "scale", "deployment", name, f"--replicas={replicas}", "-n", namespace],
            capture_output=True,
            text=True,
            timeout=30,
            env=env
        )
        
        if result.returncode == 0:
            logger.info(f"✅ Successfully scaled deployment {name} to {replicas} replicas in namespace {namespace}")
            return {
                "success": True,
                "message": f"Deployment {name} scaled to {replicas} replicas successfully",
                "kubectl_output": result.stdout
            }
        else:
            logger.error(f"❌ Failed to scale deployment {name}: {result.stderr}")
            return {
                "success": False,
                "message": f"Failed to scale deployment {name}: {result.stderr}",
                "error": result.stderr
            }
            
    except Exception as e:
        logger.error(f"❌ Error scaling deployment {name}: {e}")
        return {
            "success": False,
            "message": f"Error scaling deployment {name}: {str(e)}",
            "error": str(e)
        }

@app.delete("/api/deployment/{namespace}/{name}")
async def delete_deployment(namespace: str, name: str):
    """Eliminar un deployment desde Kubernetes."""
    try:
        import subprocess
        import os
        
        # Configure environment for kubectl
        env = os.environ.copy()
        env["PATH"] = f"{os.environ.get('HOME')}/.local/bin:{os.environ.get('PATH')}"
        
        # Delete the deployment using kubectl
        result = subprocess.run(
            ["kubectl", "delete", "deployment", name, "-n", namespace],
            capture_output=True,
            text=True,
            timeout=30,
            env=env
        )
        
        if result.returncode == 0:
            logger.info(f"✅ Successfully deleted deployment {name} from namespace {namespace}")
            return {
                "success": True,
                "message": f"Deployment {name} deleted successfully from namespace {namespace}",
                "kubectl_output": result.stdout
            }
        else:
            logger.error(f"❌ Failed to delete deployment {name}: {result.stderr}")
            return {
                "success": False,
                "message": f"Failed to delete deployment {name}: {result.stderr}",
                "error": result.stderr
            }
            
    except Exception as e:
        logger.error(f"❌ Error deleting deployment {name}: {e}")
        return {
            "success": False,
            "message": f"Error deleting deployment {name}: {str(e)}",
            "error": str(e)
        }

# -----------------------------------------------------------------------------
# Pod Management
# -----------------------------------------------------------------------------
@app.get("/api/pods")
def api_pods(ns: str = Query("default")):
    return _pods(ns)

def _pods(ns: str) -> List[Dict[str, Any]]:
    """Obtener pods de un namespace."""
    try:
        from kubernetes import client, config
        try:
            config.load_incluster_config()
        except:
            config.load_kube_config()
        
        v1 = client.CoreV1Api()
        pods_list = v1.list_namespaced_pod(namespace=ns)
        
        pods_data = []
        for pod in pods_list.items:
            # Get restart count
            restarts = sum(container.restart_count for container in (pod.status.container_statuses or []))
            
            # Get pod age/created
            created = "-"
            if pod.metadata.creation_timestamp:
                try:
                    # Keep UTC timezone info by adding 'Z' suffix
                    created = pod.metadata.creation_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
                except:
                    created = str(pod.metadata.creation_timestamp)
            
            pods_data.append({
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "status": pod.status.phase or "Unknown",
                "node": pod.spec.node_name or "-",
                "restarts": restarts,
                "created": created,
                "age": created
            })
        
        logger.info(f"✅ Retrieved {len(pods_data)} pods from namespace {ns}")
        return pods_data
    except Exception as e:
        logger.error(f"Error getting pods: {e}")
        return []

@app.post("/api/pods")
def api_pods_post(body: NSBody):
    return _pods(body.ns)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)