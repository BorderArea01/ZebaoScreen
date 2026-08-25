"""Eyes display server for the Zebao kiosk screen.

Serves eyes_live2d.html and exposes /api/mood endpoints.
Also connects to the Reachy Mini daemon via WebSocket to sync eye
expressions with robot emotion/dance playback. The current deployment runs
on the Raspberry Pi host; Orange Pi deployment files are kept only as legacy
rollback/reference material.
"""
# ruff: noqa: D101, D103
# mypy: disable-error-code=misc

import asyncio
import json
import os
import threading
import time
from collections import deque
from typing import Any

import uvicorn
from eye_scripts import get_script
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# --- Configuration ---
DAEMON_HOST = os.environ.get("DAEMON_HOST", "192.167.123.1")
DAEMON_PORT = os.environ.get("DAEMON_PORT", "8000")
DAEMON_WS_URL = f"ws://{DAEMON_HOST}:{DAEMON_PORT}/api/move/ws/updates"

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PAGES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pages")
_mood = "normal"
_move_active = False
_webview_url: str | None = None
_pixel_text = ""
_pixel_text_message_id: str | None = None
_pixel_text_streaming = False
_pixel_text_until = 0.0
_pixel_text_updated_at = 0.0
_speaking_until = 0.0
_speaking_mood_until = 0.0
_mood_before_speaking: str | None = None
_pipeline_data: dict[str, Any] = {}
_pipeline_until = 0.0
_diag_state: dict[str, Any] = {}
_diag_until = 0.0
_diag_seq = 0
_DIAG_FINAL_BACKLOG_CAPACITY = 64
_DIAG_FINAL_SEEN_CAPACITY = 512
_diag_final_backlog: deque[dict[str, Any]] = deque()
_diag_final_seen_order: deque[str] = deque()
_diag_final_seen_ids: set[str] = set()
_last_activity = time.monotonic()
_lock = threading.Lock()


class _DiagSubscriber:
    """Keep reliable finals separate from coalesced live snapshots."""

    def __init__(self) -> None:
        self.latest: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self.finals: deque[dict[str, Any]] = deque()


_diag_subscribers: set[_DiagSubscriber] = set()


class WebviewRequest(BaseModel):
    url: str


class PixelTextRequest(BaseModel):
    text: str = ""
    message_id: str | None = None
    streaming: bool = False
    final: bool = False
    duration_seconds: float = Field(default=8.0, ge=0.2, le=120.0)


class DiagRequest(BaseModel):
    state: str = ""
    audio_rms: float = 0.0
    audio_peak: float = 0.0
    partial: str = ""
    asr: dict[Any, Any] = Field(default_factory=dict)
    intent: dict[Any, Any] = Field(default_factory=dict)
    gate: dict[Any, Any] = Field(default_factory=dict)
    funasr_ok: bool = False
    mode: str = ""
    wake_names: list[Any] = Field(default_factory=list)


class PipelineRequest(BaseModel):
    stages: dict[Any, Any] = Field(default_factory=dict)
    total_ms: float = 0.0
    trace_id: str | None = None


class SpeakingRequest(BaseModel):
    duration_seconds: float = Field(default=0.0, ge=0.0, le=120.0)
    mood: str | None = None
    reset_mood: bool = True


# --- Script playback state ---
_script_task: asyncio.Task[None] | None = None
_speaking_restore_task: asyncio.Task[None] | None = None


async def run_script(script: list[tuple[float, str]], duration: float) -> None:
    """Play an eye expression script over the given duration.

    Each entry in the script is (relative_time, mood).
    Relative times are 0.0 → 1.0; converted to absolute seconds via duration.
    """
    global _mood
    if not script or duration <= 0:
        return

    t0 = time.monotonic()
    idx = 0
    while idx < len(script):
        elapsed = time.monotonic() - t0
        abs_time = script[idx][0] * duration
        if elapsed >= abs_time:
            with _lock:
                _mood = script[idx][1]
            idx += 1
        else:
            await asyncio.sleep(0.05)
    # Hold last mood until externally reset


@app.on_event("startup")
async def startup() -> None:
    """Start WebSocket client to daemon on server boot."""
    asyncio.create_task(ws_daemon_client())


async def _restore_speaking_mood_after(deadline: float) -> None:
    """Restore temporary speaking mood even if no client polls /api/mood."""
    while True:
        with _lock:
            current_deadline = _speaking_mood_until
        if current_deadline <= 0.0 or current_deadline > deadline + 0.001:
            return
        wait_seconds = max(0.0, current_deadline - time.monotonic())
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        _restore_speaking_mood_if_needed()
        with _lock:
            if _mood_before_speaking is None:
                return
            if _move_active:
                deadline = _speaking_mood_until
        await asyncio.sleep(0.2)


def _schedule_speaking_mood_restore(deadline: float) -> None:
    global _speaking_restore_task
    if deadline <= 0.0:
        return
    if _speaking_restore_task and not _speaking_restore_task.done():
        _speaking_restore_task.cancel()
    _speaking_restore_task = asyncio.create_task(_restore_speaking_mood_after(deadline))


async def ws_daemon_client() -> None:
    """Connect to daemon's move WebSocket and react to move events."""
    # Delay initial connection to let daemon start
    await asyncio.sleep(2)

    while True:
        try:
            import websockets
        except ImportError:
            print("[eyes] websockets not installed, skipping daemon sync")
            return

        try:
            async with websockets.connect(DAEMON_WS_URL) as ws:
                print(f"[eyes] Connected to daemon at {DAEMON_WS_URL}")
                while True:
                    msg = await ws.recv()
                    try:
                        data = json.loads(msg)
                    except json.JSONDecodeError:
                        continue

                    msg_type = data.get("type", "")
                    if msg_type == "move_started":
                        await handle_move_started(data)
                    elif msg_type in ("move_completed", "move_failed", "move_cancelled"):
                        await handle_move_ended()
        except Exception as e:
            print(f"[eyes] Daemon WS disconnected: {e}, retrying in 5s...")
            await asyncio.sleep(5)


async def handle_move_started(data: dict[str, Any]) -> None:
    """Start playing the eye script for a move."""
    global _script_task, _mood, _move_active, _last_activity

    dataset = data.get("dataset", "")
    move = data.get("move", "")
    duration = data.get("duration", 3.0)

    # Cancel any running script
    if _script_task and not _script_task.done():
        _script_task.cancel()

    with _lock:
        _move_active = True
        _last_activity = time.monotonic()

    if move:
        script = get_script(dataset, move)
        if script:
            print(f"[eyes] Move started: {move} (dataset={dataset}, duration={duration:.1f}s)")
            _script_task = asyncio.create_task(run_script(script, duration))
            return

    # Fallback for standard daemon (no dataset/move in WS message)
    _mood = "cheerful"
    print(f"[eyes] Move started (generic, duration={duration:.1f}s)")


async def handle_move_ended() -> None:
    """Reset eyes to normal when a move ends."""
    global _script_task, _mood, _move_active, _last_activity

    if _script_task and not _script_task.done():
        _script_task.cancel()

    with _lock:
        _move_active = False
        _mood = "normal"
        _last_activity = time.monotonic()
    print("[eyes] Move ended, reset to normal")


# --- HTTP Routes ---


def _clip_pixel_text(text: str) -> str:
    """Keep screen text bounded for the 800x480 kiosk surface."""
    clean = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())
    return clean[:420]


def _clear_expired_pixel_text_locked(now: float) -> None:
    global _pixel_text, _pixel_text_message_id, _pixel_text_streaming, _pixel_text_until
    if _pixel_text and _pixel_text_until > 0.0 and now >= _pixel_text_until:
        _pixel_text = ""
        _pixel_text_message_id = None
        _pixel_text_streaming = False
        _pixel_text_until = 0.0


def _diag_envelope_locked(now: float) -> dict[str, Any]:
    diag = dict(_diag_state) if _diag_until > now else {}
    pipeline = dict(_pipeline_data) if _pipeline_until > now else {}
    payload = dict(diag)
    payload["pipeline"] = pipeline
    return {"type": "diag.update", "seq": _diag_seq, "payload": payload}


def _advance_diag_envelope_locked(now: float) -> dict[str, Any]:
    global _diag_seq
    _diag_seq += 1
    return _diag_envelope_locked(now)


def _offer_latest_diag(
    subscriber: _DiagSubscriber,
    envelope: dict[str, Any],
) -> None:
    """Keep at most the newest diagnostic snapshot for a subscriber."""
    queue = subscriber.latest
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    try:
        queue.put_nowait(envelope)
    except asyncio.QueueFull:
        pass


def _broadcast_diag(envelope: dict[str, Any]) -> None:
    for subscriber in tuple(_diag_subscribers):
        _offer_latest_diag(subscriber, envelope)


def _remember_diag_final_id_locked(final_id: str) -> None:
    """Keep a bounded replay-deduplication window for producer reconnects."""
    while len(_diag_final_seen_order) >= _DIAG_FINAL_SEEN_CAPACITY:
        expired = _diag_final_seen_order.popleft()
        _diag_final_seen_ids.discard(expired)
    _diag_final_seen_order.append(final_id)
    _diag_final_seen_ids.add(final_id)


async def _wait_for_diag_subscriber_disconnect(websocket: WebSocket) -> None:
    """Consume ASGI messages until an idle diagnostic subscriber disconnects."""
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return


async def _send_diag_subscriber_updates(
    websocket: WebSocket,
    subscriber: _DiagSubscriber,
) -> None:
    """Forward every final in order while coalescing ordinary snapshots."""
    while True:
        if subscriber.finals:
            envelope = subscriber.finals[0]
            await websocket.send_json(envelope)
            if (
                subscriber.finals
                and subscriber.finals[0].get("final_id")
                == envelope.get("final_id")
            ):
                subscriber.finals.popleft()
            continue
        try:
            envelope = await asyncio.wait_for(
                subscriber.latest.get(),
                timeout=0.1,
            )
        except asyncio.TimeoutError:
            continue
        if subscriber.finals:
            _offer_latest_diag(subscriber, envelope)
            continue
        await websocket.send_json(envelope)


@app.get("/")
async def index():  # type: ignore[no-untyped-def]
    return FileResponse(os.path.join(PAGES, "eyes_live2d.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/api/mood")
async def get_mood():  # type: ignore[no-untyped-def]
    _restore_speaking_mood_if_needed()
    with _lock:
        now = time.monotonic()
        _clear_expired_pixel_text_locked(now)
        speaking_remaining = max(0.0, _speaking_until - now)
        pixel_text_remaining = max(0.0, _pixel_text_until - now) if _pixel_text else 0.0
        return {
            "mood": _mood,
            "move_active": _move_active,
            "webview_url": _webview_url,
            "pixel_text": {
                "text": _pixel_text,
                "message_id": _pixel_text_message_id,
                "streaming": _pixel_text_streaming,
                "remaining_seconds": pixel_text_remaining,
                "updated_at": _pixel_text_updated_at,
            },
            "idle_seconds": now - _last_activity,
            "speaking": speaking_remaining > 0,
            "speaking_remaining_seconds": speaking_remaining,
            "pipeline": _pipeline_data if _pipeline_until > now else {},
            "diag": _diag_state if _diag_until > now else {},
        }


@app.post("/api/mood/{name}")
async def set_mood(name: str):  # type: ignore[no-untyped-def]
    global _mood, _last_activity
    with _lock:
        _mood = name
        _last_activity = time.monotonic()
    return {"mood": _mood}


@app.post("/api/pixel_text")
async def set_pixel_text(req: PixelTextRequest):  # type: ignore[no-untyped-def]
    global _pixel_text, _pixel_text_message_id, _pixel_text_streaming, _pixel_text_until, _pixel_text_updated_at, _last_activity
    text = _clip_pixel_text(req.text)
    message_id = (req.message_id or "").strip() or None
    now = time.monotonic()
    duration = max(0.2, min(120.0, float(req.duration_seconds)))
    with _lock:
        same_stream = bool(
            req.streaming
            and message_id
            and _pixel_text_message_id == message_id
            and _pixel_text
        )
        if same_stream:
            _pixel_text = _clip_pixel_text(_pixel_text + text)
        else:
            _pixel_text = text
        _pixel_text_message_id = message_id
        _pixel_text_streaming = bool(req.streaming and not req.final)
        _pixel_text_until = now + duration if _pixel_text else 0.0
        _pixel_text_updated_at = now
        _last_activity = now
        return {
            "pixel_text": {
                "text": _pixel_text,
                "message_id": _pixel_text_message_id,
                "streaming": _pixel_text_streaming,
                "remaining_seconds": max(0.0, _pixel_text_until - now),
                "updated_at": _pixel_text_updated_at,
            }
        }


@app.delete("/api/pixel_text")
async def clear_pixel_text():  # type: ignore[no-untyped-def]
    global _pixel_text, _pixel_text_message_id, _pixel_text_streaming, _pixel_text_until, _pixel_text_updated_at
    with _lock:
        _pixel_text = ""
        _pixel_text_message_id = None
        _pixel_text_streaming = False
        _pixel_text_until = 0.0
        _pixel_text_updated_at = time.monotonic()
    return {"pixel_text": {"text": "", "message_id": None, "streaming": False, "remaining_seconds": 0.0}}


# --- Diagnostic overlay ---


@app.websocket("/api/diag/ws")
async def diag_websocket(websocket: WebSocket) -> None:
    """Carry local diagnostic snapshots without putting ASR on a poll timer."""
    role = str(websocket.query_params.get("role", "subscriber")).strip().lower()
    if role not in {"producer", "subscriber"}:
        await websocket.close(code=1008, reason="role must be producer or subscriber")
        return

    await websocket.accept()
    if role == "producer":
        try:
            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict) or message.get("type") != "diag.update":
                    await websocket.send_json(
                        {"type": "diag.error", "error": "expected diag.update envelope"}
                    )
                    continue
                payload = message.get("payload")
                if not isinstance(payload, dict):
                    await websocket.send_json(
                        {"type": "diag.error", "error": "payload must be an object"}
                    )
                    continue

                global _diag_state, _diag_until, _pipeline_data, _pipeline_until, _last_activity
                now = time.monotonic()
                pipeline = payload.get("pipeline")
                diag = {key: value for key, value in payload.items() if key != "pipeline"}
                if str(message.get("delivery") or "") == "final":
                    final_id = str(message.get("final_id") or "").strip()[:160]
                    utterance_id = int(message.get("utterance_id", 0) or 0)
                    asr = diag.get("asr")
                    payload_utterance_id = (
                        int(asr.get("utterance_id", 0) or 0)
                        if isinstance(asr, dict)
                        else 0
                    )
                    if (
                        not final_id
                        or utterance_id <= 0
                        or payload_utterance_id != utterance_id
                    ):
                        await websocket.send_json(
                            {
                                "type": "diag.error",
                                "error": "invalid final_id or utterance_id",
                            }
                        )
                        continue
                    with _lock:
                        duplicate = final_id in _diag_final_seen_ids
                        subscribers = tuple(_diag_subscribers)
                        if subscribers:
                            delivery_has_capacity = all(
                                len(subscriber.finals)
                                < _DIAG_FINAL_BACKLOG_CAPACITY
                                for subscriber in subscribers
                            )
                        else:
                            delivery_has_capacity = (
                                len(_diag_final_backlog)
                                < _DIAG_FINAL_BACKLOG_CAPACITY
                            )
                        accepted = duplicate or delivery_has_capacity
                        if accepted and not duplicate:
                            _diag_state = diag
                            _diag_until = float("inf") if diag else 0.0
                            if isinstance(pipeline, dict):
                                _pipeline_data = pipeline
                                _pipeline_until = (
                                    now + 120.0 if pipeline else 0.0
                                )
                            _last_activity = now
                            envelope = _advance_diag_envelope_locked(now)
                            envelope.update(
                                {
                                    "delivery": "final",
                                    "final_id": final_id,
                                    "utterance_id": utterance_id,
                                }
                            )
                            if subscribers:
                                for subscriber in subscribers:
                                    subscriber.finals.append(envelope)
                            else:
                                _diag_final_backlog.append(envelope)
                            _remember_diag_final_id_locked(final_id)
                    if accepted:
                        await websocket.send_json(
                            {
                                "type": "diag.final.ack",
                                "final_id": final_id,
                                "utterance_id": utterance_id,
                                "duplicate": duplicate,
                            }
                        )
                    else:
                        await websocket.send_json(
                            {
                                "type": "diag.final.nack",
                                "final_id": final_id,
                                "utterance_id": utterance_id,
                                "reason": "final_backlog_full",
                                "retryable": True,
                            }
                        )
                    continue
                with _lock:
                    _diag_state = diag
                    # Raw ASR final is cleared by the next utterance, not by a display TTL.
                    _diag_until = float("inf") if diag else 0.0
                    if isinstance(pipeline, dict):
                        _pipeline_data = pipeline
                        _pipeline_until = now + 120.0 if pipeline else 0.0
                    _last_activity = now
                    envelope = _advance_diag_envelope_locked(now)
                _broadcast_diag(envelope)
        except WebSocketDisconnect:
            return

    subscriber = _DiagSubscriber()
    try:
        with _lock:
            while _diag_final_backlog:
                subscriber.finals.append(_diag_final_backlog.popleft())
            _diag_subscribers.add(subscriber)
            snapshot = _diag_envelope_locked(time.monotonic())
        _offer_latest_diag(subscriber, snapshot)
        tasks = {
            asyncio.create_task(_wait_for_diag_subscriber_disconnect(websocket)),
            asyncio.create_task(
                _send_diag_subscriber_updates(websocket, subscriber)
            ),
        }
        try:
            done, _ = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except WebSocketDisconnect:
        return
    finally:
        with _lock:
            _diag_subscribers.discard(subscriber)
            if not _diag_subscribers and subscriber.finals:
                pending = list(subscriber.finals)
                subscriber.finals.clear()
                _diag_final_backlog.extendleft(reversed(pending))


@app.post("/api/diag")
async def set_diag(req: DiagRequest):  # type: ignore[no-untyped-def]
    global _diag_state, _diag_until, _last_activity
    now = time.monotonic()
    with _lock:
        _diag_state = {
            "state": req.state or "",
            "audio_rms": float(req.audio_rms or 0.0),
            "audio_peak": float(req.audio_peak or 0.0),
            "partial": req.partial or "",
            "asr": req.asr if isinstance(req.asr, dict) else {},
            "intent": req.intent if isinstance(req.intent, dict) else {},
            "gate": req.gate if isinstance(req.gate, dict) else {},
            "funasr_ok": bool(req.funasr_ok),
            "mode": req.mode or "",
            "wake_names": req.wake_names if isinstance(req.wake_names, list) else [],
        }
        _diag_until = float("inf")
        _last_activity = now
        envelope = _advance_diag_envelope_locked(now)
    _broadcast_diag(envelope)
    return {"ok": True}


@app.delete("/api/diag")
async def clear_diag():  # type: ignore[no-untyped-def]
    global _diag_state, _diag_until
    with _lock:
        _diag_state = {}
        _diag_until = 0.0
        envelope = _advance_diag_envelope_locked(time.monotonic())
    _broadcast_diag(envelope)
    return {"ok": True}


# --- Pipeline overlay ---

@app.post("/api/pipeline")
async def set_pipeline(req: PipelineRequest):  # type: ignore[no-untyped-def]
    global _pipeline_data, _pipeline_until, _last_activity
    now = time.monotonic()
    with _lock:
        _pipeline_data = {
            "stages": req.stages if isinstance(req.stages, dict) else {},
            "total_ms": float(req.total_ms or 0.0),
            "trace_id": req.trace_id or "",
        }
        _pipeline_until = now + 120.0
        _last_activity = now
        envelope = _advance_diag_envelope_locked(now)
    _broadcast_diag(envelope)
    return {"ok": True, "pipeline": _pipeline_data}

@app.delete("/api/pipeline")
async def clear_pipeline():  # type: ignore[no-untyped-def]
    global _pipeline_data, _pipeline_until
    with _lock:
        _pipeline_data = {}
        _pipeline_until = 0.0
        envelope = _advance_diag_envelope_locked(time.monotonic())
    _broadcast_diag(envelope)
    return {"ok": True}


# --- Webview ---


@app.post("/api/speaking")
async def set_speaking(req: SpeakingRequest):  # type: ignore[no-untyped-def]
    global _mood, _mood_before_speaking, _speaking_until, _speaking_mood_until, _last_activity
    duration = max(0.0, min(120.0, float(req.duration_seconds)))
    restore_deadline = 0.0
    with _lock:
        now = time.monotonic()
        _speaking_until = max(_speaking_until, now + duration)
        clean_mood = (req.mood or "").strip()
        if clean_mood and not _move_active:
            if _mood_before_speaking is None:
                _mood_before_speaking = _mood if _mood != clean_mood else "normal"
            _mood = clean_mood
            _speaking_mood_until = max(_speaking_mood_until, now + duration)
            restore_deadline = _speaking_mood_until
        elif not req.reset_mood:
            _speaking_mood_until = 0.0
        _last_activity = now
        response = {
            "mood": _mood,
            "speaking": _speaking_until > now,
            "speaking_remaining_seconds": max(0.0, _speaking_until - now),
        }
    _schedule_speaking_mood_restore(restore_deadline)
    return response


@app.delete("/api/speaking")
async def clear_speaking():  # type: ignore[no-untyped-def]
    global _speaking_restore_task, _speaking_until, _speaking_mood_until, _last_activity
    with _lock:
        _speaking_until = 0.0
        _speaking_mood_until = 0.0
        _last_activity = time.monotonic()
    if _speaking_restore_task and not _speaking_restore_task.done():
        _speaking_restore_task.cancel()
        _speaking_restore_task = None
    _restore_speaking_mood_if_needed(force=True)
    return {"speaking": False, "speaking_remaining_seconds": 0.0}


def _restore_speaking_mood_if_needed(force: bool = False) -> None:
    global _mood, _mood_before_speaking, _speaking_mood_until
    with _lock:
        if _mood_before_speaking is None:
            return
        now = time.monotonic()
        if not force and (now < _speaking_mood_until or _move_active):
            return
        _mood = _mood_before_speaking or "normal"
        _mood_before_speaking = None
        _speaking_mood_until = 0.0


@app.post("/api/webview")
async def set_webview(req: WebviewRequest):  # type: ignore[no-untyped-def]
    global _webview_url, _last_activity
    with _lock:
        _webview_url = req.url
        _last_activity = time.monotonic()
    return {"webview_url": _webview_url}


@app.delete("/api/webview")
async def clear_webview():  # type: ignore[no-untyped-def]
    global _webview_url, _last_activity
    with _lock:
        _webview_url = None
        _last_activity = time.monotonic()
    return {"webview_url": None}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
