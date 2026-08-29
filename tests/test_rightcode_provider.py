from __future__ import annotations

import base64
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


RELEASE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_ROOT))

from webapp.providers import rightcode as RIGHTCODE  # noqa: E402


SPEC = importlib.util.spec_from_file_location("whiteboard_rightcode_server", RELEASE_ROOT / "webapp" / "server.py")
assert SPEC and SPEC.loader
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl9sAAAAASUVORK5CYII="
)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.is_error = status_code >= 400
        self.text = str(payload)
        self.content = b""

    def json(self) -> dict:
        return self._payload


class FakeRightCodeClient:
    calls: list[dict] = []
    task_reads = 0

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def request(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if url.endswith("/responses"):
            return FakeResponse({
                "output": [{"content": [{"type": "output_text", "text": '[{"title":"测试"}]'}]}],
            })
        if url.endswith("/images/generations"):
            return FakeResponse({"task_id": "task-test", "status": "processing", "progress": 0})
        if url.endswith("/v1/tasks/task-test"):
            self.__class__.task_reads += 1
            if self.task_reads == 1:
                return FakeResponse({"task_id": "task-test", "status": "in_progress", "progress": 45})
            return FakeResponse({
                "created": 1,
                "data": [{"b64_json": base64.b64encode(ONE_PIXEL_PNG).decode("ascii")}],
            })
        if url.endswith("/models"):
            return FakeResponse({"data": [{"id": "gpt-5.5"}, {"id": "gpt-image-2"}]})
        raise AssertionError(f"Unexpected Right Code URL: {url}")


class RightCodeProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeRightCodeClient.calls = []
        FakeRightCodeClient.task_reads = 0

    def test_text_uses_dedicated_responses_endpoint(self) -> None:
        with mock.patch.object(RIGHTCODE.httpx, "Client", FakeRightCodeClient):
            text = RIGHTCODE.generate_text(
                "rightcode-secret",
                "https://www.rightapi.ai/codex/v1",
                "gpt-5.5",
                "返回 JSON",
            )

        self.assertEqual(text, '[{"title":"测试"}]')
        call = FakeRightCodeClient.calls[0]
        self.assertEqual(call["url"], "https://www.rightapi.ai/codex/v1/responses")
        self.assertEqual(call["headers"]["Authorization"], "Bearer rightcode-secret")
        self.assertEqual(call["json"]["model"], "gpt-5.5")
        self.assertFalse(call["json"]["stream"])

    def test_async_image_sends_references_polls_and_saves_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "person.png"
            reference.write_bytes(ONE_PIXEL_PNG)
            target = root / "scene.png"
            progress: list[int] = []

            with mock.patch.object(RIGHTCODE.httpx, "Client", FakeRightCodeClient):
                RIGHTCODE.generate_image(
                    "rightcode-secret",
                    "https://www.rightapi.ai/draw/v1",
                    "https://www.rightapi.ai/v1/tasks",
                    "gpt-image-2",
                    "生成一张横屏人物插图",
                    target,
                    [reference],
                    poll_interval=0,
                    progress_callback=progress.append,
                )

            self.assertEqual(target.read_bytes(), ONE_PIXEL_PNG)
            submit = FakeRightCodeClient.calls[0]
            self.assertEqual(submit["url"], "https://www.rightapi.ai/draw/v1/images/generations")
            self.assertTrue(submit["json"]["async"])
            self.assertEqual(submit["json"]["size"], "16:9")
            self.assertEqual(submit["json"]["imageSize"], "1K")
            self.assertTrue(submit["json"]["image"][0].startswith("data:image/png;base64,"))
            self.assertEqual(FakeRightCodeClient.calls[1]["url"], "https://www.rightapi.ai/v1/tasks/task-test")
            self.assertEqual(progress, [45])

    def test_safe_config_never_returns_rightcode_key(self) -> None:
        safe = SERVER.safe_config({**SERVER.DEFAULT_CONFIG, "rightcode_api_key": "rightcode-secret"})
        self.assertEqual(safe["rightcode_api_key"], "********")
        self.assertTrue(safe["has_rightcode_api_key"])
        self.assertNotIn("rightcode-secret", str(safe))

    def test_provider_and_job_models_are_pinned(self) -> None:
        current = {**SERVER.DEFAULT_CONFIG, "ai_provider": "gemini"}
        job = {
            "ai_provider": "rightcode",
            "provider_text_model": "gpt-5.5",
            "provider_image_model": "gpt-image-2",
        }
        with (
            mock.patch.object(SERVER, "load_config", return_value=current),
            mock.patch.object(SERVER, "JOBS", {"job-1": job}),
        ):
            result = SERVER.model_config_for_job("job-1")

        self.assertEqual(result["ai_provider"], "rightcode")
        self.assertEqual(result["rightcode_text_model"], "gpt-5.5")
        self.assertEqual(result["rightcode_image_model"], "gpt-image-2")


    def test_server_dispatches_rightcode_images_without_other_providers(self) -> None:
        config = {
            **SERVER.DEFAULT_CONFIG,
            "ai_provider": "rightcode",
            "rightcode_api_key": "test-key",
        }
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "scene.png"

            def fake_generate(_key, _image_url, _task_url, _model, _prompt, output, _references, **_kwargs):
                output.write_bytes(ONE_PIXEL_PNG)

            with (
                mock.patch.object(SERVER, "rightcode_generate_image", side_effect=fake_generate) as rightcode_call,
                mock.patch.object(SERVER, "gemini_generate_image") as gemini_call,
                mock.patch.object(SERVER, "provider_post") as openlux_call,
            ):
                SERVER.generate_image(config, "测试画面", target)

            self.assertEqual(target.read_bytes(), ONE_PIXEL_PNG)
            rightcode_call.assert_called_once()
            gemini_call.assert_not_called()
            openlux_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
