"""
Configuration settings for deployment dashboard.
"""
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    """Application settings from environment variables."""
    
    # Application
    APP_NAME: str = Field(default="agrarian-deployment-dashboard", description="Application name")
    APP_ENV: str = Field(default="dev", description="Environment: dev|staging|prod")
    
    # HTTP Configuration
    HTTP_PORT: int = Field(default=8002, description="Service port")
    
    # Kubernetes Configuration
    K8S_NAMESPACE: str = Field(default="default", description="Kubernetes namespace")
    K8S_CONTEXT: Optional[str] = Field(default=None, description="Kubernetes context")
    K8S_IN_CLUSTER: bool = Field(default=False, description="Running in cluster")
    
    # GitHub Configuration
    GITHUB_OWNER: str = Field(default="AGRARIAN-Application-Repository", description="GitHub owner")
    GITHUB_REPOSITORY: str = Field(default="agrarian-ecosystem", description="GitHub repository")
    GITHUB_REGISTRY: str = Field(default="ghcr.io", description="GitHub registry")
    
    # Service Configuration
    REQUEST_TIMEOUT_SECS: int = Field(default=30, description="Request timeout")
    LOG_LEVEL: str = Field(default="info", description="Log level: info|debug|warning")
    METRICS_ENABLED: bool = Field(default=True, description="Enable metrics")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


# Global settings instance
settings = Settings()


