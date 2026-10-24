"""Published console and module entry point."""

import sys


def main():
    """Dispatch doctor before importing the normal application's runtime."""
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        # The package can already be cached from an earlier normal invocation.
        sys.dont_write_bytecode = True
        from .commands.doctor import main as doctor_main

        return doctor_main()
    from .main import main as application_main

    return application_main()


if __name__ == "__main__":
    main()
