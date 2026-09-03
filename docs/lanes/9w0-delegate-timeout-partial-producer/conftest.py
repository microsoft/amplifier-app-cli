"""anyio backend for running this directory's cross-repo checks out of tree.

The round-trip check is NOT part of the repo's own suite -- it needs
amplifier_module_tool_delegate on PYTHONPATH (see DONE-NOTE.md section 6).
"""

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
