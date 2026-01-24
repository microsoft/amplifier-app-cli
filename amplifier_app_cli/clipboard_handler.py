"""Cross-platform clipboard image handler for Amplifier CLI."""

import base64
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class ClipboardImageHandler:
    """Handle clipboard image paste across macOS, Linux, and Windows."""

    def __init__(self):
        """Initialize handler with platform detection."""
        self.platform = platform.system()
        self.temp_dir = Path(tempfile.gettempdir())
        self.temp_file = self.temp_dir / "amplifier_clipboard_image.png"

    def get_platform_commands(self) -> dict[str, str]:
        """Get platform-specific clipboard commands."""
        temp_path = str(self.temp_file)

        if self.platform == "Darwin":  # macOS
            return {
                "check": "osascript -e 'the clipboard as «class PNGf»'",
                "save": f"osascript -e 'set png_data to (the clipboard as «class PNGf»)' "
                f"-e 'set fp to open for access POSIX file \"{temp_path}\" with write permission' "
                f"-e 'write png_data to fp' "
                f"-e 'close access fp'",
                "clean": f'rm -f "{temp_path}"',
            }
        elif self.platform == "Linux":
            # Try xclip first (X11), fall back to wl-paste (Wayland)
            return {
                "check": "xclip -selection clipboard -t TARGETS -o 2>/dev/null | grep -q image/png || "
                "wl-paste -l 2>/dev/null | grep -q image/png",
                "save": f'xclip -selection clipboard -t image/png -o > "{temp_path}" 2>/dev/null || '
                f'wl-paste --type image/png > "{temp_path}" 2>/dev/null',
                "clean": f'rm -f "{temp_path}"',
            }
        elif self.platform == "Windows":
            # Escape backslashes for PowerShell
            ps_path = temp_path.replace("\\", "\\\\")
            return {
                "check": 'powershell -NoProfile -Command "(Get-Clipboard -Format Image) -ne $null"',
                "save": f'powershell -NoProfile -Command "$img = Get-Clipboard -Format Image; '
                f"if ($img) {{ $img.Save('{ps_path}', [System.Drawing.Imaging.ImageFormat]::Png) }}\"",
                "clean": f'del /f "{temp_path}"',
            }
        else:
            return {"check": "false", "save": "false", "clean": "true"}

    def _run_command(self, command: str, timeout: int = 5) -> tuple[bool, str]:
        """Run shell command and return (success, output)."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=timeout,
                text=True,
            )
            return result.returncode == 0, result.stdout
        except subprocess.TimeoutExpired:
            return False, "Command timed out"
        except Exception as e:
            return False, str(e)

    def has_image_in_clipboard(self) -> bool:
        """Check if clipboard contains an image."""
        commands = self.get_platform_commands()
        success, _ = self._run_command(commands["check"])
        return success

    def read_clipboard_image(self) -> Optional[dict]:
        """
        Read image from clipboard and return ImageBlock structure.

        Returns:
            dict with 'type', 'source' (base64 data), or None if no image/error
        """
        commands = self.get_platform_commands()

        # Check if image exists in clipboard
        if not self.has_image_in_clipboard():
            return None

        try:
            # Save clipboard image to temp file
            success, _ = self._run_command(commands["save"])
            if not success or not self.temp_file.exists():
                return None

            # Read the temp file and encode to base64
            image_data = self.temp_file.read_bytes()
            if len(image_data) == 0:
                return None

            # Detect media type from file header
            media_type = self._detect_media_type(image_data)

            # Encode to base64
            encoded_data = base64.b64encode(image_data).decode("utf-8")

            # Clean up temp file
            self._run_command(commands["clean"])

            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": encoded_data,
                },
                "size_bytes": len(image_data),
            }

        except Exception:
            # Clean up on error
            self._run_command(commands["clean"])
            return None

    def _detect_media_type(self, data: bytes) -> str:
        """Detect image media type from file header bytes."""
        if len(data) < 4:
            return "image/png"

        # PNG: 89 50 4E 47
        if data[0] == 0x89 and data[1] == 0x50 and data[2] == 0x4E and data[3] == 0x47:
            return "image/png"

        # JPEG: FF D8 FF
        if data[0] == 0xFF and data[1] == 0xD8 and data[2] == 0xFF:
            return "image/jpeg"

        # GIF: 47 49 46
        if data[0] == 0x47 and data[1] == 0x49 and data[2] == 0x46:
            return "image/gif"

        # WebP: RIFF ... WEBP
        if len(data) >= 12:
            if (
                data[0] == 0x52
                and data[1] == 0x49
                and data[2] == 0x46
                and data[3] == 0x46
                and data[8] == 0x57
                and data[9] == 0x45
                and data[10] == 0x42
                and data[11] == 0x50
            ):
                return "image/webp"

        # Default to PNG if unknown
        return "image/png"

    def cleanup(self):
        """Clean up temp files."""
        commands = self.get_platform_commands()
        self._run_command(commands["clean"])

    def get_platform_hint(self) -> str:
        """Get platform-specific hint message for when no image is found."""
        if self.platform == "Darwin":
            return "No image in clipboard. Use Cmd+Shift+4 to copy screenshot to clipboard."
        elif self.platform == "Linux":
            return "No image in clipboard. Use screenshot tool to copy to clipboard."
        elif self.platform == "Windows":
            return "No image in clipboard. Use Win+Shift+S to copy screenshot to clipboard."
        else:
            return "No image in clipboard."
