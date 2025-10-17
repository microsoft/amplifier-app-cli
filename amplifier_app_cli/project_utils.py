"""Project detection utilities for session storage."""

from pathlib import Path


def get_project_slug() -> str:
    """
    Generate project slug from current working directory.

    The slug is a deterministic identifier based on the absolute path
    of the current working directory. This enables project-scoped
    session storage and filtering.

    Returns:
        Project slug string

    Examples:
        /home/user/repos/myapp → -home-user-repos-myapp
        /tmp → -tmp
        C:\\projects\\web-app → -C-projects-web-app (Windows)
    """
    cwd = Path.cwd().resolve()

    # Replace path separators and colons with hyphens
    slug = str(cwd).replace("/", "-").replace("\\", "-").replace(":", "")

    # Ensure it starts with hyphen for readability
    if not slug.startswith("-"):
        slug = "-" + slug

    return slug
