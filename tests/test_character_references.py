from __future__ import annotations

import importlib.util
import io
import json
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from PIL import Image
from starlette.requests import Request


RELEASE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_ROOT))
SPEC = importlib.util.spec_from_file_location("character_reference_server", RELEASE_ROOT / "webapp" / "server.py")
assert SPEC and SPEC.loader
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


def image_upload(name: str = "person.png") -> UploadFile:
    content = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(content, format="PNG", compress_level=0)
    content.seek(0)
    return UploadFile(file=content, filename=name)


class CharacterReferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_job_saves_optional_character_images_and_uses_them_as_references(self) -> None:
        request = Request({"type": "http", "client": ("127.0.0.1", 50000), "headers": []})
        reference = UploadFile(file=io.BytesIO(b"audio-placeholder"), filename="reference.wav")
        manifest = json.dumps([
            {"name": "母亲", "description": "五十岁，短发，深色外套", "file_count": 1},
        ], ensure_ascii=False)

        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir)
            jobs: dict[str, dict] = {}
            voice_queue: queue.Queue = queue.Queue()
            with (
                patch.object(SERVER, "JOBS_DIR", jobs_dir),
                patch.object(SERVER, "JOBS", jobs),
                patch.object(SERVER, "VOICE_QUEUE", voice_queue),
                patch.object(SERVER, "_persist_job_locked"),
                patch.object(SERVER, "ensure_pipeline_workers"),
                patch.object(SERVER, "FFPROBE", "ffprobe"),
                patch.object(SERVER.shutil, "which", return_value=None),
            ):
                result = await SERVER.create_job(
                    request=request,
                    script="这是一段长度足够的标准制作人物参考测试文案。",
                    style=SERVER.DEFAULT_STYLE,
                    scenes_per_image=1,
                    task_name="人物参考测试",
                    pen_text="",
                    include_key_text=True,
                    include_subtitles=True,
                    stroke_detail="detailed",
                    reference=reference,
                    reference_mode="standard",
                    character_manifest=manifest,
                    style_reference=None,
                    character_references=[image_upload()],
                )

                job_id = result["id"]
                metadata = jobs[job_id]
                self.assertEqual(metadata["reference_mode"], "standard")
                self.assertEqual(metadata["character_count"], 1)
                self.assertNotIn("style_image", metadata["visual_references"])
                character = metadata["visual_references"]["characters"][0]
                self.assertEqual(character["name"], "母亲")
                self.assertEqual(character["description"], "五十岁，短发，深色外套")
                self.assertTrue((jobs_dir / job_id / character["images"][0]).is_file())

                paths, instruction, context = SERVER.visual_reference_context(job_id)
                self.assertEqual(len(paths), 1)
                self.assertIn("输入图1", instruction)
                self.assertIn("母亲", instruction)
                self.assertIn("母亲", context)
                self.assertEqual(voice_queue.qsize(), 1)


if __name__ == "__main__":
    unittest.main()
