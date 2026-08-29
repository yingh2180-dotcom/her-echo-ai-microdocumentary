from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Any, Callable

import httpx


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
RetryCallback = Callable[[int], None]


class GeminiHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _model_id(model: str) -> str:
    value = str(model).strip()
    if value.startswith("models/"):
        value = value.removeprefix("models/")
    if not value or "/" in value:
        raise RuntimeError("Gemini 模型名称无效")
    return value


def _headers(api_key: str) -> dict[str, str]:
    key = str(api_key).strip()
    if not key:
        raise RuntimeError("请先在 API 设置中填写 Gemini API Key")
    return {"x-goog-api-key": key, "Content-Type": "application/json"}


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    detail = str(error.get("message") or error.get("status") or "").strip() if isinstance(error, dict) else ""
    if not detail:
        detail = response.text[:800].strip() or "未返回错误详情"
    if response.status_code in {400, 401} and ("API key" in detail or "API_KEY" in detail):
        return f"Gemini API Key 无效：{detail}"
    if response.status_code == 403:
        return f"Gemini 账号或模型权限不足，请检查计费、地区和模型权限：{detail}"
    if response.status_code == 404:
        return f"Gemini 模型不存在或当前账号不可用：{detail}"
    if response.status_code == 429:
        return f"Gemini 请求超过频率或配额限制：{detail}"
    return f"Gemini 调用失败：{response.status_code} {detail}"


def _retry_delay(attempt: int) -> int:
    return (3, 8, 15)[min(attempt, 2)]


def _post_json(
    api_key: str,
    endpoint: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    retry_callback: RetryCallback | None = None,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    f"{GEMINI_BASE_URL}/{endpoint.lstrip('/')}",
                    headers=_headers(api_key),
                    json=payload,
                )
            if response.is_error:
                raise GeminiHTTPError(response.status_code, _error_message(response))
            result = response.json()
            if not isinstance(result, dict):
                raise RuntimeError("Gemini 返回了无效 JSON")
            return result
        except (httpx.TimeoutException, httpx.TransportError, GeminiHTTPError) as exc:
            last_error = exc
            retryable = not isinstance(exc, GeminiHTTPError) or exc.status_code in RETRYABLE_STATUS_CODES
            if not retryable or attempt == 2:
                raise
            if retry_callback:
                retry_callback(attempt + 2)
            time.sleep(_retry_delay(attempt))
    raise RuntimeError(f"Gemini 服务重试失败：{last_error}")


def generate_text(
    api_key: str,
    model: str,
    prompt: str,
    schema: dict[str, Any],
    *,
    timeout: float = 180,
    retry_callback: RetryCallback | None = None,
) -> str:
    model_id = _model_id(model)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        },
    }
    result = _post_json(
        api_key,
        f"models/{model_id}:generateContent",
        payload,
        timeout=timeout,
        retry_callback=retry_callback,
    )
    pieces: list[str] = []
    for candidate in result.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content") or {}
        for part in content.get("parts", []) if isinstance(content, dict) else []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                pieces.append(part["text"])
    text = "\n".join(piece for piece in pieces if piece.strip()).strip()
    if not text:
        feedback = result.get("promptFeedback") or result.get("prompt_feedback") or {}
        reason = feedback.get("blockReason") or feedback.get("block_reason") if isinstance(feedback, dict) else ""
        suffix = f"：{reason}" if reason else ""
        raise RuntimeError(f"Gemini 未返回有效文本，可能被安全策略拦截{suffix}")
    return text


def generate_image(
    api_key: str,
    model: str,
    prompt: str,
    target: Path,
    reference_images: list[Path] | None = None,
    *,
    timeout: float = 1800,
    retry_callback: RetryCallback | None = None,
) -> None:
    references = list(reference_images or [])
    if len(references) > 14:
        raise RuntimeError("Gemini 单次最多支持 14 张参考图，请减少人物或图片数量")
    inputs: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for path in references:
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        inputs.append({
            "type": "image",
            "mime_type": mime_type,
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        })
    result = _post_json(
        api_key,
        "interactions",
        {
            "model": _model_id(model),
            "input": inputs,
            "response_format": {"type": "image", "aspect_ratio": "16:9"},
        },
        timeout=timeout,
        retry_callback=retry_callback,
    )
    encoded = ""
    for step in result.get("steps", []):
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for block in step.get("content", []):
            if isinstance(block, dict) and block.get("type") == "image" and isinstance(block.get("data"), str):
                encoded = block["data"]
    if not encoded:
        raise RuntimeError("Gemini 没有返回有效图像，可能被安全策略拦截")
    if encoded.startswith("data:image"):
        encoded = encoded.split(",", 1)[-1]
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Gemini 返回的图像 Base64 无效") from exc
    if not image_bytes:
        raise RuntimeError("Gemini 返回的图像文件为空")
    target.write_bytes(image_bytes)


def get_model(api_key: str, model: str, *, timeout: float = 30) -> dict[str, Any]:
    model_id = _model_id(model)
    with httpx.Client(timeout=timeout) as client:
        response = client.get(f"{GEMINI_BASE_URL}/models/{model_id}", headers=_headers(api_key))
    if response.is_error:
        raise GeminiHTTPError(response.status_code, _error_message(response))
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("Gemini 模型信息返回格式无效")
    return result
