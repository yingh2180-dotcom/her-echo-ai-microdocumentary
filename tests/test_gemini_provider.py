from __future__ import annotations

import base64
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


RELEASE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_ROOT))

from webapp.providers import gemini as GEMINI  # noqa: E402


SPEC = importlib.util.spec_from_file_location("whiteboard_gemini_server", RELEASE_ROOT / "webapp" / "server.py")
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
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self) -> dict:
        return self._payload


class FakeGeminiClient:
    calls: list[dict] = []

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def post(self, url: str, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        if url.endswith(":generateContent"):
            return FakeResponse({
                "candidates": [{"content": {"parts": [{"text": '[{"title":"测试"}]'}]}}],
            })
        if url.endswith("/interactions"):
            return FakeResponse({
                "steps": [{
                    "type": "model_output",
                    "content": [{"type": "image", "data": base64.b64encode(ONE_PIXEL_PNG).decode("ascii")}],
                }],
            })
        raise AssertionError(f"Unexpected Gemini URL: {url}")

    def get(self, url: str, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return FakeResponse({"name": "models/gemini-3.1-flash-image"})


class GeminiProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeGeminiClient.calls = []

    def test_safe_config_never_returns_gemini_key(self) -> None:
        safe = SERVER.safe_config({**SERVER.DEFAULT_CONFIG, "gemini_api_key": "gemini-secret"})
        self.assertEqual(safe["gemini_api_key"], "********")
        self.assertTrue(safe["has_gemini_api_key"])
        self.assertNotIn("gemini-secret", str(safe))

    def test_old_config_defaults_to_openlux(self) -> None:
        self.assertEqual(SERVER.selected_ai_provider({}), "openlux")
        self.assertEqual(SERVER.selected_ai_provider({"ai_provider": "gemini"}), "gemini")

    def test_structured_text_uses_official_header_and_schema(self) -> None:
        schema = {"type": "array", "items": {"type": "object"}}
        with mock.patch.object(GEMINI.httpx, "Client", FakeGeminiClient):
            text = GEMINI.generate_text("test-gemini-key", "gemini-3.7-flash", "返回 JSON", schema)

        self.assertEqual(json.loads(text), [{"title": "测试"}])
        call = FakeGeminiClient.calls[0]
        self.assertTrue(call["url"].endswith("/models/gemini-3.7-flash:generateContent"))
        self.assertEqual(call["headers"]["x-goog-api-key"], "test-gemini-key")
        self.assertNotIn("Authorization", call["headers"])
        generation_config = call["json"]["generationConfig"]
        self.assertEqual(generation_config["responseMimeType"], "application/json")
        self.assertEqual(generation_config["responseJsonSchema"], schema)
        self.assertNotIn("responseFormat", generation_config)

    def test_image_generation_sends_references_and_saves_base64(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "person.png"
            reference.write_bytes(ONE_PIXEL_PNG)
            target = root / "scene.png"

            with mock.patch.object(GEMINI.httpx, "Client", FakeGeminiClient):
                GEMINI.generate_image(
                    "test-gemini-key",
                    "gemini-3.1-flash-image",
                    "生成一张横屏人物插图",
                    target,
                    [reference],
                )

            self.assertEqual(target.read_bytes(), ONE_PIXEL_PNG)
            call = FakeGeminiClient.calls[0]
            self.assertTrue(call["url"].endswith("/interactions"))
            self.assertEqual(call["json"]["response_format"], {"type": "image", "aspect_ratio": "16:9"})
            self.assertEqual(call["json"]["input"][1]["type"], "image")
            self.assertEqual(call["json"]["input"][1]["mime_type"], "image/png")

    def test_server_dispatches_gemini_images_without_openlux(self) -> None:
        config = {
            **SERVER.DEFAULT_CONFIG,
            "ai_provider": "gemini",
            "gemini_api_key": "test-key",
        }
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "scene.png"

            def fake_generate(_key, _model, _prompt, output, _references, **_kwargs):
                output.write_bytes(ONE_PIXEL_PNG)

            with (
                mock.patch.object(SERVER, "gemini_generate_image", side_effect=fake_generate) as gemini_call,
                mock.patch.object(SERVER, "provider_post") as openlux_call,
            ):
                SERVER.generate_image(config, "测试画面", target)

            self.assertTrue(target.is_file())
            gemini_call.assert_called_once()
            openlux_call.assert_not_called()

    def test_reference_limit_is_checked_before_network_request(self) -> None:
        references = [Path(f"image-{index}.png") for index in range(15)]
        with self.assertRaisesRegex(RuntimeError, "14"):
            GEMINI.generate_image("test-key", "gemini-3.1-flash-image", "prompt", Path("target.png"), references)


    def test_job_model_config_keeps_the_provider_selected_at_creation(self) -> None:
        current = {
            **SERVER.DEFAULT_CONFIG,
            "ai_provider": "openlux",
            "gemini_text_model": "new-text-model",
            "gemini_image_model": "new-image-model",
        }
        job = {
            "ai_provider": "gemini",
            "provider_text_model": "pinned-text-model",
            "provider_image_model": "pinned-image-model",
        }
        with (
            mock.patch.object(SERVER, "load_config", return_value=current),
            mock.patch.object(SERVER, "JOBS", {"job-1": job}),
        ):
            result = SERVER.model_config_for_job("job-1")

        self.assertEqual(result["ai_provider"], "gemini")
        self.assertEqual(result["gemini_text_model"], "pinned-text-model")
        self.assertEqual(result["gemini_image_model"], "pinned-image-model")


if __name__ == "__main__":
    unittest.main()
