import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


RELEASE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_ROOT))
SPEC = importlib.util.spec_from_file_location("whiteboard_minimax_server", RELEASE_ROOT / "webapp" / "server.py")
assert SPEC and SPEC.loader
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.is_error = status_code >= 400
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class FakeMiniMaxClient:
    calls: list[dict] = []

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def post(self, url: str, **kwargs):
        call = {"url": url, **kwargs}
        if "files" in call:
            call["upload_name"] = call["files"]["file"][0]
            del call["files"]
        self.calls.append(call)
        if url.endswith("/v1/files/upload"):
            return FakeResponse({
                "file": {"file_id": 12345},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            })
        if url.endswith("/v1/voice_clone"):
            return FakeResponse({"base_resp": {"status_code": 0, "status_msg": "success"}})
        if url.endswith("/v1/t2a_v2"):
            return FakeResponse({
                "data": {"audio": b"RIFFtestWAVE".hex()},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            })
        raise AssertionError(f"Unexpected MiniMax URL: {url}")


class MiniMaxVoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeMiniMaxClient.calls = []
        self.config = {
            "minimax_api_key": "test-only-key",
            "minimax_base_url": "https://api.minimaxi.com",
            "minimax_api_type": "t2a_v2",
            "minimax_speech_model": "speech-2.8-hd",
            "minimax_clone_prefix": "csboard",
        }

    def test_safe_config_never_returns_minimax_key(self) -> None:
        safe = SERVER.safe_config({**SERVER.DEFAULT_CONFIG, "minimax_api_key": "secret-value"})
        self.assertEqual(safe["minimax_api_key"], "********")
        self.assertTrue(safe["has_minimax_api_key"])
        self.assertNotIn("secret-value", str(safe))

    def test_voice_id_is_unique_and_valid(self) -> None:
        voice_id = SERVER.minimax_voice_id({**self.config, "minimax_clone_prefix": "123 bad!"}, "abc123")
        self.assertEqual(voice_id, "v123bad-abc123")
        self.assertTrue(voice_id[0].isalpha())

    def test_upload_clone_and_t2a_preserve_wav_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "reference.wav"
            reference.write_bytes(b"RIFF" + b"x" * 2048)
            target = Path(temporary) / "voice.wav"
            voice_id = "csboard-job123"

            with mock.patch.object(SERVER.httpx, "Client", FakeMiniMaxClient):
                SERVER.clone_minimax_voice(self.config, reference, voice_id)
                SERVER.synthesize_voice(self.config, "这是一段测试旁白。", target, voice_id)

            self.assertEqual(target.read_bytes(), b"RIFFtestWAVE")
            self.assertEqual([call["url"].rsplit("/", 1)[-1] for call in FakeMiniMaxClient.calls], [
                "upload", "voice_clone", "t2a_v2",
            ])
            upload, clone, t2a = FakeMiniMaxClient.calls
            self.assertEqual(upload["headers"]["Authorization"], "Bearer test-only-key")
            self.assertNotIn("Content-Type", upload["headers"])
            self.assertEqual(upload["data"], {"purpose": "voice_clone"})
            self.assertEqual(upload["upload_name"], "reference.wav")
            self.assertEqual(clone["json"]["file_id"], 12345)
            self.assertEqual(clone["json"]["voice_id"], voice_id)
            self.assertEqual(t2a["json"]["voice_setting"]["voice_id"], voice_id)
            self.assertEqual(t2a["json"]["audio_setting"]["format"], "wav")
            self.assertEqual(t2a["json"]["output_format"], "hex")

    def test_activated_voice_cache_reuses_identical_audio(self) -> None:
        old_state_dir = SERVER.STATE_DIR
        old_cache_path = SERVER.VOICE_CACHE_PATH
        try:
            with tempfile.TemporaryDirectory() as temporary:
                SERVER.STATE_DIR = Path(temporary)
                SERVER.VOICE_CACHE_PATH = Path(temporary) / "minimax_voice_cache.json"
                first = Path(temporary) / "first.wav"
                second = Path(temporary) / "second.wav"
                different = Path(temporary) / "different.wav"
                first.write_bytes(b"same-reference-audio")
                second.write_bytes(b"same-reference-audio")
                different.write_bytes(b"different-reference-audio")

                first_hash = SERVER.reference_audio_sha256(first)
                self.assertEqual(first_hash, SERVER.reference_audio_sha256(second))
                self.assertNotEqual(first_hash, SERVER.reference_audio_sha256(different))

                SERVER.remember_minimax_voice(first_hash, "csboard-shared", activated=True, now=100)
                cached = SERVER.cached_minimax_voice(first_hash, now=200)

                self.assertIsNotNone(cached)
                self.assertEqual(cached["voice_id"], "csboard-shared")
                self.assertTrue(cached["activated"])
                self.assertIsNone(SERVER.cached_minimax_voice(SERVER.reference_audio_sha256(different), now=200))
        finally:
            SERVER.STATE_DIR = old_state_dir
            SERVER.VOICE_CACHE_PATH = old_cache_path

    def test_history_seeds_cache_for_an_existing_successful_clone(self) -> None:
        old_state_dir = SERVER.STATE_DIR
        old_cache_path = SERVER.VOICE_CACHE_PATH
        old_jobs_dir = SERVER.JOBS_DIR
        old_jobs = SERVER.JOBS
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                SERVER.STATE_DIR = root
                SERVER.VOICE_CACHE_PATH = root / "minimax_voice_cache.json"
                SERVER.JOBS_DIR = root / "jobs"
                candidate_dir = SERVER.JOBS_DIR / "old-job"
                candidate_dir.mkdir(parents=True)
                reference = candidate_dir / "reference.wav"
                reference.write_bytes(b"historical-reference")
                (candidate_dir / "voice.partial.wav").write_bytes(b"RIFF" + b"x" * 2048)
                SERVER.JOBS = {
                    "old-job": {
                        "created_at": 100,
                        "minimax_voice_cloned": True,
                        "minimax_voice_id": "csboard-old-job",
                    }
                }

                audio_hash = SERVER.reference_audio_sha256(reference)
                cached = SERVER.historical_minimax_voice(audio_hash, "new-job")

                self.assertIsNotNone(cached)
                self.assertEqual(cached["voice_id"], "csboard-old-job")
                self.assertTrue(cached["activated"])
        finally:
            SERVER.STATE_DIR = old_state_dir
            SERVER.VOICE_CACHE_PATH = old_cache_path
            SERVER.JOBS_DIR = old_jobs_dir
            SERVER.JOBS = old_jobs

    def test_minimax_api_error_is_reported(self) -> None:
        response = FakeResponse({
            "base_resp": {"status_code": 1004, "status_msg": "invalid api key"},
        })
        with self.assertRaisesRegex(RuntimeError, "1004"):
            SERVER.minimax_json(response, "voice clone")

    def test_probe_duration_uses_resolved_ffprobe_path(self) -> None:
        completed = mock.Mock(stdout="21.188250\n")
        with mock.patch.object(SERVER.subprocess, "run", return_value=completed) as run:
            duration = SERVER.probe_duration(Path("voice.wav"))

        self.assertAlmostEqual(duration, 21.18825)
        self.assertEqual(run.call_args.args[0][0], str(SERVER.FFPROBE))


if __name__ == "__main__":
    unittest.main()
