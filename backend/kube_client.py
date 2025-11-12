"""
Kubernetes client for deployment operations.
"""
import logging
from typing import List, Optional, Dict, Any
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kube_types import Pod, Deployment, RolloutStatus

logger = logging.getLogger(__name__)


class KubeClient:
    """Kubernetes client for orchestrator operations."""
    
    def __init__(self, namespace: str, in_cluster: bool = True, context: str | None = None):
        """
        Initialize Kubernetes client.
        
        Args:
            namespace: Target Kubernetes namespace
            in_cluster: Whether running inside cluster (default: True)
            context: Kubernetes context name (optional)
        """
        self.namespace = namespace
        self.in_cluster = in_cluster
        
        try:
            if in_cluster:
                config.load_incluster_config()
            else:
                if context:
                    config.load_kube_config(context=context)
                else:
                    config.load_kube_config()
            
            self.v1 = client.CoreV1Api()
            self.apps_v1 = client.AppsV1Api()
            logger.info(f"✅ Kubernetes client initialized for namespace: {namespace}")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Kubernetes client: {e}")
            raise
    
    def get_pods(self, label_selector: str | None = None) -> List[Pod]:
        """
        Get pods in the namespace.
        
        Args:
            label_selector: Optional label selector for filtering
            
        Returns:
            List of Pod objects
        """
        try:
            pods = self.v1.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=label_selector
            )
            
            pod_list = []
            for pod in pods.items:
                pod_list.append(Pod(
                    name=pod.metadata.name,
                    namespace=pod.metadata.namespace,
                    status=pod.status.phase,
                    labels=pod.metadata.labels or {},
                    creation_timestamp=pod.metadata.creation_timestamp
                ))
            
            logger.info(f"Retrieved {len(pod_list)} pods from namespace {self.namespace}")
            return pod_list
            
        except ApiException as e:
            logger.error(f"Failed to get pods: {e}")
            raise
    
    def rollout_status(self, deployment: str, timeout_s: int = 300) -> RolloutStatus:
        """
        Check deployment rollout status.
        
        Args:
            deployment: Deployment name
            timeout_s: Timeout in seconds
            
        Returns:
            RolloutStatus object
        """
        try:
            deployment_obj = self.apps_v1.read_namespaced_deployment(
                name=deployment,
                namespace=self.namespace
            )
            
            # Get replica sets
            replicasets = self.apps_v1.list_namespaced_replica_set(
                namespace=self.namespace,
                label_selector=f"app={deployment}"
            )
            
            ready_replicas = deployment_obj.status.ready_replicas or 0
            desired_replicas = deployment_obj.spec.replicas or 0
            
            status = "ready" if ready_replicas == desired_replicas else "pending"
            
            return RolloutStatus(
                deployment=deployment,
                namespace=self.namespace,
                status=status,
                ready_replicas=ready_replicas,
                desired_replicas=desired_replicas,
                updated_replicas=deployment_obj.status.updated_replicas or 0
            )
            
        except ApiException as e:
            logger.error(f"Failed to get rollout status for {deployment}: {e}")
            raise
    
    def deploy_image(self, deployment: str, container: str, image: str) -> None:
        """
        Deploy new image to deployment.
        
        Args:
            deployment: Deployment name
            container: Container name
            image: New image to deploy
        """
        try:
            # Get current deployment
            deployment_obj = self.apps_v1.read_namespaced_deployment(
                name=deployment,
                namespace=self.namespace
            )
            
            # Update image
            for container_spec in deployment_obj.spec.template.spec.containers:
                if container_spec.name == container:
                    container_spec.image = image
                    break
            
            # Apply update
            self.apps_v1.patch_namespaced_deployment(
                name=deployment,
                namespace=self.namespace,
                body=deployment_obj
            )
            
            logger.info(f"✅ Deployed image {image} to {deployment}/{container}")
            
        except ApiException as e:
            logger.error(f"Failed to deploy image {image} to {deployment}: {e}")
            raise
    
    def get_metrics(self, kind: str, window: str = "5m") -> List[Dict[str, Any]]:
        """
        Get metrics for a resource kind.
        
        Args:
            kind: Resource kind (pod, node, etc.)
            window: Time window for metrics
            
        Returns:
            List of metric dictionaries
        """
        # Placeholder for metrics - can be implemented later
        return []

