"""Exercise the real in-process client in each CI venv before the long suites."""
from contextlib import asynccontextmanager
from importlib.metadata import version
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestClientCompatibility(unittest.TestCase):
    def test_request_and_lifespan(self):
        events = []

        @asynccontextmanager
        async def lifespan(app):
            events.append("start")
            yield
            events.append("stop")

        app = FastAPI(lifespan=lifespan)

        @app.get("/health")
        def health():
            return {"ready": events == ["start"]}

        with TestClient(app) as client:
            response = client.get("/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"ready": True})
        self.assertEqual(events, ["start", "stop"])


if __name__ == "__main__":
    print({name: version(name) for name in ("fastapi", "starlette", "httpx")}, flush=True)
    unittest.main()
