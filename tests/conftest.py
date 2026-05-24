"""Root conftest — enables asyncio auto mode for all flow tests."""

pytest_plugins = []


# Tell pytest-asyncio to automatically mark async test functions
# without needing @pytest.mark.asyncio on every one.
def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark a test as async")
