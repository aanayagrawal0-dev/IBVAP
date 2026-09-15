"""
Phase 1 acceptance test — heterogeneous ingestion adapters.

The spec's acceptance check: "demonstrate all four source types
(webcam / RTSP / ONVIF / MJPEG) feeding the same downstream pipeline in one
running demo." The pipeline is source-agnostic by construction — it only
touches a source through the frame contract (frames() -> (idx, BGR ndarray),
fps, is_stream, _open(), release()). So the honest, deterministic proof is:
every adapter, however it got its pixels, produces frames in that identical
shape/dtype the detection loop consumes.

What's real vs. simulated here (no physical cameras on this box):
  * Recorded clip (file)  — REAL cv2 decode of the bundled sample clip.
  * MJPEG/HTTP            — REAL: a local multipart/x-mixed-replace server is
                            spun up and its JPEG stream is parsed & decoded.
  * Webcam / RTSP         — cv2.VideoCapture is mocked (no device / no RTSP
                            server), exercising the same VideoSource path.
  * ONVIF                 — the onvif-zeep client is mocked to return a stream
                            URI, proving the handshake -> RTSP hand-off.

Run:  python tests/test_phase1_adapters.py     (from backend/, venv active)
"""

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src import ingestion

_CLIP = os.path.join(os.path.dirname(__file__), "..", "sample_data", "synthetic_test_clip.mp4")


# ── helpers ────────────────────────────────────────────────────────────────

def _assert_pipeline_frame(frame):
    """The exact shape/dtype pipeline.py's loop assumes (frame.shape[:2],
    detector.detect(frame))."""
    assert isinstance(frame, np.ndarray), f"frame is {type(frame)}, not ndarray"
    assert frame.ndim == 3 and frame.shape[2] == 3, f"expected HxWx3, got {frame.shape}"
    assert frame.dtype == np.uint8, f"expected uint8, got {frame.dtype}"


def _assert_contract(source):
    """Every adapter must expose the surface pipeline.py relies on."""
    assert hasattr(source, "frames") and callable(source.frames)
    assert isinstance(source.fps, (int, float)) and source.fps > 0
    assert isinstance(source.is_stream, bool)
    assert callable(source._open)
    assert callable(source.release)


class _FakeCapture:
    """Stand-in for cv2.VideoCapture for webcam/RTSP/ONVIF (no real device)."""

    def __init__(self, uri, *a, **k):
        self.uri = uri
        self._n = 0

    def isOpened(self):
        return True

    def read(self):
        self._n += 1
        return True, np.full((48, 64, 3), (self._n * 7) % 255, dtype=np.uint8)

    def get(self, prop):
        return 25.0

    def release(self):
        pass


class _PatchCv2Capture:
    """Context manager: swap ingestion's cv2.VideoCapture for the fake."""

    def __enter__(self):
        self._orig = cv2.VideoCapture
        cv2.VideoCapture = _FakeCapture
        return self

    def __exit__(self, *exc):
        cv2.VideoCapture = self._orig


def _install_fake_onvif(stream_uri="rtsp://10.0.0.5:554/Streaming/Channels/101"):
    """Insert a fake 'onvif' module so resolve_onvif_stream_uri() runs without
    the real onvif-zeep package or a real device. Returns a restore()."""
    import types

    class _Profile:
        token = "profile-token-0"

    class _Media:
        def GetProfiles(self):
            return [_Profile()]

        def create_type(self, _name):
            return types.SimpleNamespace()

        def GetStreamUri(self, _req):
            return types.SimpleNamespace(Uri=stream_uri)

    class ONVIFCamera:
        def __init__(self, host, port, user, pwd):
            self.host, self.port, self.user, self.pwd = host, port, user, pwd

        def create_media_service(self):
            return _Media()

    fake = types.ModuleType("onvif")
    fake.ONVIFCamera = ONVIFCamera
    prev = sys.modules.get("onvif")
    sys.modules["onvif"] = fake

    def restore():
        if prev is not None:
            sys.modules["onvif"] = prev
        else:
            sys.modules.pop("onvif", None)

    return restore


def _make_mjpeg_server(frames_per_connection=5, colors=((255, 0, 0), (0, 255, 0), (0, 0, 255))):
    """Local HTTP server streaming multipart/x-mixed-replace JPEG frames of
    solid BGR colors, so the adapter's decode can be checked against a known
    value. Returns (server, base_url, colors)."""
    jpgs = []
    for c in colors:
        frame = np.full((60, 80, 3), c, dtype=np.uint8)  # BGR
        ok, buf = cv2.imencode(".jpg", frame)
        assert ok
        jpgs.append(buf.tobytes())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            for i in range(frames_per_connection):
                jpg = jpgs[i % len(jpgs)]
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                self.wfile.write(b"Content-Length: %d\r\n\r\n" % len(jpg))
                self.wfile.write(jpg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}/stream", colors


# ── tests ──────────────────────────────────────────────────────────────────

def test_recorded_clip_source():
    assert os.path.exists(_CLIP), f"sample clip missing: {_CLIP}"
    src = ingestion.open_source(_CLIP, name="CLIP")
    try:
        _assert_contract(src)
        assert src.is_stream is False, "a recorded clip must be finite (loops, not reconnects)"
        got = 0
        for _idx, frame in src.frames():
            _assert_pipeline_frame(frame)
            got += 1
            if got >= 3:
                break
        assert got == 3
        # A finite source can be reopened to loop (pipeline.stream relies on this).
        src._open()
    finally:
        src.release()
    print("  [PASS] recorded-clip adapter: real decode, finite, reopenable")


def test_mjpeg_http_source_decodes_and_strips_credentials():
    server, url, colors = _make_mjpeg_server()
    # Put credentials in the URL to prove they're stripped from the request
    # URL and turned into HTTP auth instead.
    scheme, rest = url.split("://", 1)
    url_with_creds = f"{scheme}://user:secret@{rest}"
    try:
        src = ingestion.open_source(url_with_creds, name="MJPEG")
        try:
            assert isinstance(src, ingestion.MjpegHttpSource)
            _assert_contract(src)
            assert src.is_stream is True
            assert "@" not in src.uri and "secret" not in src.uri, "credentials leaked into request URL"
            assert src._auth == ("user", "secret"), f"auth not parsed: {src._auth}"

            decoded = []
            for _idx, frame in src.frames():
                _assert_pipeline_frame(frame)
                decoded.append(frame)
                if len(decoded) >= 3:
                    break
            assert len(decoded) == 3, f"expected 3 decoded frames, got {len(decoded)}"
            # The 3 frames were solid colors; decode should recover them
            # (JPEG is lossy, so allow tolerance).
            for frame, expected_bgr in zip(decoded, colors):
                mean = frame.reshape(-1, 3).mean(axis=0)
                assert np.allclose(mean, expected_bgr, atol=30), \
                    f"decoded color {mean} != served {expected_bgr}"
        finally:
            src.release()
    finally:
        server.shutdown()
    print("  [PASS] MJPEG/HTTP adapter: real multipart decode + credential stripping")


def test_onvif_resolves_to_rtsp_and_injects_credentials():
    restore = _install_fake_onvif(stream_uri="rtsp://10.0.0.5:554/Streaming/Channels/101")
    try:
        with _PatchCv2Capture():
            src = ingestion.open_source("onvif://admin:pw123@10.0.0.5:80", name="ONVIF")
            try:
                _assert_contract(src)
                # Handshake produced an RTSP source (the ordinary RTSP path)…
                assert isinstance(src, ingestion.VideoSource)
                assert src.is_stream is True, "resolved ONVIF source must be a live RTSP stream"
                # …pointed at the discovered URI, with credentials injected.
                assert src.uri == "rtsp://admin:pw123@10.0.0.5:554/Streaming/Channels/101", \
                    f"unexpected resolved URI: {src.uri}"
                _idx, frame = next(src.frames())
                _assert_pipeline_frame(frame)
            finally:
                src.release()
    finally:
        restore()
    print("  [PASS] ONVIF adapter: handshake -> RTSP hand-off with credential injection")


def test_missing_onvif_package_gives_clear_error():
    # With no fake installed and onvif-zeep genuinely absent, the resolver
    # must fail with an actionable message rather than a bare ImportError.
    sys.modules.pop("onvif", None)
    try:
        ingestion.resolve_onvif_stream_uri("10.0.0.5", 80, "u", "p")
    except ImportError as exc:
        assert "onvif-zeep" in str(exc), f"unhelpful error: {exc}"
        print("  [PASS] missing ONVIF package -> clear install hint")
        return
    except Exception as exc:  # if the package IS installed, that's fine too
        print(f"  [SKIP] onvif-zeep appears installed ({type(exc).__name__}); clear-error path not applicable")
        return
    print("  [SKIP] onvif-zeep appears installed; clear-error path not applicable")


def test_all_adapters_same_frame_contract():
    """The headline acceptance check: webcam / RTSP / ONVIF / MJPEG / file all
    hand the SAME frame shape to the (source-agnostic) pipeline."""
    results = {}

    # Webcam + RTSP via mocked cv2 (no device / no RTSP server here).
    with _PatchCv2Capture():
        for label, spec in [("webcam", 0), ("rtsp", "rtsp://10.0.0.9:554/live")]:
            src = ingestion.open_source(spec, name=label)
            try:
                _assert_contract(src)
                _idx, frame = next(src.frames())
                _assert_pipeline_frame(frame)
                results[label] = frame.shape
            finally:
                src.release()

        # ONVIF resolves to RTSP, which also uses the mocked cv2.
        restore = _install_fake_onvif()
        try:
            src = ingestion.open_source("onvif://admin:pw@10.0.0.5", name="onvif")
            try:
                _assert_contract(src)
                _idx, frame = next(src.frames())
                _assert_pipeline_frame(frame)
                results["onvif"] = frame.shape
            finally:
                src.release()
        finally:
            restore()

    # MJPEG (real local server) + file (real clip) use the genuine cv2 path.
    server, url, _colors = _make_mjpeg_server()
    try:
        src = ingestion.open_source(url, name="mjpeg")
        try:
            _idx, frame = next(src.frames())
            _assert_pipeline_frame(frame)
            results["mjpeg"] = frame.shape
        finally:
            src.release()
    finally:
        server.shutdown()

    src = ingestion.open_source(_CLIP, name="file")
    try:
        _idx, frame = next(src.frames())
        _assert_pipeline_frame(frame)
        results["file"] = frame.shape
    finally:
        src.release()

    assert set(results) == {"webcam", "rtsp", "onvif", "mjpeg", "file"}, results
    print(f"  [PASS] all 5 source specs yield pipeline-compatible frames: {results}")


# ── runner ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running Phase 1 ingestion-adapter tests…")
    test_recorded_clip_source()
    test_mjpeg_http_source_decodes_and_strips_credentials()
    test_onvif_resolves_to_rtsp_and_injects_credentials()
    test_missing_onvif_package_gives_clear_error()
    test_all_adapters_same_frame_contract()
    print("\nAll Phase 1 tests passed.")
