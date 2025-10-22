"""Path resolution for @mentions with search path support."""

import importlib.resources
from pathlib import Path


class MentionResolver:
    """Resolves @mentions to file paths using configured search paths.

    Search order (first match wins):
    1. Relative paths (if starts with ./)
    2. Bundled context (amplifier_app_cli/data/)
    3. Project context (.amplifier/context/)
    4. User context (~/.amplifier/context/)
    """

    def __init__(
        self,
        bundled_data_dir: Path | None = None,
        project_context_dir: Path | None = None,
        user_context_dir: Path | None = None,
        relative_to: Path | None = None,
    ):
        """Initialize resolver with search paths.

        Args:
            bundled_data_dir: Path to bundled data directory (default: package data/)
            project_context_dir: Path to project context (default: .amplifier/context/)
            user_context_dir: Path to user context (default: ~/.amplifier/context/)
            relative_to: Base path for resolving relative mentions (./file)
        """
        if bundled_data_dir is None:
            bundled_data_dir = self._get_bundled_data_dir()

        if project_context_dir is None:
            project_context_dir = Path.cwd() / ".amplifier" / "context"

        if user_context_dir is None:
            user_context_dir = Path.home() / ".amplifier" / "context"

        self.bundled_data_dir = bundled_data_dir
        self.project_context_dir = project_context_dir
        self.user_context_dir = user_context_dir
        self.relative_to = relative_to

    def _get_bundled_data_dir(self) -> Path:
        """Get path to bundled data directory."""
        try:
            if hasattr(importlib.resources, "files"):
                data_path = importlib.resources.files("amplifier_app_cli") / "data"
                # Convert to Path - works for both Path-like and Traversable
                return Path(str(data_path))
        except (ImportError, AttributeError, TypeError):
            pass

        pkg_path = Path(__file__).parent.parent.parent / "data"
        if pkg_path.exists():
            return pkg_path

        return Path.cwd() / "data"

    def resolve(self, mention: str) -> Path | None:
        """Resolve @mention to file path.

        Args:
            mention: @mention string (e.g., '@AGENTS.md', '@bundled/file.md', './relative.md')

        Returns:
            Resolved Path if file exists, None if not found
        """
        if mention.startswith("@"):
            mention = mention[1:]

        if mention.startswith("./") or mention.startswith("../"):
            return self._resolve_relative(mention)

        if mention.startswith("bundled/"):
            return self._resolve_bundled(mention[8:])

        # Try working directory first (for files like ai_context/README.md)
        from pathlib import Path

        cwd_path = Path.cwd() / mention
        if cwd_path.exists() and cwd_path.is_file():
            return cwd_path.resolve()

        paths_to_try = [
            self.bundled_data_dir / "context" / mention,  # Bundled context files
            self.bundled_data_dir / mention,  # Fallback to data root
            self.project_context_dir / mention,
            self.user_context_dir / mention,
        ]

        for path in paths_to_try:
            if path.exists() and path.is_file():
                return path.resolve()

        return None

    def _resolve_relative(self, path: str) -> Path | None:
        """Resolve relative path mention."""
        if self.relative_to is None:
            return None

        resolved = (self.relative_to / path).resolve()
        if resolved.exists() and resolved.is_file():
            return resolved
        return None

    def _resolve_bundled(self, path: str) -> Path | None:
        """Resolve bundled/ prefix path."""
        resolved = self.bundled_data_dir / path
        if resolved.exists() and resolved.is_file():
            return resolved.resolve()
        return None
