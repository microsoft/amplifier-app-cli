"""Module source implementations - app layer policy.

Concrete implementations of the ModuleSource protocol for various source types:
- FileSource: Local filesystem paths
- GitSource: Git repositories with caching
- PackageSource: Installed Python packages
"""

import hashlib
import logging
import subprocess
from pathlib import Path

from amplifier_core.module_sources import ModuleLoadError
from amplifier_core.module_sources import ModuleNotFoundError
from amplifier_core.module_sources import ModuleSource

logger = logging.getLogger(__name__)


class FileSource(ModuleSource):
    """Local filesystem path source."""

    def __init__(self, path: str | Path):
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
            raise ModuleNotFoundError(f"Module path not found: {self.path}")

        if not self.path.is_dir():
            raise ModuleLoadError(f"Module path is not a directory: {self.path}")

        # Validate it's a Python module
        if not self._is_valid_module(self.path):
            raise ModuleLoadError(f"Path does not contain a valid Python module: {self.path}")

        return self.path

    def _is_valid_module(self, path: Path) -> bool:
        """Check if directory contains Python module."""
        return any(path.glob("**/*.py"))

    def __repr__(self) -> str:
        return f"FileSource({self.path})"


class GitSource(ModuleSource):
    """Git repository source with caching."""

    def __init__(self, url: str, ref: str = "main", subdirectory: str | None = None):
        """Initialize with git URL.

        Args:
            url: Git repository URL (without git+ prefix)
            ref: Branch, tag, or commit (default: main)
            subdirectory: Optional subdirectory within repo
        """
        self.url = url
        self.ref = ref
        self.subdirectory = subdirectory
        self.cache_dir = Path.home() / ".amplifier" / "module-cache"

    @classmethod
    def from_uri(cls, uri: str) -> "GitSource":
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
            ModuleNotFoundError: Git clone failed
        """
        # Generate cache key - include subdirectory to avoid collisions
        # (same repo+ref with different subdirectories need separate caches)
        cache_key_input = f"{self.url}@{self.ref}"
        if self.subdirectory:
            cache_key_input += f"#{self.subdirectory}"
        cache_key = hashlib.sha256(cache_key_input.encode()).hexdigest()[:12]
        cache_path = self.cache_dir / cache_key / self.ref

        # Check cache
        if cache_path.exists() and self._is_valid_cache(cache_path):
            logger.debug(f"Using cached git module: {cache_path}")
            return cache_path

        # Download
        # Note: uv installs the package content FROM subdirectory TO cache_path directly
        # It does NOT create the subdirectory structure in the target
        logger.info(f"Downloading git module: {self.url}@{self.ref}")
        try:
            self._download_via_uv(cache_path)
        except subprocess.CalledProcessError as e:
            raise ModuleNotFoundError(f"Failed to download {self.url}@{self.ref}: {e}")

        # Verify installation worked
        if not self._is_valid_cache(cache_path):
            sub_info = f"#subdirectory={self.subdirectory}" if self.subdirectory else ""
            raise ModuleNotFoundError(f"Failed to install module from {self.url}@{self.ref}{sub_info}")

        return cache_path

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

        # Build git URL
        git_url = f"git+{self.url}@{self.ref}"
        if self.subdirectory:
            git_url += f"#subdirectory={self.subdirectory}"

        # Use uv to download module with its dependencies
        # Note: amplifier-core should NOT be in module dependencies (peer dependency)
        cmd = [
            "uv",
            "pip",
            "install",
            "--target",
            str(target),
            git_url,
        ]

        logger.debug(f"Running: {' '.join(cmd)}")
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    def __repr__(self) -> str:
        sub = f"#{self.subdirectory}" if self.subdirectory else ""
        return f"GitSource({self.url}@{self.ref}{sub})"


class PackageSource(ModuleSource):
    """Installed Python package source."""

    def __init__(self, package_name: str):
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
            ModuleNotFoundError: Package not installed
        """
        try:
            import importlib.metadata

            dist = importlib.metadata.distribution(self.package_name)
            # Get package location
            if dist.files:
                # Get first file's parent to find package root
                package_path = Path(str(dist.locate_file(dist.files[0]))).parent
                return package_path
            # Fallback: use locate_file with empty string
            return Path(str(dist.locate_file("")))
        except importlib.metadata.PackageNotFoundError:
            raise ModuleNotFoundError(
                f"Package '{self.package_name}' not installed. Install with: uv pip install {self.package_name}"
            )

    def __repr__(self) -> str:
        return f"PackageSource({self.package_name})"
