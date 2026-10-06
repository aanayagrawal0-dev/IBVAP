"""
Phase 9 acceptance test — live CCTV gateway readiness (Sentinel grid rules).

Maps one-to-one onto the gateway's pre-submission checklist, hermetically
(no network, no gateway account, no YOLO weights):

  1. RTSP forced over TCP; HLS dispatched to FFmpeg, never to the MJPEG path
  2. No timing depends on CAP_PROP_FPS or arrival time (PTS clock: GOP burst,
     gaps, backward jumps, no-PTS fallback; PTS-driven media index)
  3. Inter-frame gaps don't crash/stall; a slow consumer gets the newest frame
  4. Reconnect with exponential backoff (2s -> 30s cap), tested by
     "restarting a feed" (fake capture that dies and comes back); a failing
     initial connect doesn't kill the worker
  5. Decoder trouble at join is not fatal (transient failed reads recover)
  6. Camera list + per-camera properties read from the catalogue; credentials
     never stored; load paced to the selected cameras
  7. Mixed H.264/H.265 + mixed resolutions flow through the same contract
  8. Sane across a scene discontinuity: loop-point cut detected, track ids
     never reused, per-scene state reset

Run:  python tests/test_phase9_live_gateway.py     (from backend/, venv active)
"""

import json
import os
os.environ["IBVAP_SKIP_DOTENV"] = "1"  # keep the developer's real .env out of tests
os.environ["IBVAP_ROAD_ROUTING"] = "0"  # no online routing calls from tests
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import cv2  # noqa: E402
import supervision as sv  # noqa: E402

from src import ingestion, timing, gateway, camera_store  # noqa: E402
from src.tracker import Tracker  # noqa: E402


# ── fakes ──────────────────────────────────────────────────────────────────

class _ScriptedCapture:
    """cv2.VideoCapture stand-in driven by a shared script so a test can make
    the "feed" die and come back. Each opened instance serves `frames_per_conn`
    frames with PTS spaced `pts_step_ms`, then fails."""

    script = {}
    opened_with = []

    def __init__(self, uri, *args):
        type(self).opened_with.append((uri, args))
        sc = type(self).script
        sc["opens"] = sc.get("opens", 0) + 1
        if sc["opens"] == 1:
            self._ok = not sc.get("fail_first", False)
        elif sc.get("fail_reopens", 0) > 0:
            sc["fail_reopens"] -= 1   # gateway still restarting
            self._ok = False
        else:
            self._ok = True
        self._n = 0
        self._shape = sc.get("shape", (48, 64, 3))

    def isOpened(self):
        return self._ok

    def read(self):
        sc = type(self).script
        if self._n >= sc.get("frames_per_conn", 10**9):
            return False, None
        self._n += 1
        return True, np.full(self._shape, (self._n * 9) % 255, dtype=np.uint8)

    def get(self, prop):
        if prop == cv2.CAP_PROP_POS_MSEC:
            return 1000.0 + self._n * type(self).script.get("pts_step_ms", 40.0)
        if prop == cv2.CAP_PROP_FOURCC:
            return float(cv2.VideoWriter_fourcc(*type(self).script.get("fourcc", "h264")))
        if prop == cv2.CAP_PROP_FPS:
            return 90000.0  # a deliberately absurd "declared" rate
        return 0.0

    def release(self):
        pass


class _patched_capture:
    def __init__(self, **script):
        self.script = script

    def __enter__(self):
        _ScriptedCapture.script = dict(self.script)
        _ScriptedCapture.opened_with = []
        self._orig = cv2.VideoCapture
        cv2.VideoCapture = _ScriptedCapture
        return _ScriptedCapture

    def __exit__(self, *exc):
        cv2.VideoCapture = self._orig


def _no_sleep(source, record):
    def _wait(seconds):
        record.append(round(seconds, 3))
        return source._stop.is_set()
    source._wait = _wait


def _make_hls_fixture(n_segments=4, seg_s=2.0, fps=25, size=(160, 96)):
    """A real encrypted HLS stream, like the portal's: MPEG-TS chunks with
    continuous timestamps, AES-128 encrypted, plus the key and a VOD playlist."""
    import io as _io
    from fractions import Fraction
    import av
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key, iv = os.urandom(16), os.urandom(16)
    per = int(seg_s * fps)
    files = {"/cam04/enc.key": key}
    for s in range(n_segments):
        buf = _io.BytesIO()
        out = av.open(buf, "w", format="mpegts")
        st = out.add_stream("mpeg2video", rate=fps)
        st.width, st.height, st.pix_fmt = size[0], size[1], "yuv420p"
        st.codec_context.time_base = Fraction(1, fps)
        st.codec_context.gop_size = per  # each chunk starts on a keyframe
        for i in range(per):
            img = np.full((size[1], size[0], 3), (s * 60 + i) % 255, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(img, format="bgr24")
            frame.pts = s * per + i
            for pkt in st.encode(frame):
                out.mux(pkt)
        for pkt in st.encode():
            out.mux(pkt)
        out.close()
        data = buf.getvalue()
        pad = 16 - len(data) % 16
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        files[f"/cam04/seg{s:05d}.ts"] = enc.update(data + bytes([pad]) * pad) + enc.finalize()
    lines = ["#EXTM3U", "#EXT-X-VERSION:6", f"#EXT-X-TARGETDURATION:{int(seg_s)}", "#EXT-X-MEDIA-SEQUENCE:0",
             "#EXT-X-PLAYLIST-TYPE:VOD", f'#EXT-X-KEY:METHOD=AES-128,URI="enc.key",IV=0x{iv.hex()}']
    for s in range(n_segments):
        lines += [f"#EXTINF:{seg_s:.6f},", f"seg{s:05d}.ts"]
    files["/cam04/index.m3u8"] = ("\n".join(lines + ["#EXT-X-ENDLIST"]) + "\n").encode()
    return files


def _serve_hls(files, require_cookie=None, delays=None, bytes_per_s=None):
    """Local HTTP server for an HLS fixture. `require_cookie` -> 403 without it;
    `delays` = {path: seconds} to simulate a slow gateway."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading as _threading
    seen = {"cookies": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen["cookies"].append(self.headers.get("Cookie"))
            if require_cookie and self.headers.get("Cookie") != require_cookie:
                self.send_response(403); self.end_headers(); self.wfile.write(b"forbidden"); return
            body = files.get(self.path)
            if body is None:
                self.send_response(404); self.end_headers(); return
            time.sleep((delays or {}).get(self.path, 0))
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not bytes_per_s or not self.path.endswith(".ts"):
                self.wfile.write(body)
                return
            try:
                for i in range(0, len(body), 4096):  # a slow gateway, byte by byte
                    self.wfile.write(body[i:i + 4096])
                    self.wfile.flush()
                    time.sleep(4096 / bytes_per_s)
            except OSError:
                pass  # the client stopped reading (test finished)

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    server.seen = seen
    _threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


# ── 1. transport ───────────────────────────────────────────────────────────

def test_rtsp_forced_over_tcp_and_hls_dispatch():
    opts = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
    assert "rtsp_transport;tcp" in opts, f"RTSP not forced to TCP: {opts!r}"
    with _patched_capture():
        src = ingestion.VideoSource("rtsp://10.1.1.1:8554/stream/cam04", name="t",
                                    latest_frame_only=False)
        uri, args = _ScriptedCapture.opened_with[0]
        assert args and args[0] == cv2.CAP_FFMPEG, "network stream must use the FFmpeg backend"
        src.release()
    assert ingestion.classify_source("https://cctv.corp8.cloud/cam04/index.m3u8") == "hls"
    assert ingestion.classify_source("http://cam.local/mjpeg") == "mjpeg"
    server, base = _serve_hls(_make_hls_fixture(n_segments=1))
    try:
        src = ingestion.open_source(f"{base}/cam04/index.m3u8", name="hls")
        assert isinstance(src, ingestion.HlsBufferedSource) and src.is_stream
        src.release()
    finally:
        server.shutdown()
    print("  [PASS] RTSP forced over TCP via FFmpeg; HLS goes to the buffered HLS reader, not the MJPEG parser")


def test_gateway_credentials_injected_not_stored():
    os.environ["IBVAP_GATEWAY_EMAIL"] = "alice@example.com"
    os.environ["IBVAP_GATEWAY_PASSWORD"] = "p@ss:word"
    try:
        out = ingestion.inject_gateway_credentials("rtsp://103.250.160.189:8554/stream/cam04")
        assert out == "rtsp://alice%40example.com:p%40ss%3Aword@103.250.160.189:8554/stream/cam04", out
        # other hosts never receive gateway credentials
        assert ingestion.inject_gateway_credentials("rtsp://10.0.0.5/live") == "rtsp://10.0.0.5/live"
        # existing credentials are left alone
        pre = "rtsp://bob:x@103.250.160.189:8554/stream/cam01"
        assert ingestion.inject_gateway_credentials(pre) == pre
        assert "alice" not in ingestion.redact_uri(out) and "p%40ss" not in ingestion.redact_uri(out)
    finally:
        os.environ.pop("IBVAP_GATEWAY_EMAIL")
        os.environ.pop("IBVAP_GATEWAY_PASSWORD")
    print("  [PASS] gateway credentials injected at open time (%40-encoded), redacted in logs")


# ── 2. timing ──────────────────────────────────────────────────────────────

def test_pts_clock_ignores_arrival_time():
    clk = ingestion.PtsClock()
    # GOP replay burst: 25 frames 40 ms apart in PTS, all arriving at once.
    for i in range(25):
        assert not clk.update(1000 + i * 40, now_mono=100.0, now_epoch=5000.0) or i == 0
    assert abs(clk.pts_s - 24 * 0.040) < 1e-6, f"burst timing followed arrival: {clk.pts_s}"
    # ts_epoch follows PTS spacing, anchored once.
    assert abs(clk.ts_epoch - (5000.0 + 0.96)) < 1e-6
    # A 3 s inter-frame gap is normal: no discontinuity, time advances 3 s.
    before = clk.pts_s
    assert clk.update(1000 + 24 * 40 + 3000, now_mono=103.0) is False
    assert abs(clk.pts_s - before - 3.0) < 1e-6
    # PTS going backwards = discontinuity, but media time stays monotonic.
    before = clk.pts_s
    assert clk.update(500, now_mono=104.0) is True
    assert clk.pts_s > before
    # A huge forward jump = discontinuity too.
    assert clk.update(500 + 60_000, now_mono=105.0) is True
    # Reconnect = discontinuity.
    assert clk.update(80, new_connection=True) is True

    # A source that never provides PTS falls back to the monotonic clock
    # without a spurious discontinuity.
    clk2 = ingestion.PtsClock()
    discs = [clk2.update(0, now_mono=10.0 + i * 0.1) for i in range(6)]
    assert clk2.pts_source == "arrival-fallback" and not any(discs[1:]), discs
    print("  [PASS] PTS clock: GOP burst, gaps, backward/forward jumps, reconnect, no-PTS fallback")


def test_media_index_is_pts_driven():
    mi = timing.MediaIndex(15)
    # 20 s of stream delivered unevenly (bursts + stalls): ~every 3rd frame.
    pts = [i * 0.04 for i in range(0, 500, 3)]
    idxs = [mi.index(p) for p in pts]
    assert all(b > a for a, b in zip(idxs, idxs[1:])), "index must strictly increase"
    span_s = (idxs[-1] - idxs[0]) / 15.0
    assert abs(span_s - (pts[-1] - pts[0])) < 0.1, f"dwell span {span_s:.2f}s != PTS span"
    # Duplicate PTS still yields a strictly increasing index.
    assert mi.index(pts[-1]) > idxs[-1]
    print(f"  [PASS] media index spans {span_s:.2f}s for {pts[-1]-pts[0]:.2f}s of PTS "
          f"({len(pts)} frames delivered) — CAP_PROP_FPS never consulted")


def test_declared_fps_not_used_for_timing():
    with _patched_capture(pts_step_ms=40.0):
        src = ingestion.VideoSource("rtsp://10.1.1.1/x", name="fps", latest_frame_only=False)
        gen = src.frames()
        for _ in range(30):
            next(gen)
        assert 20 < src.fps < 30, f"fps should be PTS-measured (~25), got {src.fps}"
        assert abs(src.last_pts_s - 29 * 0.040) < 1e-6
        src.release()
    print("  [PASS] declared CAP_PROP_FPS (90000) ignored; rate measured from PTS (~25)")


# ── 3/4/5. gaps, reconnect, backoff ────────────────────────────────────────

def test_backoff_schedule():
    delays = ingestion.backoff_delays(2.0, 30.0, jitter=0)
    got = [next(delays) for _ in range(7)]
    assert got == [2, 4, 8, 16, 30, 30, 30], got
    print(f"  [PASS] backoff schedule {got}")


def test_reconnect_with_backoff_after_feed_restart():
    # Feed serves 5 frames per connection, then dies; the first 3 reopen
    # attempts fail (gateway restarting), then it comes back.
    with _patched_capture(frames_per_conn=5, fail_reopens=3):
        src = ingestion.VideoSource("rtsp://10.1.1.1/cam", name="restart",
                                    latest_frame_only=False, backoff_jitter=0)
        waits = []
        _no_sleep(src, waits)
        gen = src.frames()
        discs = []
        for _ in range(9):
            next(gen)
            discs.append(src.last_discontinuity)
        assert waits == [2.0, 4.0, 8.0, 16.0], f"backoff not exponential: {waits}"
        st = src.stats()
        assert st["reconnects"] == 1 and st["read_failures"] == 1, st
        assert discs[5] is True and not any(discs[:5]) and not any(discs[6:]), discs
        # Healthy again -> the next outage starts from ~2 s, not 30 s.
        for _ in range(3):
            next(gen)
        assert waits[-1] == 2.0, f"backoff did not reset after recovery: {waits}"
        src.release()
    print(f"  [PASS] feed restart: reconnected after backoff {waits[:4]}, flagged discontinuity, "
          "backoff reset after recovery")


def test_initial_connect_failure_is_retried():
    with _patched_capture(fail_first=True):
        src = ingestion.VideoSource("rtsp://10.1.1.1/cam", name="late",
                                    latest_frame_only=False, backoff_jitter=0)
        waits = []
        _no_sleep(src, waits)
        _idx, frame = next(src.frames())
        assert frame is not None and waits == [2.0], waits
        src.release()
    print("  [PASS] failing initial connect doesn't kill the camera; retried with backoff")


def test_latest_frame_reader_and_stop():
    with _patched_capture(pts_step_ms=40.0):
        src = ingestion.VideoSource("rtsp://10.1.1.1/cam", name="lat")
        gen = src.frames()
        next(gen)
        time.sleep(0.2)              # slow consumer: the reader keeps decoding
        next(gen)
        st = src.stats()
        assert st["frames_dropped"] > 0, "stale frames should be dropped, not queued"
        t0 = time.time()
        src.release()                # must not hang
        assert time.time() - t0 < 2.0
        assert src._reader is not None and not src._reader.is_alive()
    print(f"  [PASS] slow consumer gets the newest frame ({st['frames_dropped']} stale dropped); "
          "stop/release is prompt")


def test_hls_plays_at_real_time_speed():
    """Portal HLS is a recording served as files: frames must be released at
    PTS rate (not fast-forwarded), and a stall must not trigger catch-up."""
    with _patched_capture(pts_step_ms=40.0):
        src = ingestion.VideoSource("https://cctv.example/cam04/index.m3u8", name="hls",
                                    latest_frame_only=False)
        assert src.pace_realtime
        gen = src.frames()
        next(gen)
        t0 = time.monotonic()
        for _ in range(25):  # 25 frames x 40 ms of PTS = 1.0 s of video
            next(gen)
        elapsed = time.monotonic() - t0
        src.release()
    assert 0.85 <= elapsed <= 1.5, f"HLS not paced to real time: 1 s of video took {elapsed:.2f}s"
    with _patched_capture():
        rtsp = ingestion.VideoSource("rtsp://10.1.1.1/cam", name="rtsp", latest_frame_only=False)
        assert not rtsp.pace_realtime, "RTSP is already real-time; it must not be paced"
        rtsp.release()
    print(f"  [PASS] HLS paced to real time (1.0 s of video delivered in {elapsed:.2f}s)")


def test_hls_buffer_ahead_rides_through_slow_chunk():
    """The download-ahead buffer: chunk 3 takes 3 s to arrive for 2 s of video.
    Fetched on demand that's a visible stall; buffered ahead it plays through.
    Also: portal cookie sent only to allowed hosts, AES-128 decrypted, PTS
    real-time pacing, and the recording loops with a discontinuity flag."""
    files = _make_hls_fixture(n_segments=4, seg_s=2.0)
    cookie = "sentinel=test-session"
    server, base = _serve_hls(files, require_cookie=cookie, delays={"/cam04/seg00002.ts": 3.0})
    url = f"{base}/cam04/index.m3u8"
    old = {k: os.environ.get(k) for k in ("IBVAP_HLS_COOKIE", "IBVAP_HLS_COOKIE_HOSTS")}
    os.environ["IBVAP_HLS_COOKIE"] = cookie
    try:
        # Cookie scoping: not an allowed host -> never sent -> server refuses.
        os.environ["IBVAP_HLS_COOKIE_HOSTS"] = "cctv.corp8.cloud"
        denied = ingestion.HlsBufferedSource(url, name="scoped", max_reconnect_attempts=0)
        assert denied._playlist is None and "403" in (denied.stats()["last_error"] or "")
        denied.release()
        assert all(c is None for c in server.seen["cookies"]), "cookie leaked to a non-allowed host"

        os.environ["IBVAP_HLS_COOKIE_HOSTS"] = "127.0.0.1"
        src = ingestion.HlsBufferedSource(url, name="buffered", latest_frame_only=False,
                                          start_buffer_s=2.0, max_buffer_s=60.0)
        gen = src.frames()
        next(gen)
        t0 = time.monotonic()
        pts, disc = [src.last_pts_s], []
        for _ in range(199):  # the rest of the 8 s recording (4 x 50 frames)
            next(gen)
            pts.append(src.last_pts_s)
        elapsed = time.monotonic() - t0
        st = src.stats()
        assert st["stalls"] == 0, f"buffer should ride through the slow chunk, got {st['stalls']} stalls"
        assert 7.0 <= elapsed <= 9.5, f"8 s of video should play in ~8 s, took {elapsed:.1f}s"
        assert all(b > a for a, b in zip(pts, pts[1:])), "PTS must increase across chunks"
        assert st["codec"] and st["width"] == 160 and st["segments_downloaded"] >= 4
        for _ in range(10):  # the feed loops back to chunk 0 -> discontinuity
            next(gen)
            disc.append(src.last_discontinuity)
        assert any(disc), "loop point must be flagged as a discontinuity"
        src.release()
    finally:
        server.shutdown()
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print(f"  [PASS] HLS buffer-ahead: slow chunk (3 s for 2 s of video) played with 0 stalls, "
          f"8 s of video in {elapsed:.1f}s, encrypted chunks decoded, cookie host-scoped, loop flagged")


def test_hls_frames_arrive_while_chunk_downloads():
    """A slow 1080p-style chunk must not mean staring at 'Buffering' until the
    whole file is in: frames are decoded and shown while it downloads."""
    files = _make_hls_fixture(n_segments=1, seg_s=6.0, size=(640, 360))
    size = len(files["/cam04/seg00000.ts"])
    rate = size / 6.0  # the whole 6 s chunk takes ~6 s to arrive
    server, base = _serve_hls(files, bytes_per_s=rate)
    try:
        t0 = time.monotonic()
        src = ingestion.HlsBufferedSource(f"{base}/cam04/index.m3u8", name="slow", latest_frame_only=False)
        gen = src.frames()
        next(gen)
        first = time.monotonic() - t0
        n = 1
        while time.monotonic() - t0 < 3.0:
            next(gen)
            n += 1
        src.release()
    finally:
        server.shutdown()
    assert first < 2.0, f"first frame should appear while the chunk downloads, took {first:.1f}s"
    assert n >= 25, f"frames should keep flowing during the download, got {n} in 3 s"
    print(f"  [PASS] progressive HLS: first frame after {first:.1f}s of a 6 s download, {n} frames in the first 3 s")


def test_process_ahead_playout():
    """AI analyses buffered frames ahead; playout shows them at the video's
    own rate, fires each alert with its frame, and holds the AI back once it
    is max_ahead_s ahead (so the GPU isn't pushed harder than playback)."""
    from src.playout import PlayoutBuffer

    shown, fired = [], []
    po = PlayoutBuffer(lambda jpeg: shown.append((time.monotonic(), jpeg)),
                       start_delay_s=0.4, max_ahead_s=0.8)
    t_push0 = time.monotonic()
    for i in range(50):  # 2 s of 25 fps video, "analysed" instantly
        item = po.push(i * 0.04, i)
        if i == 30:
            po.defer(item, lambda: fired.append((time.monotonic(), len(shown))))
    push_s = time.monotonic() - t_push0
    time.sleep(1.2)
    po.close()

    assert [j for _, j in shown] == list(range(50)), "every analysed frame must be shown, in order"
    play_span = shown[-1][0] - shown[0][0]
    assert 1.8 <= play_span <= 2.3, f"2 s of video should play in ~2 s, took {play_span:.2f}s"
    assert push_s >= 0.9, f"AI should be held back when far ahead (pushed 2 s of video in {push_s:.2f}s)"
    assert fired and fired[0][1] == 30, "alert must fire when ITS frame is shown, not when analysed"
    print(f"  [PASS] process-ahead playout: 2 s of analysed video shown in {play_span:.2f}s, "
          f"alert fired with its frame, AI held at most 0.8 s ahead")


def test_mixed_codecs_and_resolutions_same_contract():
    shapes = {}
    for fourcc, shape in (("h264", (1080, 1920, 3)), ("hevc", (576, 704, 3))):
        with _patched_capture(fourcc=fourcc, shape=shape):
            src = ingestion.VideoSource(f"rtsp://10.1.1.1/{fourcc}", name=fourcc, latest_frame_only=False)
            _idx, frame = next(src.frames())
            st = src.stats()
            shapes[st["codec"]] = (st["width"], st["height"])
            assert frame.shape == shape
            src.release()
    assert shapes == {"h264": (1920, 1080), "hevc": (704, 576)}, shapes
    print(f"  [PASS] mixed H.264/H.265 + resolutions reported per camera: {shapes}")


# ── 6. catalogue ───────────────────────────────────────────────────────────

_CATALOG = {"cameras": [
    {"id": "cam01", "name": "CAM01", "location": "Chiman bhai Bridge", "status": "live",
     "codec": "h264", "width": 1920, "height": 1080, "fps": 25,
     "urls": {"rtsp": "rtsp://leak:secret@103.250.160.189:8554/stream/cam01",
              "hls": "https://cctv.corp8.cloud/cam01/index.m3u8",
              "webrtc": "http://103.250.160.189:8889/stream/cam01/whep"}},
    {"id": "cam02", "name": "CAM02", "location": "Janpath", "live": True,
     "stream": {"codec": "h265", "resolution": "704x576"}},
    {"id": "cam03", "location": "O.N.G.C. Office", "status": "live"},
    {"id": "cam04", "location": "Paldi Circle", "status": "offline"},
    {"id": "cam05", "location": "Visat teen Rasta", "status": "live"},
    {"id": "cam06", "location": "Timbavadi gate-Junagadh", "status": "live"},
]}


def test_catalog_parse_and_registry_sync():
    tmp = tempfile.mkdtemp()
    camera_store._DB_PATH = os.path.join(tmp, "history.db")
    camera_store.init_db()
    for k in ("IBVAP_GATEWAY_ACTIVE", "IBVAP_GATEWAY_TRANSPORT"):
        os.environ.pop(k, None)
    os.environ["IBVAP_GATEWAY_MAX_ACTIVE"] = "3"

    entries = gateway.parse_catalog(_CATALOG)
    by = {e["id"]: e for e in entries}
    assert by["cam02"]["codec"] == "h265" and by["cam02"]["width"] == 704
    assert by["cam04"]["live"] is False
    assert "secret" not in json.dumps(entries), "credentials from the catalogue must be stripped"

    res = gateway.sync_registry(entries)
    assert sorted(res["added"]) == [f"cam0{i}" for i in range(1, 7)]
    rows = {c["id"]: c for c in camera_store.list_cameras()}
    enabled = sorted(i for i, c in rows.items() if c["enabled"])
    assert enabled == ["cam01", "cam02", "cam03"], f"load not paced: {enabled}"
    assert rows["cam04"]["status"] == "down" and not rows["cam04"]["enabled"]
    assert rows["cam01"]["source_spec"] == "rtsp://103.250.160.189:8554/stream/cam01"
    assert rows["cam03"]["source_spec"] == "rtsp://103.250.160.189:8554/stream/cam03"  # built from base
    assert rows["cam01"]["name"] == "CAM01 — Chiman bhai Bridge"
    assert rows["cam02"]["camera_type"] == "rtsp"

    # Explicit selection + HLS transport; camera removed from the catalogue.
    os.environ["IBVAP_GATEWAY_ACTIVE"] = "cam05,cam06"
    os.environ["IBVAP_GATEWAY_TRANSPORT"] = "hls"
    try:
        res2 = gateway.sync_registry(gateway.parse_catalog(
            {"cameras": [c for c in _CATALOG["cameras"] if c["id"] != "cam03"]}), apply_active=True)
    finally:
        os.environ.pop("IBVAP_GATEWAY_ACTIVE")
        os.environ.pop("IBVAP_GATEWAY_TRANSPORT")
        os.environ.pop("IBVAP_GATEWAY_MAX_ACTIVE")
    rows = {c["id"]: c for c in camera_store.list_cameras()}
    assert res2["removed"] == ["cam03"] and not rows["cam03"]["enabled"]
    assert sorted(i for i, c in rows.items() if c["enabled"]) == ["cam05", "cam06"]
    assert rows["cam01"]["source_spec"] == "https://cctv.corp8.cloud/cam01/index.m3u8"
    assert rows["cam01"]["camera_type"] == "hls"
    print("  [PASS] catalogue parsed (both schema flavours), credentials stripped, load paced, "
          "removed cameras disabled, RTSP/HLS selectable")


def test_gateway_cameras_get_map_positions():
    """The gateway catalogue has names but no coordinates: known cameras are
    placed from camera_locations.json, unmatched names stay off the map, and
    a position an operator sets in the Registry survives every re-sync."""
    tmp = tempfile.mkdtemp()
    camera_store._DB_PATH = os.path.join(tmp, "history.db")
    camera_store.init_db()
    cat = {"cameras": [{"id": "cam04", "name": "04 Paldi Circle"},
                       {"id": "cam05", "name": "05 Visat teen Rasta"},
                       {"id": "cam12", "name": "12 Some Renamed Camera"},   # id known, name doesn't match
                       {"id": "cam20", "name": "20 Mohanpura"}]}           # deliberately not placed
    gateway.sync_registry(gateway.parse_catalog(cat))
    rows = {c["id"]: c for c in camera_store.list_cameras()}
    assert (rows["cam04"]["lat"], rows["cam04"]["lon"]) == (23.0125, 72.5625)
    assert "Map position: Paldi Circle" in rows["cam04"]["storage_details"]
    assert rows["cam12"]["lat"] is None, "a renamed camera must not inherit another place's position"
    assert rows["cam20"]["lat"] is None

    camera_store.update_camera("cam05", {"lat": 23.1111, "lon": 72.6000})  # operator fixes it
    gateway.sync_registry(gateway.parse_catalog(cat))
    gateway.sync_registry(gateway.parse_catalog(cat))
    rows = {c["id"]: c for c in camera_store.list_cameras()}
    assert (rows["cam05"]["lat"], rows["cam05"]["lon"]) == (23.1111, 72.6000), "operator position overwritten"
    assert "Map position: Paldi Circle" in rows["cam04"]["storage_details"], "note must survive re-sync"
    print("  [PASS] gateway cameras placed on the map by name; unknown/renamed ones left off; "
          "operator positions kept across re-syncs")


def test_catalog_login_redirect_is_explained():
    try:
        gateway.parse_catalog("<html>login</html>")
    except ValueError:
        pass
    else:
        raise AssertionError("non-JSON catalogue must be rejected")
    print("  [PASS] non-JSON / login-page catalogue rejected with a clear error")


# ── 8. scene discontinuity ─────────────────────────────────────────────────

def test_scene_cut_detector():
    rng = np.random.default_rng(0)
    scene_a = rng.integers(0, 90, (360, 640, 3), dtype=np.uint8)
    scene_b = rng.integers(150, 255, (360, 640, 3), dtype=np.uint8)
    det = timing.SceneCutDetector()
    cuts = []
    for i in range(20):
        frame = scene_a.copy()
        frame[100:160, i * 10:i * 10 + 60] = 255   # a moving object is not a cut
        cuts.append(det.update(frame, i * 0.04))
    assert not any(cuts), "object motion flagged as a cut"
    assert det.update(scene_b, 0.84) is True, "loop-point hard cut not detected"
    assert det.update(scene_b, 0.88) is False
    print("  [PASS] loop-point hard cut detected; ordinary motion is not")


def test_tracker_reset_never_reuses_ids():
    trk = Tracker(frame_rate=15, enable_reid=False)

    def dets(x):
        return sv.Detections(xyxy=np.array([[x, 50, x + 40, 130]], dtype=float),
                             confidence=np.array([0.9]), class_id=np.array([0]))

    ids_before = set()
    for i in range(5):
        t = trk.update(dets(100 + i * 2), i)
        ids_before.update(int(x) for x in t.tracker_id)
    trk.reset()
    assert not trk.history, "per-track history survived the cut"
    ids_after = set()
    for i in range(5, 10):
        t = trk.update(dets(100 + i * 2), i)
        ids_after.update(int(x) for x in t.tracker_id)
    assert ids_before and ids_after and not (ids_before & ids_after), (ids_before, ids_after)
    print(f"  [PASS] tracker reset at the cut: ids {sorted(ids_before)} -> {sorted(ids_after)}, none reused")


def test_pipeline_resets_scene_state_on_discontinuity():
    from src.pipeline import Pipeline
    from src import tamper as tamper_mod

    class _Src:
        last_pts_s = 1.0
        last_ts_epoch = 1000.0
        last_discontinuity = False

    p = Pipeline.__new__(Pipeline)  # no detector/weights needed for this path
    p.source = _Src()
    p.source_name = "camX"
    p.track_fps = 15.0
    p.tracker = Tracker(frame_rate=15, enable_reid=False)
    p.media_index = timing.MediaIndex(15.0)
    p.scene_cut = timing.SceneCutDetector()
    p._plate_cache, p._plate_read_frame, p._sighting_frame = {1: "GJ01AB1234"}, {1: 3}, {1: 3}
    p._alerted_watchlist = {(1, "GJ01AB1234")}
    p.behavior = object()
    p.tamper = tamper_mod.TamperDetector()
    p.scene_resets, p.last_scene_reset = 0, None
    frame = np.zeros((72, 128, 3), dtype=np.uint8)

    v1 = p._frame_timing(frame)
    assert p.scene_resets == 0 and p.frame_ts_epoch == 1000.0
    p.source.last_pts_s, p.source.last_discontinuity = 1.5, True
    v2 = p._frame_timing(frame)
    assert p.scene_resets == 1 and v2 > v1
    assert not p._plate_cache and not p._alerted_watchlist and p.behavior is None
    assert "discontinuity" in p.last_scene_reset["reason"]
    print("  [PASS] pipeline drops tracks/plates/dwell/tamper baseline on a discontinuity; "
          "media index keeps increasing")


def test_api_startup_mirrors_catalogue():
    """End-to-end: boot the API against a catalogue file; the registry mirrors
    it (no demo seeds), only the selected cameras get a worker, and the admin
    sync endpoint follows catalogue changes without a restart."""
    os.environ["IBVAP_GATEWAY_RESYNC_INTERVAL_S"] = "0"
    from fastapi.testclient import TestClient
    from src import api_server, history_store, auth_store, audit_store

    class _FakePipeline:
        def __init__(self, **kwargs):
            pass

        def stream(self, on_frame, stop_flag=None, **_kw):
            while not (stop_flag and stop_flag()):
                on_frame(np.zeros((36, 64, 3), dtype=np.uint8))
                time.sleep(0.02)

    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "history.db")
    for mod in (history_store, camera_store, auth_store, audit_store):
        mod._DB_PATH = db
    history_store._THUMB_DIR = os.path.join(tmp, "thumbs")
    cat_path = os.path.join(tmp, "cameras.json")
    with open(cat_path, "w", encoding="utf-8") as fh:
        json.dump(_CATALOG, fh)
    os.environ["IBVAP_GATEWAY_CATALOG"] = cat_path
    os.environ["IBVAP_GATEWAY_ACTIVE"] = "cam01,cam02"
    api_server.Pipeline = _FakePipeline
    api_server.GATEWAY_RESYNC_INTERVAL_S = 0
    try:
        with TestClient(api_server.app) as client:
            tok = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()["token"]
            client.headers.update({"Authorization": f"Bearer {tok}"})
            cams = {c["id"]: c for c in client.get("/api/cameras").json()["cameras"]}
            assert set(cams) == {f"cam0{i}" for i in range(1, 7)}, sorted(cams)
            streaming = sorted(i for i, c in cams.items() if c["streaming"])
            assert streaming == ["cam01", "cam02"], f"load not paced: {streaming}"
            assert "secret" not in json.dumps(cams)

            cat = client.get("/api/gateway/catalog").json()
            assert cat["configured"] and cat["error"] is None and len(cat["cameras"]) == 6

            # Catalogue changes: cam02 disappears, cam05 is selected.
            with open(cat_path, "w", encoding="utf-8") as fh:
                json.dump({"cameras": [c for c in _CATALOG["cameras"] if c["id"] != "cam02"]}, fh)
            os.environ["IBVAP_GATEWAY_ACTIVE"] = "cam01,cam05"
            r = client.post("/api/gateway/sync", json={"apply_active": True})
            assert r.status_code == 200, r.text
            assert r.json()["removed"] == ["cam02"]
            time.sleep(0.1)
            running = sorted(i for i, w in api_server._cameras.items() if w.is_alive())
            assert running == ["cam01", "cam05"], running
    finally:
        for k in ("IBVAP_GATEWAY_CATALOG", "IBVAP_GATEWAY_ACTIVE"):
            os.environ.pop(k, None)
    print("  [PASS] API boots from the catalogue, streams only selected cameras, "
          "and /api/gateway/sync follows catalogue changes live")


def test_on_demand_live_and_snapshots():
    """IBVAP_MAX_LIVE=1: only one camera streams; Load switches it and the
    previous one drops back to standby; snapshots serve the rest."""
    from fastapi.testclient import TestClient
    from src import api_server, history_store, auth_store, audit_store
    from src.snapshots import SnapshotService

    class _FakePipeline:
        def __init__(self, **kwargs):
            pass

        def stream(self, on_frame, stop_flag=None, **_kw):
            while not (stop_flag and stop_flag()):
                on_frame(np.zeros((36, 64, 3), dtype=np.uint8))
                time.sleep(0.02)

    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "history.db")
    for mod in (history_store, camera_store, auth_store, audit_store):
        mod._DB_PATH = db
    history_store._THUMB_DIR = os.path.join(tmp, "thumbs")
    cat_path = os.path.join(tmp, "cameras.json")
    with open(cat_path, "w", encoding="utf-8") as fh:
        json.dump(_CATALOG, fh)
    os.environ["IBVAP_GATEWAY_CATALOG"] = cat_path
    os.environ["IBVAP_GATEWAY_ACTIVE"] = "cam01,cam02,cam05"
    api_server.Pipeline = _FakePipeline
    api_server.GATEWAY_RESYNC_INTERVAL_S = 0
    api_server.MAX_LIVE = 1
    api_server.IDLE_STOP_S, api_server.IDLE_CHECK_S = 1.0, 0.2
    snaps = SnapshotService(lambda: [], interval_s=3600)
    snaps._images["cam02"] = (b"\xff\xd8fake\xff\xd9", time.time() - 12)
    api_server._snapshots = snaps
    try:
        with TestClient(api_server.app) as client:
            tok = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()["token"]
            client.headers.update({"Authorization": f"Bearer {tok}"})
            conn = {c["id"]: c["connectivity"] for c in client.get("/api/cameras").json()["cameras"]}
            assert not [i for i, c in conn.items() if c == "online"], f"nothing may start at boot: {conn}"
            assert conn["cam02"] == "standby" and conn["cam05"] == "standby", conn

            r = client.get("/api/snapshot/cam02")
            assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
            assert float(r.headers["X-Snapshot-Age"]) >= 12
            assert client.get("/api/snapshot/cam05").status_code == 404  # none taken yet

            assert client.post("/api/live/cam01").json()["connectivity"] == "online"
            r = client.post("/api/live/cam02")
            assert r.status_code == 200 and r.json()["connectivity"] == "online", r.text
            time.sleep(0.1)
            conn = {c["id"]: c["connectivity"] for c in client.get("/api/cameras").json()["cameras"]}
            assert conn["cam02"] == "online" and conn["cam01"] == "standby", conn
            assert sorted(api_server._cameras) == ["cam02"]
            r = client.get("/api/snapshot/cam02")  # live camera -> its current frame
            assert r.status_code == 200 and r.headers["X-Snapshot-Age"] == "0.0"

            # Nobody opens its stream -> stopped after IDLE_STOP_S (watch time saved).
            time.sleep(2.0)
            assert "cam02" not in api_server._cameras, "unwatched camera should be stopped"
            conn = {c["id"]: c["connectivity"] for c in client.get("/api/cameras").json()["cameras"]}
            assert conn["cam02"] == "standby", conn
    finally:
        api_server.MAX_LIVE = 0
        api_server._snapshots = None
        api_server.IDLE_STOP_S, api_server.IDLE_CHECK_S = 60.0, 5.0
        for k in ("IBVAP_GATEWAY_CATALOG", "IBVAP_GATEWAY_ACTIVE"):
            os.environ.pop(k, None)
    print("  [PASS] on-demand mode: nothing starts at boot, Load switches the live camera, unwatched camera stopped")


if __name__ == "__main__":
    print("Running Phase 9 live-gateway readiness tests…")
    test_rtsp_forced_over_tcp_and_hls_dispatch()
    test_gateway_credentials_injected_not_stored()
    test_pts_clock_ignores_arrival_time()
    test_media_index_is_pts_driven()
    test_declared_fps_not_used_for_timing()
    test_backoff_schedule()
    test_reconnect_with_backoff_after_feed_restart()
    test_initial_connect_failure_is_retried()
    test_latest_frame_reader_and_stop()
    test_hls_plays_at_real_time_speed()
    test_hls_buffer_ahead_rides_through_slow_chunk()
    test_hls_frames_arrive_while_chunk_downloads()
    test_process_ahead_playout()
    test_mixed_codecs_and_resolutions_same_contract()
    test_catalog_parse_and_registry_sync()
    test_gateway_cameras_get_map_positions()
    test_catalog_login_redirect_is_explained()
    test_scene_cut_detector()
    test_tracker_reset_never_reuses_ids()
    test_pipeline_resets_scene_state_on_discontinuity()
    test_api_startup_mirrors_catalogue()
    test_on_demand_live_and_snapshots()
    print("\nAll Phase 9 tests passed.")
