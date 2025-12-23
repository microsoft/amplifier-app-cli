"""
Sample Python code for testing grep/search operations.
This fixture contains various patterns that can be searched.
"""


def test_function():
    """A test function that should be found by grep."""
    return "test result"


def another_function(param1, param2):
    """Another function with parameters."""
    result = param1 + param2
    return result


class SampleClass:
    """A sample class for testing."""

    def __init__(self):
        self.value = 42

    def method_one(self):
        """First method."""
        return self.value

    def method_two(self):
        """Second method."""
        return self.value * 2


# Some constants
CONSTANT_VALUE = "search pattern"
DEBUG_MODE = True

# Test data
test_data = {
    "key1": "value1",
    "key2": "value2",
    "key3": "test_function",
}
