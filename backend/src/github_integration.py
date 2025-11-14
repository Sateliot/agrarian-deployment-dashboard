#!/usr/bin/env python3
"""
Simple GitHub Packages integration - only uses GitHub REST and GraphQL APIs
No Docker Registry catalog discovery to avoid blocking
"""

import os
import requests
from typing import Dict, List, Optional
from dataclasses import dataclass

@dataclass
class GitHubPackage:
    name: str
    description: str
    image: str
    registry: str
    version: str
    created_at: str
    size: str
    digest: str

class GitHubPackagesIntegration:
    def __init__(self, github_token: str = None, github_org: str = "AGRARIAN-Application-Repository"):
        self.github_token = github_token or os.getenv("GITHUB_TOKEN")
        self.github_org = github_org
        self.ghcr_base_url = "https://api.github.com/orgs"
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
        }
        if self.github_token:
            self.headers["Authorization"] = f"token {self.github_token}"
    
    def get_package_by_name(self, package_name: str) -> Optional[Dict]:
        """Get a specific package by name, even if it doesn't appear in the list API"""
        try:
            # Try to get package directly by name - this works even for manually linked packages
            url = f"{self.ghcr_base_url}/{self.github_org}/packages/container/{package_name}"
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                pkg = response.json()
                return {
                    "name": pkg.get("name"),
                    "package_type": "container",
                    "repository": pkg.get("repository"),
                    "created_at": pkg.get("created_at", ""),
                    "description": pkg.get("description", f"{package_name} application")
                }
            return None
        except Exception as e:
            print(f"Error getting package {package_name} by name: {e}")
            return None
    
    def check_package_exists_in_registry(self, package_name: str) -> bool:
        """Check if a package exists in Docker Registry using Registry API"""
        try:
            import base64
            org_lower = self.github_org.lower()
            
            # Get username from GitHub token
            user_response = requests.get("https://api.github.com/user", headers=self.headers, timeout=10)
            if user_response.status_code != 200:
                return False
            
            user_data = user_response.json()
            username = user_data.get("login")
            
            if not username or not self.github_token:
                return False
            
            # Authenticate to GHCR
            auth_string = f"{username}:{self.github_token}".encode()
            auth_header = base64.b64encode(auth_string).decode()
            
            # Get registry token
            auth_url = "https://ghcr.io/token"
            auth_params = {
                "service": "ghcr.io",
                "scope": f"repository:{org_lower}/{package_name}:pull"
            }
            auth_headers_registry = {
                "Authorization": f"Basic {auth_header}",
                "Accept": "application/json"
            }
            
            auth_response = requests.get(auth_url, headers=auth_headers_registry, params=auth_params, timeout=10)
            if auth_response.status_code != 200:
                return False
            
            auth_data = auth_response.json()
            registry_token = auth_data.get("token")
            if not registry_token:
                return False
            
            # Check if package exists by trying to get its tags
            tags_url = f"https://ghcr.io/v2/{org_lower}/{package_name}/tags/list"
            tags_headers = {
                "Authorization": f"Bearer {registry_token}",
                "Accept": "application/json"
            }
            tags_response = requests.get(tags_url, headers=tags_headers, timeout=5)
            return tags_response.status_code == 200
        except Exception as e:
            print(f"Error checking package {package_name} in registry: {e}")
            return False
    
    def _discover_packages_from_repositories(self) -> List[Dict]:
        """
        Discover packages by iterating through all repositories in the organization.
        This method finds packages that were manually linked to repositories,
        which might not appear in the standard package list APIs.
        """
        discovered_packages = []
        discovered_names = set()
        
        try:
            org = self.github_org
            page = 1
            per_page = 100
            
            # Get all repositories in the organization
            while True:
                repos_url = f"https://api.github.com/orgs/{org}/repos"
                repos_params = {"page": page, "per_page": per_page, "type": "all"}
                repos_response = requests.get(repos_url, headers=self.headers, timeout=10)
                
                if repos_response.status_code != 200:
                    break
                
                repositories = repos_response.json()
                if not repositories:
                    break
                
                # For each repository, check if it has packages
                for repo in repositories:
                    repo_name = repo.get("name")
                    if not repo_name:
                        continue
                    
                    # Get packages for this repository
                    # Note: GitHub API doesn't have a direct endpoint, but we can try
                    # to get package info by checking the repository's packages endpoint
                    try:
                        # Try to get packages associated with this repository
                        # We'll use GraphQL to query packages for this specific repository
                        graphql_query = """
                        {
                          repository(owner: "%s", name: "%s") {
                            packages(first: 100, packageType: DOCKER) {
                              nodes {
                                name
                                id
                                repository {
                                  name
                                  owner {
                                    login
                                  }
                                }
                              }
                            }
                          }
                        }
                        """ % (org, repo_name)
                        
                        graphql_response = requests.post(
                            "https://api.github.com/graphql",
                            headers={**self.headers, "Content-Type": "application/json"},
                            json={"query": graphql_query},
                            timeout=5
                        )
                        
                        if graphql_response.status_code == 200:
                            data = graphql_response.json()
                            if "data" in data and "repository" in data["data"]:
                                repo_data = data["data"]["repository"]
                                if repo_data:
                                    packages_data = repo_data.get("packages", {})
                                    packages = packages_data.get("nodes", [])
                                    
                                    for pkg in packages:
                                        pkg_name = pkg.get("name")
                                        if pkg_name and pkg_name not in discovered_names:
                                            discovered_packages.append({
                                                "name": pkg_name,
                                                "package_type": "container",
                                                "repository": pkg.get("repository"),
                                                "created_at": "",
                                                "description": f"{pkg_name} application"
                                            })
                                            discovered_names.add(pkg_name)
                    except Exception as e:
                        # Continue with next repository if this one fails
                        continue
                
                # Check if there are more pages
                if len(repositories) < per_page:
                    break
                page += 1
                
        except Exception as e:
            print(f"Error discovering packages from repositories: {e}")
        
        return discovered_packages
    
    def get_available_packages(self, additional_package_names: List[str] = None) -> List[GitHubPackage]:
        """
        Get all available packages from GitHub Packages API (REST + GraphQL)
        Also discovers packages by checking all repositories (catches manually linked packages)
        """
        try:
            all_packages = []
            all_package_names = set()
            
            # Step 1: Get packages from REST API
            try:
                url = f"{self.ghcr_base_url}/{self.github_org}/packages"
                page = 1
                per_page = 100
                
                while True:
                    params = {"package_type": "container", "page": page, "per_page": per_page}
                    response = requests.get(url, headers=self.headers, params=params, timeout=10)
                    response.raise_for_status()
                    
                    page_packages = response.json()
                    if not page_packages:
                        break
                    
                    for pkg in page_packages:
                        pkg_name = pkg.get("name")
                        if pkg_name and pkg_name not in all_package_names:
                            all_packages.append(pkg)
                            all_package_names.add(pkg_name)
                    
                    if len(page_packages) < per_page:
                        break
                    page += 1
            except Exception as e:
                print(f"Note: REST API failed: {e}")
            
            # Step 2: Supplement with GraphQL API
            try:
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
                                id
                                packageType
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
                        """ % (self.github_org, cursor)
                    else:
                        graphql_query = """
                        {
                          organization(login: "%s") {
                            packages(first: 100, packageType: DOCKER) {
                              nodes {
                                name
                                id
                                packageType
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
                        """ % self.github_org
                    
                    graphql_response = requests.post(
                        "https://api.github.com/graphql",
                        headers={**self.headers, "Content-Type": "application/json"},
                        json={"query": graphql_query},
                        timeout=30
                    )
                    
                    if graphql_response.status_code == 200:
                        data = graphql_response.json()
                        if "errors" in data:
                            print(f"GraphQL errors: {data['errors']}")
                            break
                        
                        if "data" in data and "organization" in data["data"]:
                            packages_data = data["data"]["organization"].get("packages", {})
                            graphql_packages = packages_data.get("nodes", [])
                            page_info = packages_data.get("pageInfo", {})
                            
                            for pkg in graphql_packages:
                                pkg_name = pkg.get("name")
                                if pkg_name and pkg_name not in all_package_names:
                                    all_packages.append({
                                        "name": pkg_name,
                                        "package_type": "container",
                                        "repository": pkg.get("repository")
                                    })
                                    all_package_names.add(pkg_name)
                            
                            has_next_page = page_info.get("hasNextPage", False)
                            cursor = page_info.get("endCursor")
                        else:
                            break
                    else:
                        break
            except Exception as e:
                print(f"Note: GraphQL API failed: {e}")
            
            # Step 3: Discover packages by iterating through repositories
            # This catches packages that were manually linked to repositories
            try:
                repo_discovered = self._discover_packages_from_repositories()
                for pkg in repo_discovered:
                    if pkg["name"] not in all_package_names:
                        all_packages.append(pkg)
                        all_package_names.add(pkg["name"])
                        print(f"[Package Discovery] Found package via repository scan: {pkg['name']}")
            except Exception as e:
                print(f"Note: Repository-based discovery failed: {e}")
            
            # Step 4: Check additional packages (if provided, but repository scan should catch most)
            if additional_package_names:
                print(f"[Package Discovery] Checking {len(additional_package_names)} additional packages: {additional_package_names}")
                for pkg_name in additional_package_names:
                    pkg_name = pkg_name.strip()
                    if not pkg_name or pkg_name in all_package_names:
                        continue
                    
                    # First check if it exists in registry
                    if self.check_package_exists_in_registry(pkg_name):
                        # Try to get it via direct API call
                        pkg_details = self.get_package_by_name(pkg_name)
                        if pkg_details:
                            all_packages.append(pkg_details)
                            all_package_names.add(pkg_name)
                            print(f"[Package Discovery] Found manually linked package: {pkg_name}")
                        else:
                            # Package exists in registry but GitHub API doesn't return it
                            # Create a minimal package entry
                            all_packages.append({
                                "name": pkg_name,
                                "package_type": "container",
                                "repository": None,
                                "created_at": "",
                                "description": f"{pkg_name} application"
                            })
                            all_package_names.add(pkg_name)
                            print(f"[Package Discovery] Found package in registry (no GitHub API data): {pkg_name}")
            
            # Convert to GitHubPackage objects
            packages = []
            for package in all_packages:
                if package.get("package_type") == "container":
                    package_name = package["name"]
                    try:
                        latest_version = self._get_latest_package_version(package_name)
                        if latest_version:
                            tag = latest_version.get("tag", latest_version.get("name", "latest"))
                            packages.append(GitHubPackage(
                                name=package_name,
                                description=package.get("description", f"{package_name} application"),
                                image=f"ghcr.io/{self.github_org.lower()}/{package_name}:{tag}",
                                registry="ghcr.io",
                                version=tag,
                                created_at=latest_version["created_at"],
                                size=self._format_size(latest_version.get("size", 0)),
                                digest=latest_version.get("name", "")
                            ))
                        else:
                            packages.append(GitHubPackage(
                                name=package_name,
                                description=package.get("description", f"{package_name} application"),
                                image=f"ghcr.io/{self.github_org.lower()}/{package_name}:latest",
                                registry="ghcr.io",
                                version="latest",
                                created_at=package.get("created_at", ""),
                                size="0 B",
                                digest=""
                            ))
                    except Exception as e:
                        print(f"Warning: Could not get versions for {package_name}: {e}")
                        packages.append(GitHubPackage(
                            name=package_name,
                            description=package.get("description", f"{package_name} application"),
                            image=f"ghcr.io/{self.github_org.lower()}/{package_name}:latest",
                            registry="ghcr.io",
                            version="latest",
                            created_at=package.get("created_at", ""),
                            size="0 B",
                            digest=""
                        ))
            return packages
        except Exception as e:
            error_msg = f"Error getting packages from GitHub: {e}"
            print(error_msg)
            if not self.github_token:
                raise Exception(f"{error_msg}. No GitHub token configured.")
            else:
                raise Exception(f"{error_msg}. Please check token permissions.")
    
    def _get_package_versions(self, package_name: str) -> List[Dict]:
        """Get versions of a specific package"""
        try:
            url = f"{self.ghcr_base_url}/{self.github_org}/packages/container/{package_name}/versions"
            response = requests.get(url, headers=self.headers, timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error getting versions for {package_name}: {e}")
            return []
    
    def _get_latest_package_version(self, package_name: str) -> Optional[Dict]:
        """Get the latest version of a package"""
        try:
            versions = self._get_package_versions(package_name)
            if not versions:
                return None
            
            sorted_versions = sorted(versions, key=lambda x: x.get("created_at", ""), reverse=True)
            latest = sorted_versions[0]
            
            # Try to find 'latest' tag, otherwise use most recent
            tag_to_use = "latest"
            for version in sorted_versions:
                metadata = version.get("metadata", {})
                container = metadata.get("container", {})
                tags = container.get("tags", [])
                if "latest" in tags:
                    tag_to_use = "latest"
                    latest = version
                    break
            
            version_with_tag = latest.copy()
            version_with_tag["tag"] = tag_to_use
            return version_with_tag
        except Exception as e:
            print(f"Error getting latest version for {package_name}: {e}")
            return None
    
    def _format_size(self, size_bytes: int) -> str:
        """Format size in bytes to human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

