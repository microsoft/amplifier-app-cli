"""Convert explicit file URIs to native paths without filesystem access."""

import nturl2path
from urllib.parse import unquote, urlsplit


def file_uri_path_value(uri: str, *, windows: bool) -> str | None:
    """Return a local filename from a file URI for the named platform.

    ``Path.as_uri()`` emits ``file:///C:/...`` on Windows, while the legacy
    CLI interpolation emits ``file://C:\\...``.  Both forms name a local drive
    there.  A named host is a UNC path only on Windows; on POSIX it is remote
    and deliberately not reinterpreted as a local path.
    """

    try:
        parsed = urlsplit(uri)
    except ValueError:
        return None
    if parsed.scheme.lower() != "file":
        return None

    netloc = parsed.netloc
    if not netloc or netloc.lower() == "localhost":
        encoded_path = parsed.path
    elif windows and len(netloc) >= 2 and netloc[1] == ":" and netloc[0].isalpha():
        # Legacy ``file://C:\path`` serialization.  ``nturl2path`` accepts
        # both backslash and slash separators after the drive.
        encoded_path = netloc + parsed.path
    elif windows and "@" not in netloc and ":" not in netloc:
        # Standard UNC syntax: file://server/share/path -> \\server\share\path.
        try:
            hostname = parsed.hostname
        except ValueError:
            return None
        if not hostname or hostname.lower() != netloc.lower():
            return None
        if not parsed.path.strip("/"):
            return None
        encoded_path = f"//{hostname}{parsed.path}"
    else:
        return None

    # urllib.request.url2pathname follows the host OS. Use percent-decoding
    # explicitly for POSIX so the requested platform, not the host, wins.
    converter = nturl2path.url2pathname if windows else unquote
    try:
        path_value = converter(encoded_path)
    except (OSError, ValueError):
        return None
    return path_value if path_value and "\x00" not in path_value else None