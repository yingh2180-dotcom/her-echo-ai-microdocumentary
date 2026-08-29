from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


RELEASE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_ROOT))
SPEC = importlib.util.spec_from_file_location("memory_remotion_server", RELEASE_ROOT / "webapp" / "server.py")
assert SPEC and SPEC.loader
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


class MemoryRemotionPropsTests(unittest.TestCase):
    def test_groups_scene_duration_by_generated_board(self) -> None:
        boards = [
            [{"duration_ms": 600}, {"duration_ms": 400}],
            [{"duration_ms": 2000}],
        ]

        props = SERVER.remotion_memory_props(boards, 3000)

        self.assertEqual(props["compositionId"], "MemoryHanddraw")
        self.assertEqual(props["totalDurationFrames"], 90)
        self.assertEqual(
            props["scenes"],
            [
                {
                    "id": "memory-1",
                    "lineImage": "board-01.line.png",
                    "colorImage": "board-01.source.png",
                    "startFrame": 0,
                    "endFrame": 30,
                },
                {
                    "id": "memory-2",
                    "lineImage": "board-02.line.png",
                    "colorImage": "board-02.source.png",
                    "startFrame": 30,
                    "endFrame": 90,
                },
            ],
        )

    def test_rejects_board_without_positive_duration(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "缺少有效时长"):
            SERVER.remotion_memory_props([[{"duration_ms": 0}]], 1000)

    def test_pipeline_is_enabled_after_module_three_review(self) -> None:
        self.assertEqual(SERVER.PIPELINE_VERSION, "narrated_deck_v11_memory_remotion")
        self.assertTrue(SERVER.MEMORY_HANDDRAW_PIPELINE_ENABLED)


if __name__ == "__main__":
    unittest.main()
