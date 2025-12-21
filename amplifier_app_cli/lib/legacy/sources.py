"""LEGACY: Module source wrappers for backward compatibility.

DELETE WHEN: Profiles/collections removed in Phase 4.

These wrappers maintain the deprecated amplifier-module-resolution API
while internally using amplifier-foundation mechanisms. This allows
app-cli to remove the external dependency while maintaining backward
compatibility with the legacy profile/collection codepath.

Key implementation notes:
- FileSource: Thin wrapper around Path operations
- GitSource: Uses foundation's GitSourceHandler with asyncio.run() bridge
- Preserves cache metadata format (.amplifier_cache_metadata.json)
- Preserves Python dependency installation in resolve()
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class ModuleResolutionError(Exception):
    """Error resolving module source."""

    pass


class InstallError(Exception):
    """Error installing module."""

    pass


class FileSource:
    """LEGACY: Local filesystem path source.

    DELETE WHEN: Profiles/collections removed in Phase 4.
    """

    def __init__(self, path: str | Path) -> None:
        """Initialize with file path.

        Args:
            path: Absolute or relative path to module directory
        """
        if isinstance(path, str):
            # Handle file:// prefix
            if path.startswith("file://"):
                path = path[7:]
            path = Path(path)

        self.path = path.resolve()

    def resolve(self) -> Path:
        """Resolve to filesystem path."""
        if not self.path.exists():
            raise ModuleResolutionError(f"Module path not found: {self.path}")

        if not self.path.is_dir():
            raise ModuleResolutionError(f"Module path is not a directory: {self.path}")

        # Validate it's a Python module
        if not self._is_valid_module(self.path):
            raise ModuleResolutionError(f"Path does not contain a valid Python module: {self.path}")

        return self.path

    def _is_valid_module(self, path: Path) -> bool:
        """Check if directory contains Python module."""
        return any(path.glob("**/*.py"))

    def __repr__(self) -> str:
        return f"FileSource({self.path})"


class GitSource:
    """LEGACY: Git repository source with caching.

    DELETE WHEN: Profiles/collections removed in Phase 4.

    Uses foundation's async GitSourceHandler internally with asyncio.run() bridge
    for backward compatibility with sync callers.
    """

    def __init__(self, url: str, ref: str = "main", subdirectory: str | None = None) -> None:
        """Initialize with git URL.

        Args:
            url: Git repository URL (without git+ prefix)
            ref: Branch, tag, or commit (default: main)
            subdirectory: Optional subdirectory within repo
        """
        self.url = url
        self.ref = ref
        self.subdirectory = subdirectory
        self.cache_dir = Path.home() / ".amplifier" / "cache" / "modules"
        self._cached_commit_sha: str | None = None

    def _get_effective_url(self) -> str:
        """Get the effective URL, applying shadow environment rewriting if configured."""
        shadow_host = os.getenv("AMPLIFIER_GIT_HOST")
        if not shadow_host:
            return self.url

        # Only rewrite GitHub URLs
        if "github.com" not in self.url:
            return self.url

        # Parse the GitHub URL to extract org/repo
        url_clean = self.url
        if url_clean.endswith(".git"):
            url_clean = url_clean[:-4]

        parts = url_clean.split("github.com/")
        if len(parts) != 2:
            return self.url

        path_parts = parts[1].split("/")
        if len(path_parts) < 2:
            return self.url

        repo = path_parts[1]
        shadow_url = f"{shadow_host.rstrip('/')}/amplifier/{repo}"

        logger.debug(f"Shadow URL rewrite: {self.url} -> {shadow_url}")
        return shadow_url

    @classmethod
    def from_uri(cls, uri: str) -> GitSource:
        """Parse git+https://... URI into GitSource.

        Format: git+https://github.com/org/repo@ref#subdirectory=path

        Args:
            uri: Git URI string

        Returns:
            GitSource instance

        Raises:
            ValueError: Invalid URI format
        """
        if not uri.startswith("git+"):
            raise ValueError(f"Git URI must start with 'git+': {uri}")

        # Remove git+ prefix
        uri = uri[4:]

        # Split on # for subdirectory
        subdirectory = None
        if "#subdirectory=" in uri:
            uri, sub_part = uri.split("#subdirectory=", 1)
            subdirectory = sub_part

        # Split on @ for ref
        ref = "main"
        if "@" in uri:
            # Find last @ (in case URL has @ in it)
            parts = uri.rsplit("@", 1)
            uri, ref = parts[0], parts[1]

        return cls(url=uri, ref=ref, subdirectory=subdirectory)

    def resolve(self) -> Path:
        """Resolve to cached git repository path.

        Returns:
            Path to cached module directory

        Raises:
            InstallError: Git clone failed
        """
        # Generate cache key (includes subdirectory for unique isolation)
        cache_key_input = f"{self.url}@{self.ref}"
        if self.subdirectory:
            cache_key_input += f"#{self.subdirectory}"
        cache_key = hashlib.sha256(cache_key_input.encode()).hexdigest()[:12]
        cache_path = self.cache_dir / cache_key / self.ref

        # Check cache
        if cache_path.exists() and self._is_valid_cache(cache_path):
            logger.debug(f"Using cached git module: {cache_path}")
            return cache_path

        # Get SHA from GitHub API BEFORE downloading
        try:
            current_sha = self._get_remote_sha_sync()
        except Exception as e:
            logger.debug(f"Could not get remote SHA for {self.url}@{self.ref}: {e}")
            current_sha = None

        # Download
        logger.info(f"Downloading git module: {self.url}@{self.ref}")
        try:
            self._download_via_uv(cache_path)
        except subprocess.CalledProcessError as e:
            raise InstallError(f"Failed to download {self.url}@{self.ref}: {e}")

        if not cache_path.exists():
            raise InstallError(f"Module not found after download from {self.url}@{self.ref}")

        # Write cache metadata for update checking (with SHA from API)
        self._write_cache_metadata(cache_path, current_sha)

        return cache_path

    async def install_to(self, target_dir: Path) -> None:
        """Install git repository to target directory (for InstallSourceProtocol).

        Used by collection installer. Downloads repo directly to target_dir.
        Cleans up partial installs on failure to prevent orphaned directories.

        Args:
            target_dir: Directory to install into (will be created)

        Raises:
            InstallError: Git clone failed
        """
        import shutil

        logger.info(f"Installing git repo to {target_dir}: {self.url}@{self.ref}")

        try:
            self._download_via_uv(target_dir)
        except subprocess.CalledProcessError as e:
            # Clean up partial install - uv may have created the directory before failing
            if target_dir.exists():
                logger.debug(f"Cleaning up partial install at {target_dir}")
                shutil.rmtree(target_dir)
            raise InstallError(f"Failed to install {self.url}@{self.ref} to {target_dir}: {e}")

        # Verify installation
        if not target_dir.exists():
            raise InstallError(f"Target directory not created after install: {target_dir}")

        logger.debug(f"Successfully installed to {target_dir}")

    def _is_valid_cache(self, cache_path: Path) -> bool:
        """Check if cache directory contains valid module."""
        return any(cache_path.glob("**/*.py"))

    def _download_via_uv(self, target: Path) -> None:
        """Download git repo using uv.

        Args:
            target: Target directory for download

        Raises:
            subprocess.CalledProcessError: Download failed
        """
        target.parent.mkdir(parents=True, exist_ok=True)

        # Build git URL (using effective URL which may be rewritten)
        effective_url = self._get_effective_url()
        git_url = f"git+{effective_url}@{self.ref}"
        if self.subdirectory:
            git_url += f"#subdirectory={self.subdirectory}"

        # Use uv to download module with its dependencies
        cmd = [
            "uv",
            "pip",
            "install",
            "--target",
            str(target),
            git_url,
        ]

        logger.debug(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        # Check for errors
        if result.returncode != 0:
            error_msg = f"Command {cmd} failed with exit code {result.returncode}"
            if result.stderr:
                error_msg += f"\nError output:\n{result.stderr.strip()}"
            if result.stdout:
                error_msg += f"\nStandard output:\n{result.stdout.strip()}"
            raise subprocess.CalledProcessError(result.returncode, cmd, output=result.stdout, stderr=result.stderr)

    def _write_cache_metadata(self, cache_path: Path, sha: str | None) -> None:
        """Write cache metadata for update checking.

        Args:
            cache_path: Path to cached module directory
            sha: Commit SHA (from GitHub API)
        """
        metadata = {
            "url": self.url,
            "ref": self.ref,
            "sha": sha,
            "cached_at": datetime.now().isoformat(),
            "is_mutable": self._is_mutable_ref(),
        }

        # Match deprecated format: .amplifier_cache_metadata.json
        metadata_file = cache_path / ".amplifier_cache_metadata.json"
        metadata_file.write_text(json.dumps(metadata, indent=2))

    def _get_remote_sha_sync(self) -> str | None:
        """Get SHA from GitHub API synchronously.

        Returns:
            Commit SHA or None if not GitHub or API fails
        """
        if "github.com" not in self.url:
            return None

        try:
            url_clean = self.url[:-4] if self.url.endswith(".git") else self.url
            parts = url_clean.split("github.com/")[-1].split("/")
            if len(parts) < 2:
                return None

            owner, repo = parts[0], parts[1]
            api_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{self.ref}"

            headers = {"Accept": "application/vnd.github.v3+json"}

            # Add authentication if available
            github_token = os.getenv("GITHUB_TOKEN")
            if github_token:
                headers["Authorization"] = f"Bearer {github_token}"
            else:
                # Try gh CLI config
                gh_config_file = Path.home() / ".config" / "gh" / "hosts.yml"
                if gh_config_file.exists():
                    try:
                        import yaml

                        gh_config = yaml.safe_load(gh_config_file.read_text())
                        token = gh_config.get("github.com", {}).get("oauth_token")
                        if token:
                            headers["Authorization"] = f"Bearer {token}"
                    except Exception:
                        pass

            request = urllib.request.Request(api_url, headers=headers)

            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read())
                sha = data.get("sha")
                if sha:
                    logger.debug(f"Retrieved SHA {sha[:7]} for {self.url}@{self.ref}")
                return sha

        except urllib.error.HTTPError as e:
            if e.code == 403:
                logger.warning(
                    f"GitHub API rate limit exceeded for {self.url}@{self.ref}. Set GITHUB_TOKEN to avoid limits."
                )
            else:
                logger.debug(f"GitHub API error for {self.url}@{self.ref}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Could not get remote SHA for {self.url}@{self.ref}: {e}")
            return None

    def _is_mutable_ref(self) -> bool:
        """Check if ref could change over time."""
        import re

        return not re.match(r"^[0-9a-f]{7,40}$", self.ref)

    @property
    def uri(self) -> str:
        """Reconstruct full git+ URI in standard format."""
        git_url = f"git+{self.url}@{self.ref}"
        if self.subdirectory:
            git_url += f"#subdirectory={self.subdirectory}"
        return git_url

    @property
    def commit_sha(self) -> str | None:
        """Get commit SHA from GitHub (cached after first retrieval)."""
        if self._cached_commit_sha is None:
            self._cached_commit_sha = self._get_remote_sha_sync()
        return self._cached_commit_sha

    def __repr__(self) -> str:
        sub = f"#{self.subdirectory}" if self.subdirectory else ""
        return f"GitSource({self.url}@{self.ref}{sub})"


class PackageSource:
    """LEGACY: Installed Python package source.

    DELETE WHEN: Profiles/collections removed in Phase 4.
    """

    def __init__(self, package_name: str) -> None:
        """Initialize with package name.

        Args:
            package_name: Python package name
        """
        self.package_name = package_name

    def resolve(self) -> Path:
        """Resolve to installed package path.

        Returns:
            Path to installed package

        Raises:
            ModuleResolutionError: Package not installed
        """
        from importlib import metadata

        try:
            dist = metadata.distribution(self.package_name)
            if dist.files:
                # Filter out metadata directories
                package_files = [
                    f for f in dist.files if not any(part.endswith((".dist-info", ".data")) for part in f.parts)
                ]
                if package_files:
                    package_path = Path(str(dist.locate_file(package_files[0]))).parent
                    return package_path
                package_path = Path(str(dist.locate_file(dist.files[0]))).parent
                return package_path
            return Path(str(dist.locate_file("")))
        except metadata.PackageNotFoundError:
            raise ModuleResolutionError(
                f"Package '{self.package_name}' not installed. Install with: uv pip install {self.package_name}"
            )

    def __repr__(self) -> str:
        return f"PackageSource({self.package_name})"
