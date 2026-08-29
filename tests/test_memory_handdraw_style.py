from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException, UploadFile
from starlette.requests import Request


RELEASE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_ROOT))
SPEC = importlib.util.spec_from_file_location("memory_handdraw_server", RELEASE_ROOT / "webapp" / "server.py")
assert SPEC and SPEC.loader
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


class MemoryHanddrawStyleTests(unittest.IsolatedAsyncioTestCase):
    def scene(self) -> dict:
        return {
            "title": "窗边的旧时光",
            "concept": "一位女性长辈坐在窗边回忆年轻时在纺织厂工作的片段",
            "text": "那时候我每天清早走进厂房，机器一响，一天就开始了。",
            "elements": ["坐在窗边的女性长辈", "旧厂房中的纺织机"],
        }

    def test_registers_dedicated_memory_style_recipe(self) -> None:
        self.assertIn(SERVER.MEMORY_HANDDRAW_STYLE, SERVER.STYLE_PRESETS)
        recipe = SERVER.style_recipe(SERVER.MEMORY_HANDDRAW_STYLE)
        self.assertIn("低饱和", recipe)
        self.assertIn("真实年龄", recipe)
        self.assertTrue(SERVER.MEMORY_HANDDRAW_PIPELINE_ENABLED)

    def test_builds_one_scene_memory_prompt_without_generic_young_presenter(self) -> None:
        prompt = SERVER.build_board_prompt([self.scene()], SERVER.MEMORY_HANDDRAW_STYLE)

        self.assertLessEqual(len(prompt), 1000)
        self.assertIn("成人口述史微纪录片", prompt)
        self.assertIn("一幕真实记忆", prompt)
        self.assertIn("深灰黑钢笔轮廓", prompt)
        self.assertIn("低饱和彩铅", prompt)
        self.assertIn("不得年轻化", prompt)
        self.assertNotIn("中国青年男性", prompt)

    def test_rejects_multiple_scenes_in_one_memory_image(self) -> None:
        with self.assertRaisesRegex(ValueError, "一幕一图"):
            SERVER.build_board_prompt([self.scene(), self.scene()], SERVER.MEMORY_HANDDRAW_STYLE)

    async def test_job_api_rejects_memory_style_outside_standard_mode(self) -> None:
        request = Request({"type": "http", "client": ("127.0.0.1", 50000), "headers": []})
        reference = UploadFile(file=io.BytesIO(b"placeholder"), filename="reference.wav")

        with self.assertRaises(HTTPException) as raised:
            await SERVER.create_job(
                request=request,
                script="这是一段长度足够的老人回忆测试文案。",
                style=SERVER.MEMORY_HANDDRAW_STYLE,
                scenes_per_image=4,
                task_name="",
                pen_text="",
                include_key_text=True,
                include_subtitles=True,
                stroke_detail="detailed",
                reference=reference,
                reference_mode="custom",
                character_manifest="[]",
                style_reference=None,
                character_references=None,
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("仅支持标准制作模式", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()