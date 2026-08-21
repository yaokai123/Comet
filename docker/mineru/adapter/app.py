from __future__ import annotations

import asyncio
import io
import json
import os
import time
import zipfile
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile

app = FastAPI(title="Comet MinerU Adapter")

UPSTREAM = os.getenv("MINERU_UPSTREAM", "http://mineru-api:8000").rstrip("/")
POLL_SECONDS = float(os.getenv("MINERU_POLL_SECONDS", "2"))
TIMEOUT_SECONDS = int(os.getenv("MINERU_TIMEOUT_SECONDS", "1800"))
MINERU_BACKEND = (os.getenv("MINERU_BACKEND", "pipeline") or "pipeline").strip()
MINERU_IMAGE_ANALYSIS = (os.getenv("MINERU_IMAGE_ANALYSIS", "false") or "false").strip().lower()


def _extract_content_list_from_zip(content: bytes) -> tuple[list[dict[str, Any]], str | None]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        preferred = [
            name for name in names
            if name.endswith("_content_list_v2.json") or name.endswith("_content_list.json")
        ]
        if not preferred:
            raise ValueError("zip result does not contain content_list json")
        preferred.sort(key=lambda name: (0 if name.endswith("_content_list_v2.json") else 1, name))
        with archive.open(preferred[0]) as handle:
            payload = json.load(handle)
    if isinstance(payload, dict):
        if isinstance(payload.get("content_list"), list):
            return payload["content_list"], str(payload.get("version") or "") or None
        if isinstance(payload.get("content_list_v2"), list):
            return payload["content_list_v2"], str(payload.get("version") or "") or None
    if isinstance(payload, list):
        return payload, None
    raise ValueError("content_list json has unsupported shape")


def _extract_zip_url(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data)
    for item in candidates:
        for key in ("full_zip_url", "zip_url", "result_url"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _extract_task_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get("task_id")
    if isinstance(direct, str) and direct:
        return direct
    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("task_id")
        if isinstance(nested, str) and nested:
            return nested
    return None


def _extract_state(payload: Any) -> tuple[str | None, dict[str, Any] | None]:
    if not isinstance(payload, dict):
        return None, None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    for item in [payload, data]:
        if not isinstance(item, dict):
            continue
        for key in ("state", "status"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value.lower(), item
    return None, data or payload


def _extract_error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data)
    for item in candidates:
        for key in ("err_msg", "error", "message"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _extract_content_list(payload: Any) -> tuple[list[dict[str, Any]], str | None] | None:
    if isinstance(payload, list):
        return payload, None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data", payload)
    if isinstance(data, list):
        return data, payload.get("version") if isinstance(payload.get("version"), str) else None
    if not isinstance(data, dict):
        return None
    for key in ("content_list", "content_list_v2"):
        value = data.get(key)
        if isinstance(value, list):
            version = data.get("version") or payload.get("version")
            return value, str(version) if version else None
        if isinstance(value, str) and value:
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                version = data.get("version") or payload.get("version")
                return parsed, str(version) if version else None
    results = data.get("results")
    if isinstance(results, dict):
        for item in results.values():
            if not isinstance(item, dict):
                continue
            for key in ("content_list", "content_list_v2"):
                value = item.get(key)
                if isinstance(value, list):
                    version = item.get("version") or data.get("version") or payload.get("version")
                    return value, str(version) if version else None
                if isinstance(value, str) and value:
                    try:
                        parsed = json.loads(value)
                    except json.JSONDecodeError:
                        parsed = None
                    if isinstance(parsed, list):
                        version = item.get("version") or data.get("version") or payload.get("version")
                        return parsed, str(version) if version else None
    return None


async def _fetch_content_list_url(client: httpx.AsyncClient, url: str) -> tuple[list[dict[str, Any]], str | None]:
    response = await client.get(url)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "application/zip" in content_type or response.content[:2] == b"PK":
        return _extract_content_list_from_zip(response.content)
    payload = response.json()
    direct = _extract_content_list(payload)
    if direct is not None:
        return direct
    raise ValueError("MinerU result URL did not include content_list")


async def _download_result(client: httpx.AsyncClient, task_id: str) -> tuple[list[dict[str, Any]], str | None]:
    result_response = await client.get(f"{UPSTREAM}/tasks/{task_id}/result")
    result_response.raise_for_status()
    content_type = result_response.headers.get("content-type", "")
    if "application/zip" in content_type or result_response.content[:2] == b"PK":
        return _extract_content_list_from_zip(result_response.content)
    payload = result_response.json()
    direct = _extract_content_list(payload)
    if direct is not None:
        return direct
    result_url = _extract_zip_url(payload)
    if result_url:
        return await _fetch_content_list_url(client, result_url)
    raise ValueError("MinerU result did not include content_list or result url")


@app.get("/health")
async def health() -> dict[str, str]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{UPSTREAM}/health")
        response.raise_for_status()
    return {"status": "ok"}


@app.post("/parse")
async def parse(file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            submit = await client.post(
                f"{UPSTREAM}/tasks",
                files={"files": (file.filename or "document.pdf", content, file.content_type or "application/pdf")},
                data={
                    "backend": MINERU_BACKEND,
                    "image_analysis": MINERU_IMAGE_ANALYSIS,
                    "return_content_list": "true",
                    "return_middle_json": "true",
                    "return_model_output": "true",
                    "return_md": "true",
                },
            )
            submit.raise_for_status()
            payload = submit.json()
            task_id = _extract_task_id(payload)
            if not isinstance(task_id, str) or not task_id:
                raise ValueError("MinerU submit response missing task_id")

            while True:
                if time.monotonic() - started > TIMEOUT_SECONDS:
                    raise TimeoutError("MinerU parsing timed out")
                status_response = await client.get(f"{UPSTREAM}/tasks/{task_id}")
                status_response.raise_for_status()
                status_payload = status_response.json()
                state, state_payload = _extract_state(status_payload)
                if state in {"done", "success", "completed"}:
                    direct = _extract_content_list(status_payload)
                    if direct is not None:
                        content_list, version = direct
                    else:
                        content_list, version = await _download_result(client, task_id)
                    return {
                        "version": version,
                        "content_list": content_list,
                    }
                if state in {"failed", "error"}:
                    raise RuntimeError(_extract_error_message(status_payload) or "MinerU task failed")
                await asyncio.sleep(POLL_SECONDS)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MinerU adapter failed: {exc}") from exc
