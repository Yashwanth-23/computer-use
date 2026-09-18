import time
import threading
import pytest
import uvicorn
import requests

from mock_target.app import app
from mock_target import core_data


@pytest.fixture(scope="session", autouse=True)
def run_mock_server():
    """Starts the FastAPI mock server on a background thread for the entire test session."""
    core_data.reset_all()
    server_config = uvicorn.Config(app=app, host="127.0.0.1", port=8000, log_level="warning")
    server = uvicorn.Server(server_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to become responsive
    max_wait = 10.0
    start = time.time()
    while time.time() - start < max_wait:
        try:
            r = requests.get("http://127.0.0.1:8000/")
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        pytest.fail("Mock server failed to start on http://127.0.0.1:8000")

    yield

    server.should_exit = True
