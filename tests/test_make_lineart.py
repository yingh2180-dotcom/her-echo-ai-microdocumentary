from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from PIL import Image


RELEASE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_ROOT))

LINEART_SPEC = importlib.util.spec_from_file_location("cs_board_make_lineart", RELEASE_ROOT / "scripts" / "make_lineart.py")
assert LINEART_SPEC and LINEART_SPEC.loader
LINEART = importlib.util.module_from_spec(LINEART_SPEC)
LINEART_SPEC.loader.exec_module(LINEART)

SERVER_SPEC = importlib.util.spec_from_file_location("lineart_integration_server", RELEASE_ROOT / "webapp" / "server.py")
assert SERVER_SPEC and SERVER_SPEC.loader
SERVER = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(SERVER)


def sample_color(width: int = 640, height: int = 360) -> np.ndarray:
    rng = np.random.default_rng(7)
    paper = rng.integers(238, 250, size=(height, width, 1), dtype=np.uint8)
    image = np.repeat(paper, 3, axis=2)
    image[80:280, 350:560] = (55, 170, 65)  # saturated green pencil area
    cv2.rectangle(image, (60, 70), (260, 285), (46, 46, 46), 5)
    cv2.line(image, (70, 260), (250, 90), (35, 35, 35), 4, cv2.LINE_AA)
    return image


def save_bgr(path: Path, image: np.ndarray) -> None:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    Image.fromarray(rgb).save(path)


class MakeLineartTests(unittest.TestCase):
    def test_suppresses_color_but_preserves_neutral_ink(self) -> None:
        image = np.full((180, 240, 3), 248, dtype=np.uint8)
        image[40:140, 135:215] = (55, 180, 65)
        cv2.line(image, (35, 25), (35, 155), (42, 42, 42), 5, cv2.LINE_AA)

        lineart = LINEART.derive_lineart(image)

        self.assertEqual(lineart.shape, image.shape)
        self.assertTrue(np.array_equal(lineart[:, :, 0], lineart[:, :, 1]))
        self.assertTrue(np.array_equal(lineart[:, :, 1], lineart[:, :, 2]))
        self.assertLess(int(lineart[90, 35, 0]), 70)
        self.assertGreater(int(lineart[90, 175, 0]), 235)
        self.assertGreater(int(lineart[15, 15, 0]), 245)

    def test_extracts_unicode_path_with_exact_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "长辈回忆"
            folder.mkdir()
            color = folder / "彩色母图.png"
            line = folder / "黑白线稿.png"
            save_bgr(color, sample_color())

            result = LINEART.extract_lineart(color, line)

            self.assertEqual(result, line)
            self.assertTrue(line.is_file())
            with Image.open(color) as color_image, Image.open(line) as line_image:
                self.assertEqual(color_image.size, line_image.size)
                self.assertEqual(line_image.mode, "RGB")

    def test_encoding_failure_keeps_previous_output_and_cleans_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            color = folder / "color.png"
            line = folder / "line.png"
            save_bgr(color, sample_color())
            save_bgr(line, sample_color(320, 180))
            previous = line.read_bytes()

            with mock.patch.object(LINEART.cv2, "imencode", return_value=(False, None)):
                with self.assertRaisesRegex(RuntimeError, "无法编码"):
                    LINEART.extract_lineart(color, line)

            self.assertEqual(line.read_bytes(), previous)
            self.assertFalse((folder / "line.partial.png").exists())

    def test_rejects_same_path_and_unsupported_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            color = Path(temporary) / "color.png"
            save_bgr(color, sample_color())
            with self.assertRaisesRegex(ValueError, "同一个路径"):
                LINEART.extract_lineart(color, color)
            with self.assertRaisesRegex(ValueError, "不支持"):
                LINEART.extract_lineart(color, Path(temporary) / "line.gif")


class MemoryLineartIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.previous_jobs_dir = SERVER.JOBS_DIR
        self.previous_jobs = SERVER.JOBS
        SERVER.JOBS_DIR = self.root / "jobs"
        SERVER.JOBS = {}

    def tearDown(self) -> None:
        SERVER.JOBS_DIR = self.previous_jobs_dir
        SERVER.JOBS = self.previous_jobs
        self.temporary.cleanup()

    def test_only_memory_style_creates_aligned_lineart(self) -> None:
        color = self.root / "board-01.source.png"
        line = self.root / "board-01.line.png"
        save_bgr(color, sample_color())

        skipped = SERVER.ensure_memory_lineart_asset(SERVER.DEFAULT_STYLE, color, line)
        self.assertIsNone(skipped)
        self.assertFalse(line.exists())

        created = SERVER.ensure_memory_lineart_asset(SERVER.MEMORY_HANDDRAW_STYLE, color, line)
        self.assertEqual(created, line)
        self.assertTrue(SERVER.aligned_image_pair(color, line))

    def test_job_snapshot_counts_lineart_without_counting_it_as_gallery_image(self) -> None:
        job_id = "memory-job"
        job_dir = SERVER.JOBS_DIR / job_id
        job_dir.mkdir(parents=True)
        color = job_dir / "board-01.png"
        line = job_dir / "board-01.line.png"
        save_bgr(color, sample_color())
        save_bgr(line, LINEART.derive_lineart(sample_color()))
        SERVER.JOBS[job_id] = {
            "id": job_id,
            "style": SERVER.MEMORY_HANDDRAW_STYLE,
            "status": "done",
            "copy": "老人回忆测试",
            "created_at": 1.0,
            "started_at": 1.0,
            "finished_at": 2.0,
            "timings": {},
        }

        snapshot = SERVER.job_snapshot(job_id)

        self.assertEqual(snapshot["image_count"], 1)
        self.assertEqual(snapshot["lineart_count"], 1)


if __name__ == "__main__":
    unittest.main()
