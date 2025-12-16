"""Registry client for fetching and caching module index from amplifier-modules."""

from __future__ import annotations

import json
import logging
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from ..data.profiles import get_system_registry_url

logger = logging.getLogger(__name__)


class RegistryClient:
    """Client for fetching and querying the amplifier-modules registry.

    Provides discovery-only functionality - does NOT handle installation.
    Installation is handled by existing amplifier module add command.
    """

    def __init__(
        self,
        registry_url: str | None = None,
        cache_ttl: int = 3600,
    ):
        """Initialize registry client.

        Args:
            registry_url: URL to registry index.json. If None, uses system default from DEFAULTS.yaml.
            cache_ttl: Cache time-to-live in seconds (default: 1 hour)
        """
        self.registry_url = registry_url or get_system_registry_url()
        self.cache_ttl = cache_ttl
        self.cache_dir = Path.home() / ".amplifier" / "cache"
        self.cache_file = self.cache_dir / "registry-index.json"

    def fetch_index(self, force_refresh: bool = False) -> dict[str, Any]:
        """Fetch registry index with caching.

        Args:
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            Registry index data

        Raises:
            httpx.HTTPError: If network request fails and no cache available
        """
        # Check cache unless force refresh
        if not force_refresh and self.cache_file.exists():
            try:
                cache_data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                fetched_at = datetime.fromisoformat(cache_data["fetched_at"])
                age = (datetime.now(UTC) - fetched_at).total_seconds()

                if age < self.cache_ttl:
                    logger.debug(f"Using cached registry data (age: {age:.0f}s)")
                    return cache_data["data"]
                logger.debug(f"Cache expired (age: {age:.0f}s, ttl: {self.cache_ttl}s)")
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.warning(f"Cache file corrupted, will fetch fresh data: {e}")

        # Fetch from registry
        logger.info(f"Fetching registry index from {self.registry_url}")
        try:
            response = httpx.get(self.registry_url, timeout=10.0, follow_redirects=True)
            response.raise_for_status()
            index_data = response.json()

            # Cache it
            self._save_to_cache(index_data)

            return index_data
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch registry: {e}")
            # Try to use stale cache as fallback
            if self.cache_file.exists():
                try:
                    cache_data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                    logger.warning("Using stale cache due to network error")
                    return cache_data["data"]
                except (json.JSONDecodeError, KeyError):
                    pass
            # No cache available, re-raise error
            raise

    def _save_to_cache(self, index_data: dict[str, Any]) -> None:
        """Save index data to cache file.

        Args:
            index_data: Registry index data to cache
        """
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_data = {"fetched_at": datetime.now(UTC).isoformat(), "data": index_data}
            self.cache_file.write_text(json.dumps(cache_data, indent=2), encoding="utf-8")
            logger.debug(f"Cached registry data to {self.cache_file}")
        except OSError as e:
            logger.warning(f"Failed to save cache: {e}")

    def list_modules(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """List all modules with optional filters.

        Args:
            filters: Optional filters dict with keys:
                - type: Filter by module type
                - verified: If True, show only verified modules

        Returns:
            List of module dictionaries with name and metadata
        """
        index = self.fetch_index()
        modules = []

        for name, module in index.get("modules", {}).items():
            # Apply filters
            if filters:
                if filters.get("type") and module.get("module_type") != filters["type"]:
                    continue
                if filters.get("verified") and not module.get("verified"):
                    continue

            module_dict = {"name": name, **module}
            modules.append(module_dict)

        # Sort by name
        modules.sort(key=lambda m: m["name"])
        return modules

    def search(self, query: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Search modules by name, description, and tags.

        Args:
            query: Search query string
            filters: Optional filters (same as list_modules)

        Returns:
            List of matching modules sorted by relevance, with relevance score added
        """
        index = self.fetch_index()
        results = []
        query_lower = query.lower()

        for name, module in index.get("modules", {}).items():
            # Apply filters first
            if filters:
                if filters.get("type") and module.get("module_type") != filters["type"]:
                    continue
                if filters.get("verified") and not module.get("verified"):
                    continue

            # Calculate relevance score
            relevance = 0

            # Exact name match
            if name.lower() == query_lower:
                relevance = 100
            # Name contains query
            elif query_lower in name.lower():
                relevance = 80
            # Description contains query
            elif query_lower in module.get("description", "").lower():
                relevance = 60
            # Tags contain query
            elif any(query_lower in tag.lower() for tag in module.get("tags", [])):
                relevance = 70

            if relevance > 0:
                module_dict = {"name": name, **module, "relevance": relevance}
                results.append(module_dict)

        # Sort by relevance descending, then by name
        results.sort(key=lambda m: (-m["relevance"], m["name"]))
        return results

    def get_module(self, name: str) -> dict[str, Any] | None:
        """Get details for a specific module.

        Args:
            name: Module name

        Returns:
            Module dictionary with name and metadata, or None if not found
        """
        index = self.fetch_index()
        module = index.get("modules", {}).get(name)

        if module:
            return {"name": name, **module}
        return None

    def get_cache_info(self) -> dict[str, Any]:
        """Get information about the cache.

        Returns:
            Dictionary with cache status information
        """
        if not self.cache_file.exists():
            return {"exists": False, "age_seconds": None, "expired": True}

        try:
            cache_data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(cache_data["fetched_at"])
            age = (datetime.now(UTC) - fetched_at).total_seconds()
            expired = age >= self.cache_ttl

            return {
                "exists": True,
                "fetched_at": fetched_at.isoformat(),
                "age_seconds": age,
                "expired": expired,
                "ttl_seconds": self.cache_ttl,
            }
        except (json.JSONDecodeError, KeyError, ValueError):
            return {"exists": True, "age_seconds": None, "expired": True, "corrupted": True}
