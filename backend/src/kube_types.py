"""
Type definitions for Kubernetes objects.
"""
from dataclasses import dataclass
from typing import Dict, Any, Optional
from datetime import datetime


@dataclass
class Pod:
    """Kubernetes Pod representation."""
    name: str
    namespace: str
    status: str
    labels: Dict[str, str]
    creation_timestamp: Optional[datetime] = None


@dataclass
class Deployment:
    """Kubernetes Deployment representation."""
    name: str
    namespace: str
    replicas: int
    ready_replicas: int
    labels: Dict[str, str]


@dataclass
class RolloutStatus:
    """Deployment rollout status."""
    deployment: str
    namespace: str
    status: str  # "ready", "pending", "failed"
    ready_replicas: int
    desired_replicas: int
    updated_replicas: int


