from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Any, Callable

import httpx


RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
RetryCallback = Callable[[int], None]
ProgressCallback = Callable[[int], None]
ActiveCallback = Callable[[], None]


class RightCodeHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _base_url(value: str, label: str) -> str:
    url = str(value).strip().rstrip("/")
    if not url.startswith(("https://", "http://")):
        raise RuntimeError(f"Right Code {label}地址无效")
    return url


def _headers(api_key: str) -> dict[str, str]:
    key = str(api_key).strip()
    if not key:
        raise RuntimeError("请先在 API 设置中填写 Right Code API Key")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _error_message(response: httpx.Response, action: str) -> str:
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    detail = str(error.get("message") or error.get("code") or "").strip() if isinstance(error, dict) else ""
    if not detail:
        detail = response.text[:800].strip() or "未返回错误详情"
    if response.status_code in {401, 403}:
        return f"Right Code {action}鉴权或权限不足，请检查 Key、模型限制和余额权限：{detail}"
    if response.status_code == 404:
        return f"Right Code {action}接口或模型不存在：{detail}"
    if response.status_code == 429:
        return f"Right Code {action}额度不足或请求过于频繁：{detail}"
    return f"Right Code {action}失败：{response.status_code} {detail}"


def _retry_delay(attempt: int) -> int:
    return (3, 8, 15)[min(attempt, 2)]


def _request_json(
    method: str,
    url: str,
    api_key: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float,
    action: str,
    retry_callback: RetryCallback | None = None,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.request(method, url, headers=_headers(api_key), json=payload)
            if response.is_error:
                raise RightCodeHTTPError(response.status_code, _error_message(response, action))
            result = response.json()
            if not isinstance(result, dict):
                raise RuntimeError(f"Right Code {action}返回了无效 JSON")
            return result
        except (httpx.TimeoutException, httpx.TransportError, RightCodeHTTPError) as exc:
            last_error = exc
            retryable = not isinstance(exc, RightCodeHTTPError) or exc.status_code in RETRYABLE_STATUS_CODES
            if not retryable or attempt == 2:
                raise
            if retry_callback:
                retry_callback(attempt + 2)
            time.sleep(_retry_delay(attempt))
    raise RuntimeError(f"Right Code {action}重试失败：{last_error}")


def _response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    pieces: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            value = content.get("text") or content.get("output_text")
            if isinstance(value, str) and value.strip():
                pieces.append(value)
    if not pieces:
        for choice in payload.get("choices", []):
            message = choice.get("message") if isinstance(choice, dict) else None
            value = message.get("content") if isinstance(message, dict) else None
            if isinstance(value, str) and value.strip():
                pieces.append(value)
    text = "\n".join(pieces).strip()
    if not text:
        raise RuntimeError("Right Code GPT-5.5 没有返回有效文本")
    return text


def generate_text(
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    *,
    timeout: float = 180,
    retry_callback: RetryCallback | None = None,
) -> str:
    model_id = str(model).strip()
    if not model_id:
        raise RuntimeError("Right Code 文本模型不能为空")
    result = _request_json(
        "POST",
        f"{_base_url(base_url, '文本接口')}/responses",
        api_key,
        payload={"model": model_id, "input": prompt, "stream": False},
        timeout=timeout,
        action="文本调用",
        retry_callback=retry_callback,
    )
    return _response_text(result)


def list_models(api_key: str, base_url: str, *, timeout: float = 30) -> set[str]:
    payload = _request_json(
        "GET",
        f"{_base_url(base_url, '模型接口')}/models",
        api_key,
        timeout=timeout,
        action="模型列表读取",
    )
    return {
        str(item.get("id"))
        for item in payload.get("data", [])
        if isinstance(item, dict) and item.get("id")
    }


def _save_image_result(payload: dict[str, Any], api_key: str, target: Path, timeout: float) -> None:
    candidates = payload.get("data") or payload.get("choices") or []
    if not candidates or not isinstance(candidates[0], dict):
        raise RuntimeError("Right Code GPT Image 2 返回格式中没有图片结果")
    item = candidates[0]
    encoded = item.get("b64_json") or item.get("b64")
    url = item.get("url")
    if isinstance(encoded, str) and encoded:
        if encoded.startswith("data:image"):
            encoded = encoded.split(",", 1)[-1]
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("Right Code 返回的图像 Base64 无效") from exc
    elif isinstance(url, str) and url:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers={"Authorization": f"Bearer {str(api_key).strip()}"})
        if response.is_error:
            raise RightCodeHTTPError(response.status_code, _error_message(response, "图片下载"))
        image_bytes = response.content
    else:
        raise RuntimeError("Right Code GPT Image 2 返回格式中没有 b64_json 或 url")
    if not image_bytes:
        raise RuntimeError("Right Code 返回的图片文件为空")
    target.write_bytes(image_bytes)


def generate_image(
    api_key: str,
    image_base_url: str,
    task_base_url: str,
    model: str,
    prompt: str,
    target: Path,
    reference_images: list[Path] | None = None,
    *,
    timeout: float = 1800,
    poll_interval: float = 2,
    retry_callback: RetryCallback | None = None,
    progress_callback: ProgressCallback | None = None,
    active_callback: ActiveCallback | None = None,
) -> None:
    model_id = str(model).strip()
    if not model_id:
        raise RuntimeError("Right Code 图片模型不能为空")
    payload: dict[str, Any] = {
        "model": model_id,
        "prompt": prompt,
        "n": 1,
        "size": "16:9",
        "imageSize": "1K",
        "async": True,
    }
    references = list(reference_images or [])
    if references:
        payload["image"] = [
            f"data:{mimetypes.guess_type(path.name)[0] or 'image/png'};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
            for path in references
        ]
    submitted = _request_json(
        "POST",
        f"{_base_url(image_base_url, '图片接口')}/images/generations",
        api_key,
        payload=payload,
        timeout=min(timeout, 180),
        action="图片任务提交",
        retry_callback=retry_callback,
    )
    if submitted.get("data"):
        _save_image_result(submitted, api_key, target, min(timeout, 120))
        return
    task_id = str(submitted.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError("Right Code 图片任务没有返回 task_id")
    task_url = f"{_base_url(task_base_url, '任务查询')}/{task_id}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if active_callback:
            active_callback()
        status_payload = _request_json(
            "GET",
            task_url,
            api_key,
            timeout=min(60, timeout),
            action="图片任务查询",
            retry_callback=retry_callback,
        )
        if status_payload.get("data"):
            _save_image_result(status_payload, api_key, target, min(timeout, 120))
            return
        status = str(status_payload.get("status") or "").lower()
        if status == "failed":
            error = status_payload.get("error") or {}
            detail = error.get("message") if isinstance(error, dict) else error
            raise RuntimeError(f"Right Code GPT Image 2 生成失败：{detail or '上游未返回详情'}")
        if status == "completed":
            raise RuntimeError("Right Code 图片任务已完成，但没有返回图片数据")
        if progress_callback:
            progress_callback(max(0, min(99, int(status_payload.get("progress") or 0))))
        time.sleep(max(0, poll_interval))
    raise RuntimeError("Right Code GPT Image 2 生成超时，请稍后重试")
