"""
Video ingestion layer — heterogeneous camera onboarding.

The pipeline downstream (detector -> tracker -> zones -> anpr) only ever
touches a source through a tiny contract: iterate ``frames()`` for
``(index, BGR-ndarray)`` tuples, read ``fps``, check ``is_stream``, call
``_open()`` to restart a finite source, and ``release()`` at the end. Every
adapter here implements exactly that contract and nothing else, so the same
detection loop runs unchanged regardless of how the pixels arrived. This is
the seam that makes the system forward-compatible with a federation /
edge-node pattern: a remote node only has to speak this frame contract.

Adapters:
  * VideoSource      — OpenCV VideoCapture: local webcam (int index), RTSP
                       IP cameras, and finite local video files/recorded
                       clips. Reconnects live sources; ends cleanly at EOF
                       on files.
  * MjpegHttpSource  — older/analog-via-encoder cameras exposing an HTTP
                       MJPEG (multipart/x-mixed-replace) endpoint, which
                       OpenCV's FFmpeg backend often can't open reliably.
  * ONVIF (resolve_onvif_stream_uri) — NOT a separate video transport: ONVIF
                       is a control layer that we handshake with only to
                       discover the camera's RTSP URI, then hand off to the
                       ordinary RTSP path (VideoSource).

``open_source(spec, name=...)`` is the single factory the pipeline/registry
call; it dispatches on the spec to the right adapter.
"""

import time
from urllib.parse import urlparse, urlunparse

import cv2
import numpy as np


class VideoSource:
    """OpenCV-backed source: webcam (int index), RTSP URL, or local file.
    Iterable frame source with automatic reconnect for live streams."""

    def __init__(self, uri, name="camera", reconnect_delay_s=2.0, max_reconnect_attempts=5):
        self.uri = uri
        self.name = name
        self.reconnect_delay_s = reconnect_delay_s
        self.max_reconnect_attempts = max_reconnect_attempts
        # Anything that isn't a path to a finite local file is treated as a
        # "live" source that should reconnect on failure rather than just
        # stopping: RTSP URLs and webcam device indices both qualify.
        self.is_stream = isinstance(uri, int) or (
            isinstance(uri, str) and uri.lower().startswith("rtsp://")
        )
        self.cap = None
        self._open()

    def _open(self):
        self.cap = cv2.VideoCapture(self.uri)
        if not self.cap.isOpened():
            raise ConnectionError(f"[{self.name}] could not open source: {self.uri}")

    def _reconnect(self):
        for attempt in range(1, self.max_reconnect_attempts + 1):
            print(f"[{self.name}] reconnect attempt {attempt}/{self.max_reconnect_attempts}...")
            time.sleep(self.reconnect_delay_s)
            self.cap.release()
            self.cap = cv2.VideoCapture(self.uri)
            if self.cap.isOpened():
                print(f"[{self.name}] reconnected.")
                return True
        return False

    @property
    def fps(self):
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        return fps if fps and fps > 0 else 25.0

    def frames(self):
        """Yield (frame_index, frame) tuples. Blocks/reconnects on RTSP drop;
        stops cleanly at EOF for file sources."""
        idx = 0
        while True:
            ok, frame = self.cap.read()
            if not ok:
                if self.is_stream:
                    print(f"[{self.name}] stream read failed, attempting reconnect...")
                    if not self._reconnect():
                        print(f"[{self.name}] giving up after {self.max_reconnect_attempts} attempts.")
                        break
                    continue
                else:
                    break  # end of file — normal termination for recorded footage
            yield idx, frame
            idx += 1

    def release(self):
        if self.cap is not None:
            self.cap.release()


class MjpegHttpSource:
    """HTTP MJPEG (multipart/x-mixed-replace) adapter for older/analog
    cameras fronted by an encoder that exposes a motion-JPEG endpoint.

    Deliberately does its own multipart parsing with ``requests`` rather
    than leaning on cv2.VideoCapture(http://...): OpenCV's FFmpeg backend is
    inconsistent across such endpoints (boundary quirks, no Content-Length,
    digest auth), whereas parsing SOI/EOI markers out of the byte stream
    ourselves is small and dependable. Frames come out as the same BGR
    ndarrays every other adapter yields.

    Credentials embedded in the URL (http://user:pass@host/…) are stripped
    from the request URL and sent as HTTP auth instead of leaking into logs
    or query strings.
    """

    # JPEG start-of-image / end-of-image markers.
    _SOI = b"\xff\xd8"
    _EOI = b"\xff\xd9"

    def __init__(self, uri, name="camera", fps=15.0, reconnect_delay_s=2.0,
                 max_reconnect_attempts=5, chunk_size=8192, timeout=(5, 30)):
        self.name = name
        self.is_stream = True  # a live source: reconnect on drop, don't loop
        self._declared_fps = fps
        self.reconnect_delay_s = reconnect_delay_s
        self.max_reconnect_attempts = max_reconnect_attempts
        self.chunk_size = chunk_size
        self.timeout = timeout

        parsed = urlparse(uri)
        # Split any userinfo out of the netloc so it becomes HTTP auth, not
        # part of the request URL.
        self._auth = None
        netloc = parsed.netloc
        if "@" in netloc:
            userinfo, hostport = netloc.rsplit("@", 1)
            user, _, pwd = userinfo.partition(":")
            self._auth = (user, pwd)
            netloc = hostport
        self.uri = urlunparse(parsed._replace(netloc=netloc))

        self._resp = None
        self._open()

    def _open(self):
        # Lazy import so the whole ingestion module doesn't hard-depend on
        # requests unless an MJPEG source is actually used.
        import requests

        self._session = requests.Session()
        self._resp = self._session.get(
            self.uri, stream=True, timeout=self.timeout, auth=self._auth
        )
        self._resp.raise_for_status()

    def _close_resp(self):
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass
            self._resp = None

    @property
    def fps(self):
        # MJPEG endpoints rarely advertise a reliable frame rate, so we use a
        # sensible declared default (the pipeline only needs it to seed the
        # tracker's frame_rate).
        return self._declared_fps

    def frames(self):
        """Yield (frame_index, BGR-ndarray). Parses JPEGs out of the
        multipart byte stream; reconnects on drop up to the attempt cap."""
        import requests

        idx = 0
        attempts = 0
        buf = bytearray()
        while True:
            try:
                if self._resp is None:
                    self._open()
                for chunk in self._resp.iter_content(chunk_size=self.chunk_size):
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    # Drain every complete JPEG currently in the buffer.
                    while True:
                        start = buf.find(self._SOI)
                        if start == -1:
                            # No frame boundary yet; keep the tail bounded so a
                            # garbage stream can't grow the buffer without limit.
                            if len(buf) > 4_000_000:
                                del buf[:-2]
                            break
                        end = buf.find(self._EOI, start + 2)
                        if end == -1:
                            # Partial frame — drop anything before SOI, wait for more.
                            if start > 0:
                                del buf[:start]
                            break
                        jpg = bytes(buf[start:end + 2])
                        del buf[:end + 2]
                        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                        if img is not None:
                            attempts = 0  # a good frame resets the reconnect budget
                            yield idx, img
                            idx += 1
                # Server closed the stream cleanly.
                self._close_resp()
            except (requests.RequestException, OSError) as exc:
                print(f"[{self.name}] MJPEG read failed: {exc}")
                self._close_resp()

            attempts += 1
            if attempts > self.max_reconnect_attempts:
                print(f"[{self.name}] giving up after {self.max_reconnect_attempts} attempts.")
                break
            print(f"[{self.name}] reconnect attempt {attempts}/{self.max_reconnect_attempts}...")
            time.sleep(self.reconnect_delay_s)

    def release(self):
        self._close_resp()
        session = getattr(self, "_session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def resolve_onvif_stream_uri(host, port=80, username=None, password=None,
                             profile_index=0, inject_credentials=True):
    """Handshake with an ONVIF device and return its RTSP stream URI.

    ONVIF is a control protocol, not a video transport: we use it purely to
    discover the RTSP URI the camera actually streams on, then the caller
    hands that URI to the ordinary RTSP path (VideoSource). The onvif-zeep
    package is imported lazily so the rest of ingestion works without it;
    a clear error is raised if an ONVIF source is used but the package is
    missing.

    Many devices return an RTSP URI without embedded credentials even though
    the RTSP stream itself requires them, so (by default) we inject the same
    user/password into the returned URI's userinfo.
    """
    try:
        from onvif import ONVIFCamera
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch in tests
        raise ImportError(
            "ONVIF support needs the 'onvif-zeep' package. Install it with: "
            "pip install onvif-zeep"
        ) from exc

    camera = ONVIFCamera(host, port, username, password)
    media = camera.create_media_service()
    profiles = media.GetProfiles()
    if not profiles:
        raise ConnectionError(f"ONVIF device {host}:{port} exposed no media profiles.")
    profile = profiles[min(profile_index, len(profiles) - 1)]
    token = profile.token

    request = media.create_type("GetStreamUri")
    request.ProfileToken = token
    request.StreamSetup = {
        "Stream": "RTP-Unicast",
        "Transport": {"Protocol": "RTSP"},
    }
    result = media.GetStreamUri(request)
    uri = result.Uri

    if inject_credentials and username and "@" not in urlparse(uri).netloc:
        uri = _inject_rtsp_credentials(uri, username, password or "")
    return uri


def _inject_rtsp_credentials(rtsp_uri, username, password):
    """Put user:pass@ into an rtsp:// URI's authority (many cameras hand back
    a credential-less URI even when the stream is authenticated)."""
    parsed = urlparse(rtsp_uri)
    netloc = f"{username}:{password}@{parsed.netloc}"
    return urlunparse(parsed._replace(netloc=netloc))


def _parse_onvif_spec(spec):
    """Parse an onvif:// spec into resolver kwargs.

    Form: onvif://[user:pass@]host[:port][?profile=N]
    """
    parsed = urlparse(spec)
    host = parsed.hostname
    if not host:
        raise ValueError(f"Malformed ONVIF spec (no host): {spec!r}")
    port = parsed.port or 80
    profile_index = 0
    if parsed.query:
        from urllib.parse import parse_qs
        q = parse_qs(parsed.query)
        if "profile" in q:
            profile_index = int(q["profile"][0])
    return {
        "host": host,
        "port": port,
        "username": parsed.username,
        "password": parsed.password,
        "profile_index": profile_index,
    }


def classify_source(spec):
    """Return the adapter kind a spec would use — 'webcam' | 'rtsp' | 'onvif'
    | 'mjpeg' | 'file' | 'none' — using the SAME dispatch rules as
    open_source(), so the registry's stored camera_type can't drift from what
    actually gets opened. 'none' means the camera has no live source yet."""
    if isinstance(spec, int):
        return "webcam"
    if spec is None or (isinstance(spec, str) and spec.strip() == ""):
        return "none"
    if isinstance(spec, str) and spec.strip().lstrip("-").isdigit():
        return "webcam"
    scheme = urlparse(spec).scheme.lower() if isinstance(spec, str) else ""
    if scheme == "onvif":
        return "onvif"
    if scheme in ("http", "https"):
        return "mjpeg"
    if scheme == "rtsp":
        return "rtsp"
    return "file"


def open_source(spec, name="camera", **kwargs):
    """Factory: build the right adapter for a source spec and return an
    object satisfying the frame contract (frames/fps/is_stream/_open/release).

    Dispatch:
      * int, or a digit string ("0")        -> webcam via VideoSource
      * "onvif://user:pass@host[:port]"      -> ONVIF handshake -> RTSP VideoSource
      * "http(s)://…"                        -> MjpegHttpSource
      * "rtsp://…"                           -> RTSP VideoSource
      * anything else (a path)               -> file VideoSource
    """
    # Webcam device index, given as int or bare digit string.
    if isinstance(spec, int):
        return VideoSource(spec, name=name, **kwargs)
    if isinstance(spec, str) and spec.strip().lstrip("-").isdigit():
        return VideoSource(int(spec.strip()), name=name, **kwargs)

    scheme = urlparse(spec).scheme.lower() if isinstance(spec, str) else ""

    if scheme == "onvif":
        rtsp_uri = resolve_onvif_stream_uri(**_parse_onvif_spec(spec))
        print(f"[{name}] ONVIF resolved -> {urlparse(rtsp_uri)._replace(netloc='***').geturl()}")
        return VideoSource(rtsp_uri, name=name, **kwargs)

    if scheme in ("http", "https"):
        return MjpegHttpSource(spec, name=name, **kwargs)

    # rtsp://… , a file path, or anything else cv2 can open.
    return VideoSource(spec, name=name, **kwargs)
