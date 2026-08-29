from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


RELEASE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_ROOT))
SPEC = importlib.util.spec_from_file_location("memory_render_branch_server", RELEASE_ROOT / "webapp" / "server.py")
assert SPEC and SPEC.loader
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


class MemoryRenderBranchTests(unittest.TestCase):
    def test_memory_style_selects_dedicated_remotion_composition(self) -> None:
        job_id = "memory-render-test"
        scene = {"duration_ms": 3000, "text": "那年冬天，我在老屋门口等母亲回来。"}
        updates: list[dict] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir)
            job_dir = jobs_dir / job_id
            job_dir.mkdir()
            fixture = Image.effect_noise((128, 96), 20).convert("RGB")
            fixture.save(job_dir / "board-01.source.png")
            fixture.save(job_dir / "board-01.line.png")

            def fake_run(command, **_kwargs) -> None:
                Path(command[3]).write_bytes(b"remotion-video")

            def fake_update(_job_id: str, **values) -> None:
                updates.append(values)

            def fake_valid_timed(path: Path, _duration_ms: int) -> bool:
                return path.name.endswith(".partial.mp4") and path.exists()

            with (
                patch.object(SERVER, "JOBS_DIR", jobs_dir),
                patch.object(SERVER, "JOBS", {job_id: {"style": SERVER.MEMORY_HANDDRAW_STYLE}}),
                patch.object(SERVER, "is_infographic_job", return_value=False),
                patch.object(SERVER, "run", side_effect=fake_run),
                patch.object(SERVER, "valid_timed_video", side_effect=fake_valid_timed),
                patch.object(SERVER, "valid_media_file", return_value=True),
                patch.object(SERVER, "begin_phase"),
                patch.object(SERVER, "finish_timing"),
                patch.object(SERVER, "update_job", side_effect=fake_update),
                patch.object(SERVER, "fail_job") as fail_job,
            ):
                SERVER.render_generated_job(
                    job_id,
                    [scene],
                    [[scene]],
                    "",
                    False,
                    "detailed",
                    3.0,
                )

            fail_job.assert_not_called()
            props = json.loads((job_dir / "memory-remotion-props.json").read_text(encoding="utf-8"))
            self.assertEqual(props["compositionId"], "MemoryHanddraw")
            self.assertEqual(props["scenes"][0]["lineImage"], "board-01.line.png")
            self.assertEqual(props["scenes"][0]["colorImage"], "board-01.source.png")
            self.assertTrue(any(item.get("render_engine") == "remotion-memory-wipe-v1" for item in updates))


if __name__ == "__main__":
    unittest.main()
