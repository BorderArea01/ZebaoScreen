from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import server  # noqa: E402


@pytest.fixture(autouse=True)
def restore_overlay_state() -> None:
    with server._lock:
        original = dict(server._overlay_visibility)
        original_pair = dict(server._phone_pair)
    yield
    with server._lock:
        server._overlay_visibility.clear()
        server._overlay_visibility.update(original)
        server._phone_pair.clear()
        server._phone_pair.update(original_pair)


def test_asr_overlay_starts_hidden() -> None:
    assert server._overlay_visibility == {"asr": False}


def test_asr_overlay_state_is_returned_by_both_screen_channels() -> None:
    result = asyncio.run(
        server.set_overlay_visibility(
            "asr",
            server.OverlayVisibilityRequest(visible=False),
        )
    )

    assert result == {
        "ok": True,
        "overlay_id": "asr",
        "visible": False,
        "overlays": {"asr": {"visible": False}},
    }
    envelope = server._diag_envelope_locked(time.monotonic())
    mood = asyncio.run(server.get_mood())
    assert envelope["payload"]["overlays"] == {"asr": {"visible": False}}
    assert mood["overlays"] == {"asr": {"visible": False}}


def test_unknown_overlay_is_not_an_arbitrary_dom_control() -> None:
    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            server.set_overlay_visibility(
                "pipeline",
                server.OverlayVisibilityRequest(visible=True),
            )
        )

    assert captured.value.status_code == 404
    assert captured.value.detail == "overlay_not_found"


def test_eyes_page_applies_overlay_state_for_ws_and_poll_recovery() -> None:
    page = (SOURCE_ROOT / "pages" / "eyes_live2d.html").read_text(encoding="utf-8")

    assert "function applyOverlayState(source)" in page
    assert "applyOverlayState(payload);" in page
    assert "applyOverlayState(d);" in page
    assert "if(!asrOverlayVisible&&intentEl)" in page


def test_webview_hides_engineering_pipeline_overlay() -> None:
    page = (SOURCE_ROOT / "pages" / "eyes_live2d.html").read_text(encoding="utf-8")

    assert 'if(pipelineEl){pipelineEl.className="";pipelineEl.innerHTML="";}' in page
    assert "if(wvActive||phonePairVisible){pipelineEl.className='';pipelineEl.innerHTML='';return}" in page


def test_phone_pair_payload_never_leaks_through_the_general_mood_snapshot() -> None:
    with server._lock:
        server._phone_pair.update(
            {
                "visible": True,
                "qr_svg": "<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>",
                "message": "扫码，按住说话",
                "expires_at": time.monotonic() + 30.0,
                "updated_at": time.monotonic(),
            }
        )

    mood = asyncio.run(server.get_mood())

    assert mood["phone_pair"]["visible"] is True
    assert "qr_svg" not in mood["phone_pair"]
    assert "扫码" not in str(mood)


def test_phone_pair_validator_accepts_only_a_bounded_plain_svg() -> None:
    svg, message = server._validate_phone_pairing_payload(
        {
            "ok": True,
            "secure_transport": True,
            "qr_svg": "<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>",
        }
    )

    assert svg.startswith("<svg")
    assert message == "扫码，按住说话"
    xml_svg, _ = server._validate_phone_pairing_payload(
        {
            "ok": True,
            "secure_transport": True,
            "qr_svg": "<?xml version=\"1.0\"?><svg xmlns=\"http://www.w3.org/2000/svg\"></svg>",
        }
    )
    assert xml_svg.startswith("<?xml")
    with pytest.raises(ValueError, match="phone_pairing_qr_invalid"):
        server._validate_phone_pairing_payload(
            {"ok": True, "qr_svg": "<svg onload=\"alert(1)\"></svg>"}
        )


def test_eyes_page_uses_a_non_webview_phone_pair_overlay() -> None:
    page = (SOURCE_ROOT / "pages" / "eyes_live2d.html").read_text(encoding="utf-8")

    assert 'id="phonePair"' in page
    assert "function applyPhonePairState(source)" in page
    assert "function currentEyeLayout()" in page
    assert 'fetch("/api/phone-pair"' in page
    assert "body.phone-pair-active" in page
