from __future__ import annotations

import importlib.util
import io
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from starlette.requests import Request


RELEASE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_ROOT))
SPEC = importlib.util.spec_from_file_location("memory_template_open_server", RELEASE_ROOT / "webapp" / "server.py")
assert SPEC and SPEC.loader
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


class MemoryTemplateOpenTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_template_accepts_standard_job_and_forces_one_scene_per_image(self) -> None:
        request = Request({"type": "http", "client": ("127.0.0.1", 50000), "headers": []})
        reference = UploadFile(file=io.BytesIO(b"audio-placeholder"), filename="reference.wav")

        with tempfile.TemporaryDirectory() as temp_dir:
            jobs: dict[str, dict] = {}
            voice_queue: queue.Queue = queue.Queue()
            with (
                patch.object(SERVER, "JOBS_DIR", Path(temp_dir)),
                patch.object(SERVER, "JOBS", jobs),
                patch.object(SERVER, "VOICE_QUEUE", voice_queue),
                patch.object(SERVER, "FFPROBE", "ffprobe"),
                patch.object(SERVER.shutil, "which", return_value=None),
                patch.object(SERVER, "_persist_job_locked"),
                patch.object(SERVER, "ensure_pipeline_workers"),
            ):
                result = await SERVER.create_job(
                    request=request,
                    script="这是一段用于验证岁月回忆手绘风开放状态的测试文案。",
                    style=SERVER.MEMORY_HANDDRAW_STYLE,
                    scenes_per_image=4,
                    task_name="第十三模板开放测试",
                    pen_text="",
                    include_key_text=True,
                    include_subtitles=True,
                    stroke_detail="detailed",
                    reference=reference,
                    reference_mode="standard",
                    character_manifest="[]",
                    style_reference=None,
                    character_references=None,
                )

            self.assertEqual(jobs[result["id"]]["scenes_per_image"], 1)
            queued = voice_queue.get_nowait()
            self.assertEqual(queued[4], 1)


if __name__ == "__main__":
    unittest.main()
