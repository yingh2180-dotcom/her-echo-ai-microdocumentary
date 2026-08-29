from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


RELEASE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_ROOT))
SPEC = importlib.util.spec_from_file_location(
    "video_download_server", RELEASE_ROOT / "webapp" / "server.py"
)
assert SPEC and SPEC.loader
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


class VideoDownloadTests(unittest.TestCase):
    def test_memory_remotion_result_supports_browser_range_request(self) -> None:
        job_id = "memory-download-test"
        result_name = "final-memory-remotion-v1.mp4"

        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir)
            job_dir = jobs_dir / job_id
            job_dir.mkdir()
            payload = b"0123456789"
            (job_dir / result_name).write_bytes(payload)

            with (
                patch.object(SERVER, "JOBS_DIR", jobs_dir),
                patch.object(
                    SERVER,
                    "JOBS",
                    {job_id: {"id": job_id, "result_file": result_name}},
                ),
            ):
                response = TestClient(SERVER.app).get(
                    f"/api/jobs/{job_id}/download",
                    headers={"Range": "bytes=0-3"},
                )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertEqual(response.headers["content-range"], "bytes 0-3/10")
        self.assertEqual(response.content, payload[:4])


if __name__ == "__main__":
    unittest.main()
