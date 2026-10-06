"""
Video ingestion layer — heterogeneous camera onboarding.

The pipeline downstream (detector -> tracker -> zones -> anpr) only ever
touches a source through a tiny contract: iterate ``frames()`` for
``(index, BGR-ndarray)`` tuples, read ``fps``, check ``is_stream``, call
``_open()`` to restart a finite source, and ``release()`` at the end. After
each yielded frame an adapter also exposes ``last_pts_s`` / ``last_ts_epoch``
(PTS-derived timing) and ``last_discontinuity``. Every
adapter here implements exactly that contract and nothing else, so the same
detection loop runs unchanged regardless of how the pixels arrived. This is
the seam that makes the system forward-compatible with a federation /
edge-node pattern: a remote node only has to speak this frame contract.

Adapters:
  * VideoSource      — OpenCV VideoCapture: local webcam (int index), RTSP
                       IP cameras / gateways (forced TCP), HLS playlists,
                       and finite local video files/recorded clips.
                       Reconnects live sources with exponential backoff;
                       ends cleanly at EOF on files.
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

import collections
import io
import os
import random
import re
import threading
import time
from urllib.parse import quote, urljoin, urlparse, urlunparse

# FFmpeg capture options must be in the environment BEFORE OpenCV's FFmpeg
# plugin first opens a stream (on Windows the plugin DLL snapshots the env at
# load), so this runs at import time, ahead of ``import cv2``. RTSP is forced
# over TCP: UDP fails across NAT/firewalls and partial UDP delivery yields
# corrupt frames that look like model bugs. An operator-provided value wins.
_FFMPEG_OPTS_ENV = "OPENCV_FFMPEG_CAPTURE_OPTIONS"


def _default_ffmpeg_options() -> str:
    # (HLS is fetched by HlsBufferedSource, which sends the portal cookie itself.)
    return "rtsp_transport;tcp"


os.environ.setdefault(_FFMPEG_OPTS_ENV, _default_ffmpeg_options())

import cv2  # noqa: E402  (must follow the env setup above)
import numpy as np  # noqa: E402

# Network stream tuning. Open/read timeouts bound how long a dead feed can
# stall a worker; normal inter-frame gaps are far shorter than READ_TIMEOUT.
OPEN_TIMEOUT_MS = int(os.environ.get("IBVAP_STREAM_OPEN_TIMEOUT_MS", "10000"))
READ_TIMEOUT_MS = int(os.environ.get("IBVAP_STREAM_READ_TIMEOUT_MS", "15000"))
# A PTS jump larger than this (either direction) is treated as a timeline
# discontinuity rather than an ordinary inter-frame gap.
PTS_GAP_DISCONTINUITY_S = float(os.environ.get("IBVAP_PTS_GAP_DISCONTINUITY_S", "10"))


# --- URL helpers -----------------------------------------------------------

def _scheme(uri) -> str:
    return urlparse(uri).scheme.lower() if isinstance(uri, str) else ""


def is_hls_uri(uri) -> bool:
    return _scheme(uri) in ("http", "https") and urlparse(uri).path.lower().endswith(".m3u8")


def is_network_stream_uri(uri) -> bool:
    """RTSP(S) or HLS — live network video decoded through FFmpeg."""
    return _scheme(uri) in ("rtsp", "rtsps") or is_hls_uri(uri)


def redact_uri(uri):
    """Strip userinfo (credentials) from a URI for logs/status output."""
    if not isinstance(uri, str) or "@" not in urlparse(uri).netloc:
        return uri
    parsed = urlparse(uri)
    return urlunparse(parsed._replace(netloc="***@" + parsed.netloc.rsplit("@", 1)[1]))


def _gateway_rtsp_hosts() -> set[str]:
    raw = os.environ.get("IBVAP_GATEWAY_RTSP_HOSTS", "103.250.160.189,stream.corp8.cloud")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def inject_gateway_credentials(uri):
    """Add the operator's gateway email/password to a credential-less RTSP
    URL on a configured gateway host. Credentials live only in the process
    environment (IBVAP_GATEWAY_EMAIL / IBVAP_GATEWAY_PASSWORD) — never in the
    registry DB, API responses or logs. The email's '@' is percent-encoded
    (%40) as the gateway requires."""
    if _scheme(uri) not in ("rtsp", "rtsps"):
        return uri
    parsed = urlparse(uri)
    if "@" in parsed.netloc or (parsed.hostname or "").lower() not in _gateway_rtsp_hosts():
        return uri
    email = os.environ.get("IBVAP_GATEWAY_EMAIL", "").strip()
    password = os.environ.get("IBVAP_GATEWAY_PASSWORD", "")
    if not email or not password:
        return uri
    userinfo = f"{quote(email, safe='')}:{quote(password, safe='')}"
    return urlunparse(parsed._replace(netloc=f"{userinfo}@{parsed.netloc}"))


def backoff_delays(initial_s=2.0, max_s=30.0, jitter=0.1):
    """Exponential reconnect backoff: ~2s, 4s, 8s, 16s, then capped at ~30s.
    Small jitter keeps many cameras from reconnecting in lockstep after a
    gateway restart."""
    delay = initial_s
    while True:
        j = 1.0 + random.uniform(-jitter, jitter) if jitter else 1.0
        yield min(delay, max_s) * j
        delay = min(delay * 2, max_s)


def _fourcc_to_str(value) -> str | None:
    try:
        code = int(value)
    except (TypeError, ValueError):
        return None
    if code <= 0:
        return None
    chars = "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4))
    return chars if chars.isprintable() and chars.strip() else None


class PtsClock:
    """Turns per-frame presentation timestamps into a monotonic media clock.

    All timing downstream (dwell, speed, loitering, route transit) is driven
    from PTS, never from frame arrival time or CAP_PROP_FPS. Handles:
      * a new connection (reconnect)      -> discontinuity, re-anchor
      * PTS going backwards / a big jump  -> discontinuity, re-anchor
      * sources with no usable PTS (e.g. some webcams, MJPEG) -> falls back to
        the monotonic clock and reports ``pts_source='arrival-fallback'``.
    ``pts_s`` stays continuous and strictly increasing across segments;
    ``ts_epoch`` maps media time to wall-clock via an anchor taken once per
    timeline segment, so event timestamps follow PTS spacing.
    """

    _STALE_LIMIT = 3  # consecutive non-advancing PTS readings before fallback

    def __init__(self, arrival_only=False):
        # arrival_only: the source is known to carry no PTS (webcam, MJPEG).
        self.arrival_only = arrival_only
        self.reset()

    def reset(self):
        self.pts_s = None           # continuous media time (seconds)
        self.ts_epoch = None        # wall-clock estimate of this frame
        self.pts_source = "stream"
        self._raw_prev = None
        self._stale = 0
        self._segment_base = None   # raw seconds at segment start
        self._segment_offset = 0.0  # media time at segment start
        self._anchor_epoch = None
        self._fallback = self.arrival_only
        if self.arrival_only:
            self.pts_source = "arrival-fallback"

    def _start_segment(self, raw_s, now_epoch):
        self._segment_offset = (self.pts_s + 1e-3) if self.pts_s is not None else 0.0
        self._segment_base = raw_s
        self._anchor_epoch = now_epoch - self._segment_offset

    def update(self, raw_ms, new_connection=False, now_mono=None, now_epoch=None) -> bool:
        """Feed one frame's raw PTS (ms). Returns True on a discontinuity."""
        now_mono = time.monotonic() if now_mono is None else now_mono
        now_epoch = time.time() if now_epoch is None else now_epoch
        valid = raw_ms is not None and raw_ms == raw_ms and raw_ms > 0  # (NaN != NaN)
        raw_s = raw_ms / 1000.0 if valid else None
        # A clear backward jump is a new timeline, not a stale reading.
        jumped_back = (raw_s is not None and self._raw_prev is not None and not self._fallback
                       and raw_s < self._raw_prev - 0.5)
        advancing = raw_s is not None and (self._raw_prev is None or new_connection
                                           or jumped_back or raw_s > self._raw_prev)

        quiet_rebase = False
        if not self._fallback:
            self._stale = 0 if advancing else self._stale + 1
            if self._stale >= self._STALE_LIMIT:
                # This source has no usable PTS — continue on the monotonic
                # clock (same timeline, so not a discontinuity).
                self._fallback = True
                self.pts_source = "arrival-fallback"
                quiet_rebase = True

        if self._fallback:
            raw_s = now_mono
        elif not advancing:
            # Transient bad/non-advancing PTS: hold just ahead of the last frame.
            raw_s = (self._raw_prev or 0.0) + 1e-3

        discontinuity = False
        if self._segment_base is None or quiet_rebase:
            discontinuity = self._segment_base is None and self.pts_s is not None
            self._start_segment(raw_s, now_epoch)
        elif new_connection:
            discontinuity = True
            self._start_segment(raw_s, now_epoch)
        else:
            step = raw_s - self._raw_prev
            if step < -0.5 or step > PTS_GAP_DISCONTINUITY_S:
                discontinuity = True
                self._start_segment(raw_s, now_epoch)

        self._raw_prev = raw_s
        self.pts_s = self._segment_offset + (raw_s - self._segment_base)
        self.ts_epoch = self._anchor_epoch + self.pts_s
        return discontinuity


def _create_capture(uri):
    """Open a cv2.VideoCapture. Network streams go explicitly through the
    FFmpeg backend with open/read timeouts so a dead feed can't hang forever."""
    if isinstance(uri, int) or not is_network_stream_uri(uri):
        return cv2.VideoCapture(uri)
    params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS,
              cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS]
    try:
        return cv2.VideoCapture(uri, cv2.CAP_FFMPEG, params)
    except TypeError:  # older cv2 builds without the params overload
        return cv2.VideoCapture(uri, cv2.CAP_FFMPEG)


class VideoSource:
    """OpenCV-backed source: webcam (int index), RTSP/HLS URL, or local file.

    Live network streams (RTSP/HLS) follow the gateway's operating rules:
      * RTSP forced over TCP (see _FFMPEG_OPTS_ENV above)
      * reconnect forever with exponential backoff (~2s -> 30s cap), never a
        tight loop; the backoff only resets once a frame actually arrives
      * a failing initial connect is retried in the background instead of
        killing the camera worker
      * a reader thread keeps only the NEWEST decoded frame, so a slow model
        never builds an ever-growing latency backlog (and the burst of GOP
        frames replayed on connect is simply skipped)
      * every delivered frame carries PTS-derived timing (``last_pts_s``,
        ``last_ts_epoch``) plus a ``last_discontinuity`` flag (reconnect or
        timeline jump); FFmpeg decoder warnings at join are logged by FFmpeg
        and never treated as fatal.
    """

    def __init__(self, uri, name="camera", reconnect_delay_s=2.0, max_reconnect_attempts=None,
                 max_reconnect_delay_s=30.0, latest_frame_only=None, backoff_jitter=0.1):
        self.uri = uri
        self.name = name
        self.reconnect_delay_s = reconnect_delay_s
        self.max_reconnect_delay_s = max_reconnect_delay_s
        self.backoff_jitter = backoff_jitter
        self.is_network = is_network_stream_uri(uri)
        # Anything that isn't a finite local file is a "live" source that
        # should reconnect on failure rather than just stopping.
        self.is_stream = isinstance(uri, int) or self.is_network
        # Network feeds retry forever; a local webcam gives up after a few.
        if max_reconnect_attempts is None and not self.is_network:
            max_reconnect_attempts = 5
        self.max_reconnect_attempts = max_reconnect_attempts
        self.latest_frame_only = self.is_network if latest_frame_only is None else latest_frame_only
        # HLS from the gateway portal is a recording served as files: FFmpeg
        # would read it as fast as the download allows (fast-forward on a
        # quick link), so frames are released at the rate their PTS says.
        self.pace_realtime = is_hls_uri(uri)
        self._pace_anchor = None  # (monotonic wall time, pts seconds)

        # Webcams expose no trustworthy PTS through OpenCV; everything else
        # (RTSP/HLS/files) is timed from the stream's own timestamps.
        self.clock = PtsClock(arrival_only=isinstance(uri, int))
        self.last_pts_s = None
        self.last_ts_epoch = None
        self.last_discontinuity = False

        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._slot = None           # newest (frame, raw_ms, conn_gen) from the reader
        # Optional hook called by the reader thread with EVERY decoded frame
        # (the AI loop only sees the newest one) — used for full-rate display.
        self.on_decoded = None
        self._reader = None
        self._reader_done = False
        self._conn_gen = 0          # increments on every successful (re)connect
        self._seen_gen = None
        self._backoff = None        # live backoff generator; None = reset
        self._fps_ema = None
        self._last_raw_ms = None
        self._stats = {
            "connected": False, "reconnects": 0, "read_failures": 0,
            "frames_decoded": 0, "frames_delivered": 0, "frames_dropped": 0,
            "discontinuities": 0, "codec": None, "width": None, "height": None,
            "last_frame_monotonic": None, "last_error": None,
        }

        self.cap = None
        try:
            self._open()
        except ConnectionError as exc:
            if not self.is_network:
                raise
            # The feed may be restarting — keep the worker alive and let the
            # reader retry with backoff instead of failing permanently.
            self._stats["last_error"] = str(exc)
            print(f"[{self.name}] initial connect failed; will retry with backoff.")

    # -- connection -------------------------------------------------------
    def _open(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
        self.cap = _create_capture(self.uri)
        if not self.cap.isOpened():
            self._stats["connected"] = False
            raise ConnectionError(f"[{self.name}] could not open source: {redact_uri(self.uri)}")
        self._conn_gen += 1
        self._pace_anchor = None
        self._stats["connected"] = True
        self._stats["codec"] = _fourcc_to_str(self.cap.get(cv2.CAP_PROP_FOURCC)) or self._stats["codec"]

    def _wait(self, seconds) -> bool:
        """Sleep that wakes early on stop(). Returns True if stopping."""
        return self._stop.wait(seconds)

    def _reconnect(self) -> bool:
        if self._backoff is None:
            self._backoff = backoff_delays(self.reconnect_delay_s, self.max_reconnect_delay_s,
                                           self.backoff_jitter)
        attempt = 0
        while not self._stop.is_set():
            attempt += 1
            if self.max_reconnect_attempts is not None and attempt > self.max_reconnect_attempts:
                return False
            delay = next(self._backoff)
            print(f"[{self.name}] reconnect attempt {attempt} in {delay:.1f}s...")
            if self._wait(delay):
                return False
            try:
                self._open()
                self._stats["reconnects"] += 1
                print(f"[{self.name}] reconnected.")
                return True
            except ConnectionError as exc:
                self._stats["last_error"] = str(exc)
        return False

    def _pace(self, raw_ms):
        """Real-time playback for HLS: hold a frame until its PTS is due.
        After a stall (download slower than playback) re-anchor instead of
        fast-forwarding to catch up — the same behaviour as a browser player."""
        if not self.pace_realtime or not raw_ms or raw_ms <= 0:
            return
        now, pts = time.monotonic(), raw_ms / 1000.0
        if self._pace_anchor is None:
            self._pace_anchor = (now, pts)
            return
        wall0, pts0 = self._pace_anchor
        ahead = (pts - pts0) - (now - wall0)
        if ahead > 1.0 or ahead < -0.5:
            self._pace_anchor = (now, pts)  # PTS jump or stall: restart the clock here
        elif ahead > 0:
            self._wait(ahead)

    # -- reading ----------------------------------------------------------
    def _read_one(self):
        """Read one frame, reconnecting as needed. Returns (frame, raw_ms, gen)
        or None when the source is finished/stopped."""
        while not self._stop.is_set():
            if self.cap is None or not self.cap.isOpened():
                if not self.is_stream or not self._reconnect():
                    return None
                continue
            ok, frame = self.cap.read()
            if ok and frame is not None:
                self._backoff = None  # healthy again: next outage starts at ~2s
                raw_ms = self.cap.get(cv2.CAP_PROP_POS_MSEC)
                self._note_decoded(frame, raw_ms)
                self._pace(raw_ms)
                return frame, raw_ms, self._conn_gen
            if not self.is_stream:
                return None  # end of file — normal termination for recorded footage
            self._stats["read_failures"] += 1
            self._stats["connected"] = False
            print(f"[{self.name}] stream read failed, reconnecting with backoff...")
            if not self._reconnect():
                if not self._stop.is_set():
                    print(f"[{self.name}] giving up after {self.max_reconnect_attempts} attempts.")
                return None
        return None

    def _note_decoded(self, frame, raw_ms):
        st = self._stats
        st["frames_decoded"] += 1
        st["last_frame_monotonic"] = time.monotonic()
        st["height"], st["width"] = frame.shape[:2]
        # Real delivery rate measured from PTS deltas (never CAP_PROP_FPS).
        prev = self._last_raw_ms
        if raw_ms and prev and raw_ms > prev and (raw_ms - prev) < 2000:
            inst = 1000.0 / (raw_ms - prev)
            self._fps_ema = inst if self._fps_ema is None else 0.9 * self._fps_ema + 0.1 * inst
        self._last_raw_ms = raw_ms

    def _reader_loop(self):
        try:
            while not self._stop.is_set():
                item = self._read_one()
                if item is None:
                    break
                if self.on_decoded is not None:
                    try:
                        self.on_decoded(item[0])
                    except Exception as exc:  # display must never kill ingestion
                        print(f"[{self.name}] display hook failed: {exc}")
                with self._cond:
                    if self._slot is not None:
                        self._stats["frames_dropped"] += 1
                    self._slot = item
                    self._cond.notify_all()
        finally:
            with self._cond:
                self._reader_done = True
                self._cond.notify_all()

    def _next_threaded(self):
        if self._reader is None:
            self._reader_done = False
            self._reader = threading.Thread(target=self._reader_loop,
                                            name=f"reader-{self.name}", daemon=True)
            self._reader.start()
        with self._cond:
            while self._slot is None and not self._reader_done and not self._stop.is_set():
                self._cond.wait(timeout=0.5)
            item, self._slot = self._slot, None
        return item

    def _stamp(self, raw_ms, gen):
        new_conn = self._seen_gen is not None and gen != self._seen_gen
        self._seen_gen = gen
        disc = self.clock.update(raw_ms, new_connection=new_conn)
        if disc:
            self._stats["discontinuities"] += 1
        self.last_pts_s = self.clock.pts_s
        self.last_ts_epoch = self.clock.ts_epoch
        self.last_discontinuity = disc

    @property
    def fps(self):
        """Measured (PTS-derived) rate when known, else the declared rate.
        Informational only (e.g. VideoWriter) — never used for timing."""
        if self._fps_ema:
            return self._fps_ema
        fps = self.cap.get(cv2.CAP_PROP_FPS) if self.cap is not None else 0
        return fps if fps and fps > 0 else 25.0

    def frames(self):
        """Yield (frame_index, frame) tuples. Live streams reconnect with
        backoff; files stop cleanly at EOF. After each yield, ``last_pts_s``,
        ``last_ts_epoch`` and ``last_discontinuity`` describe that frame."""
        idx = 0
        while not self._stop.is_set():
            item = self._next_threaded() if self.latest_frame_only else self._read_one()
            if item is None:
                break
            frame, raw_ms, gen = item
            self._stamp(raw_ms, gen)
            self._stats["frames_delivered"] += 1
            yield idx, frame
            idx += 1

    def stats(self) -> dict:
        st = dict(self._stats)
        last = st.pop("last_frame_monotonic")
        st["last_frame_age_s"] = round(time.monotonic() - last, 2) if last else None
        st["measured_fps"] = round(self._fps_ema, 2) if self._fps_ema else None
        st["pts_source"] = self.clock.pts_source
        st["transport"] = ("rtsp-tcp" if _scheme(self.uri) in ("rtsp", "rtsps")
                           else "hls" if is_hls_uri(self.uri)
                           else "webcam" if isinstance(self.uri, int) else "file")
        st["source"] = redact_uri(self.uri) if isinstance(self.uri, str) else self.uri
        return st

    def stop(self):
        """Signal the source to stop (wakes backoff sleeps and the reader)."""
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def release(self):
        self.stop()
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=READ_TIMEOUT_MS / 1000.0 + 2)
        if self.cap is not None:
            self.cap.release()


class _ChunkStream:
    """One HLS chunk that the decoder can read WHILE it is still downloading:
    the downloader feeds bytes in as they arrive, read() hands them out and
    blocks only until the next bytes come in."""

    def __init__(self, seq, duration):
        self.seq, self.duration = seq, duration
        self._buf = bytearray()
        self._cond = threading.Condition()
        self._done = False

    def feed(self, data):
        if data:
            with self._cond:
                self._buf += data
                self._cond.notify_all()

    def finish(self):
        with self._cond:
            self._done = True
            self._cond.notify_all()

    def read(self, size=-1):
        with self._cond:
            while not self._buf and not self._done:
                self._cond.wait(timeout=0.5)
            n = len(self._buf) if size is None or size < 0 else min(size, len(self._buf))
            out = bytes(self._buf[:n])
            del self._buf[:n]
            return out


class HlsBufferedSource(VideoSource):
    """HLS reader for the gateway portal feeds that adapts to the bandwidth.

    Chunks are decoded WHILE they download (progressive), so frames reach
    the screen as soon as their bytes arrive — a slow 1080p chunk no longer
    means waiting ~45 s for the whole 6 s file. A downloader thread fetches
    (and AES-128-decrypts, block by block) chunks ahead of playback, up to
    IBVAP_HLS_MAX_BUFFER_S of video. Playback speed follows the bandwidth:
    real speed when the link keeps up, and every frame shown as soon as it
    arrives when it doesn't (never frozen waiting for a buffer to fill).

    The portal session cookie (IBVAP_HLS_COOKIE) is sent only to hosts in
    IBVAP_HLS_COOKIE_HOSTS. Everything downstream — reconnect backoff, PTS
    timing, real-time pacing, the full-rate display hook — is VideoSource's.
    """

    _UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"

    def __init__(self, uri, name="camera", start_buffer_s=None, max_buffer_s=None, **kwargs):
        import requests

        self._start_buffer_s = float(start_buffer_s if start_buffer_s is not None
                                     else os.environ.get("IBVAP_HLS_START_BUFFER_S", "0"))
        self._max_buffer_s = float(max_buffer_s if max_buffer_s is not None
                                   else os.environ.get("IBVAP_HLS_MAX_BUFFER_S", "60"))
        self._http = requests.Session()
        self._seg_cond = threading.Condition()
        self._segments = collections.deque()  # _ChunkStream objects, in play order
        self._buffered_s = 0.0
        self._playing = False                 # False while (re)buffering
        self._playlist = None                 # parsed media playlist
        self._next_seq = None                 # next media-sequence number to download
        self._downloader = None
        self._current = None                  # chunk being decoded
        self._container = None
        self._frame_iter = None
        self._hls = {"segments_downloaded": 0, "stalls": 0, "download_mbit_s": None}
        super().__init__(uri, name=name, **kwargs)

    # -- HTTP --------------------------------------------------------------
    def _headers(self, url):
        headers = {"User-Agent": self._UA}
        cookie = os.environ.get("IBVAP_HLS_COOKIE", "").strip()
        hosts = {h.strip().lower() for h in
                 os.environ.get("IBVAP_HLS_COOKIE_HOSTS", "cctv.corp8.cloud").split(",") if h.strip()}
        if cookie and (urlparse(url).hostname or "").lower() in hosts:
            headers["Cookie"] = cookie
        return headers

    def _get(self, url, timeout=30, stream=False):
        resp = self._http.get(url, headers=self._headers(url), timeout=timeout,
                              allow_redirects=False, stream=stream)
        if resp.status_code != 200:
            detail = resp.text[:100] if resp.headers.get("content-type", "").startswith("text") else ""
            if resp.status_code in (301, 302, 303, 307) and "/auth/login" in resp.headers.get("location", ""):
                detail = "redirected to login — IBVAP_HLS_COOKIE missing or expired"
            resp.close()
            raise ConnectionError(f"[{self.name}] HTTP {resp.status_code} for "
                                  f"{urlparse(url).path}: {detail}".rstrip(": "))
        return resp

    def _load_playlist(self):
        """Fetch + parse the media playlist (following a master playlist to
        its first variant) and the AES-128 key if the chunks are encrypted."""
        url = self.uri
        text = self._get(url, timeout=15).text
        if "#EXT-X-STREAM-INF" in text:
            variant = next(l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#"))
            url = urljoin(url, variant)
            text = self._get(url, timeout=15).text
        if not text.startswith("#EXTM3U"):
            raise ConnectionError(f"[{self.name}] not an HLS playlist")
        seq, duration, key, segments = 0, None, None, []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                seq = int(line.split(":", 1)[1])
            elif line.startswith("#EXTINF:"):
                duration = float(line[8:].split(",")[0])
            elif line.startswith("#EXT-X-KEY:"):
                attrs = dict(re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line[11:]))
                attrs = {k: v.strip('"') for k, v in attrs.items()}
                key = None if attrs.get("METHOD", "NONE") == "NONE" else attrs
            elif line and not line.startswith("#"):
                segments.append({"seq": seq, "url": urljoin(url, line), "duration": duration or 6.0, "key": key})
                seq, duration = seq + 1, None
        keys = {}
        for seg in segments:
            k = seg["key"]
            if k and k.get("URI") not in keys:
                if k.get("METHOD") != "AES-128":
                    raise ConnectionError(f"[{self.name}] unsupported HLS encryption {k.get('METHOD')}")
                keys[k["URI"]] = self._get(urljoin(url, k["URI"]), timeout=15).content
        self._playlist = {"segments": segments, "keys": keys, "base": url,
                          "ended": "#EXT-X-ENDLIST" in text or "PLAYLIST-TYPE:VOD" in text}

    def _decryptor(self, seg):
        k = seg["key"]
        if not k:
            return None
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        iv = bytes.fromhex(k["IV"][2:]) if k.get("IV", "").lower().startswith("0x") else seg["seq"].to_bytes(16, "big")
        return Cipher(algorithms.AES(self._playlist["keys"][k["URI"]]), modes.CBC(iv)).decryptor()

    # -- downloader thread -------------------------------------------------------
    def _next_segment(self):
        """The next chunk to fetch; loops a finished (VOD) playlist, and
        re-reads a live playlist for new chunks."""
        segs = self._playlist["segments"]
        if self._next_seq is None:
            self._next_seq = segs[0]["seq"] if segs else 0
        for seg in segs:
            if seg["seq"] >= self._next_seq:
                return seg
        if self._playlist["ended"]:
            self._next_seq = segs[0]["seq"]  # feeds loop: start again
            return segs[0]
        self._load_playlist()  # live: fetch newly published chunks
        return next((s for s in self._playlist["segments"] if s["seq"] >= self._next_seq), None)

    def _download(self, seg, resp, chunk):
        """Stream one chunk's bytes into `chunk` as they arrive, decrypting
        block by block (the last block is held back for padding removal)."""
        dec, held, total, t0 = self._decryptor(seg), b"", 0, time.monotonic()
        try:
            for block in resp.iter_content(chunk_size=16384):
                if self._stop.is_set():
                    return
                total += len(block)
                if dec is None:
                    chunk.feed(block)
                    continue
                buf = held + dec.update(block)
                chunk.feed(buf[:-16])
                held = buf[-16:]
            if dec is not None:
                tail = held + dec.finalize()
                chunk.feed(tail[:-tail[-1]] if tail and 0 < tail[-1] <= 16 else tail)
        finally:
            chunk.finish()
            resp.close()
        dt = time.monotonic() - t0
        self._hls["segments_downloaded"] += 1
        self._hls["download_mbit_s"] = round(total * 8 / max(dt, 1e-3) / 1e6, 2)

    def _download_loop(self):
        backoff = None
        while not self._stop.is_set():
            with self._seg_cond:
                while self._buffered_s >= self._max_buffer_s and not self._stop.is_set():
                    self._seg_cond.wait(timeout=0.5)
            if self._stop.is_set():
                return
            try:
                seg = self._next_segment()
                if seg is None:
                    self._wait(2.0)
                    continue
                resp = self._get(seg["url"], timeout=(10, 30), stream=True)
                backoff = None
                chunk = _ChunkStream(seg["seq"], seg["duration"])
                with self._seg_cond:  # playable as soon as its first bytes arrive
                    self._segments.append(chunk)
                    self._buffered_s += seg["duration"]
                    self._seg_cond.notify_all()
                self._next_seq = seg["seq"] + 1
                self._download(seg, resp, chunk)
            except Exception as exc:
                self._stats["last_error"] = str(exc)
                self._stats["read_failures"] += 1
                if backoff is None:
                    backoff = backoff_delays(self.reconnect_delay_s, self.max_reconnect_delay_s, self.backoff_jitter)
                delay = next(backoff)
                print(f"[{self.name}] HLS download failed ({exc}); retrying in {delay:.1f}s")
                if self._wait(delay):
                    return
                try:
                    self._load_playlist()  # e.g. a refreshed cookie, or a changed playlist
                except Exception:
                    pass

    # -- VideoSource hooks ---------------------------------------------------------
    def _open(self):
        try:
            self._load_playlist()
        except ConnectionError:
            raise
        except Exception as exc:  # timeouts, DNS, TLS … -> the normal backoff path
            raise ConnectionError(f"[{self.name}] {exc}") from exc
        self._conn_gen += 1
        self._pace_anchor = None
        self._stats["connected"] = True
        if self._downloader is None:
            self._downloader = threading.Thread(target=self._download_loop,
                                                name=f"hls-{self.name}", daemon=True)
            self._downloader.start()

    def _close_container(self):
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                pass
        self._container, self._frame_iter, self._current = None, None, None

    def _read_one(self):
        import av

        while not self._stop.is_set():
            if self._playlist is None:  # initial connect failed: retry with backoff
                if not self._reconnect():
                    return None
                continue
            if self._frame_iter is not None:
                try:
                    frame = next(self._frame_iter)
                except StopIteration:
                    self._close_container()
                    continue
                except av.FFmpegError as exc:
                    print(f"[{self.name}] skipping undecodable chunk: {exc}")
                    self._close_container()
                    continue
                img = frame.to_ndarray(format="bgr24")
                raw_ms = frame.time * 1000.0 if frame.time is not None else None
                self._backoff = None
                self._note_decoded(img, raw_ms)
                self._pace(raw_ms)
                return img, raw_ms, self._conn_gen

            with self._seg_cond:
                # Optional start buffer (IBVAP_HLS_START_BUFFER_S, default 0 =
                # start with the first bytes).
                need = self._start_buffer_s if not self._playing else 0.0
                while (not self._segments or self._buffered_s < need) and not self._stop.is_set():
                    if self._playing:
                        self._playing = False
                        self._hls["stalls"] += 1
                        need = self._start_buffer_s
                    self._seg_cond.wait(timeout=0.5)
                if self._stop.is_set():
                    return None
                self._playing = True
                self._current = self._segments.popleft()
                self._buffered_s -= self._current.duration
                self._seg_cond.notify_all()
            try:
                # Tiny probe so decoding starts on the first bytes, not after
                # FFmpeg has read megabytes of a slow download.
                self._container = av.open(self._current, format="mpegts",
                                          options={"probesize": "32768", "analyzeduration": "0"})
                stream = self._container.streams.video[0]
                stream.thread_type = "AUTO"
                self._stats["codec"] = stream.codec_context.name
                self._frame_iter = self._container.decode(stream)
            except (av.FFmpegError, IndexError) as exc:
                print(f"[{self.name}] skipping unreadable chunk: {exc}")
                self._close_container()
        return None

    def stats(self) -> dict:
        st = super().stats()
        st.update(self._hls)
        st["buffered_s"] = round(self._buffered_s, 1)
        st["buffering"] = not self._playing
        return st

    def release(self):
        self.stop()
        with self._seg_cond:
            for chunk in list(self._segments) + ([self._current] if self._current else []):
                chunk.finish()  # unblock a decoder waiting for bytes
            self._seg_cond.notify_all()
        super().release()
        if self._downloader is not None and self._downloader is not threading.current_thread():
            self._downloader.join(timeout=5)
        self._close_container()
        self._http.close()


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

        # MJPEG over HTTP carries no presentation timestamps at all, so this is
        # the one adapter whose media clock is arrival time (documented).
        self.clock = PtsClock(arrival_only=True)
        self.last_pts_s = None
        self.last_ts_epoch = None
        self.last_discontinuity = False

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
                            self.last_discontinuity = self.clock.update(None)
                            self.last_pts_s = self.clock.pts_s
                            self.last_ts_epoch = self.clock.ts_epoch
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
    if is_hls_uri(spec):
        return "hls"
    if scheme in ("http", "https"):
        return "mjpeg"
    if scheme in ("rtsp", "rtsps"):
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
        print(f"[{name}] ONVIF resolved -> {redact_uri(rtsp_uri)}")
        return VideoSource(rtsp_uri, name=name, **kwargs)

    if is_hls_uri(spec):
        # HLS (e.g. https://cctv.corp8.cloud/<id>/index.m3u8): buffered download-ahead.
        return HlsBufferedSource(spec, name=name, **kwargs)

    if scheme in ("http", "https"):
        return MjpegHttpSource(spec, name=name, **kwargs)

    if scheme in ("rtsp", "rtsps"):
        # Gateway RTSP: credentials come from the environment at open time.
        return VideoSource(inject_gateway_credentials(spec), name=name, **kwargs)

    # A file path, or anything else cv2 can open.
    return VideoSource(spec, name=name, **kwargs)
