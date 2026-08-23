"""
IBVAP Real-Time Web Dashboard Application
Surveillance AI Control Center with Live MJPEG Streaming, Virtual Fence Monitoring,
and Real-time Event Logging.
"""

import os
import sys
import time
import threading
import cv2
import numpy as np
from flask import Flask, render_template, Response, jsonify, request

sys.path.insert(0, os.path.dirname(__file__))

import supervision as sv
from src.ingestion import VideoSource
from src.detector import Detector
from src.tracker import Tracker
from src.zones import Zone, ZoneManager, ZoneEventType

app = Flask(__name__)

# Global Pipeline State
class SurveillanceEngine:
    def __init__(self, source_uri=0):
        self.lock = threading.Lock()
        self.source_uri = source_uri
        self.running = False
        self.fps = 0.0
        self.active_tracks = 0
        self.total_detections = 0
        self.events = []
        self.current_frame_bytes = None
        self.error_message = None

    def start(self, source_uri=None):
        if source_uri is not None:
            self.source_uri = source_uri
        self.running = True
        self.thread = threading.Thread(target=self._process_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _process_loop(self):
        print(f"[ENGINE] Starting processing loop for source: {self.source_uri}")
        self.error_message = None
        
        try:
            source = VideoSource(self.source_uri, name="web-engine")
        except Exception as e:
            print(f"[ENGINE ERROR] Could not open source {self.source_uri}: {e}")
            self.error_message = f"Camera/Source error: {str(e)}"
            # Fallback to sample clip if webcam access fails
            sample_clip = os.path.join(os.path.dirname(__file__), "sample_data", "synthetic_test_clip.mp4")
            if os.path.exists(sample_clip) and self.source_uri != sample_clip:
                print(f"[ENGINE] Falling back to sample clip: {sample_clip}")
                self.source_uri = sample_clip
                try:
                    source = VideoSource(self.source_uri, name="web-engine-fallback")
                    self.error_message = "Webcam not available or permission denied. Running fallback test stream."
                except Exception as ex:
                    print(f"[ENGINE FATAL] Fallback failed: {ex}")
                    return
            else:
                return

        # Setup AI models
        detector = Detector(weights="yolov8n.pt", conf_threshold=0.35)
        tracker = Tracker(frame_rate=int(source.fps))

        # Default Zone Polygon (Right 45% of frame)
        W, H = 960, 540
        fence_zone = Zone(
            name="Restricted Virtual Fence",
            polygon=[(W * 0.55, 0), (W, 0), (W, H), (W * 0.55, H)],
            debounce_frames=3,
        )
        zone_manager = ZoneManager(zones=[fence_zone])

        box_annotator = sv.BoxAnnotator(thickness=2)
        label_annotator = sv.LabelAnnotator(text_thickness=1, text_scale=0.5)

        t0 = time.time()
        frame_counter = 0

        for idx, frame in source.frames():
            if not self.running:
                break

            h, w = frame.shape[:2]

            # Update zone polygon bounds dynamically to match frame size
            fence_zone.polygon = [(w * 0.55, 0), (w, 0), (w, h), (w * 0.55, h)]

            detections = detector.detect(frame)
            tracked = tracker.update(detections, idx)

            labels = []
            for i in range(len(tracked)):
                cls_id = int(tracked.class_id[i])
                tid = int(tracked.tracker_id[i])
                conf = float(tracked.confidence[i])
                class_name = detector.class_name(cls_id)
                labels.append(f"#{tid} {class_name} {conf:.2f}")

                cx, cy = (tracked.xyxy[i][0] + tracked.xyxy[i][2]) / 2.0, (tracked.xyxy[i][1] + tracked.xyxy[i][3]) / 2.0
                for evt in zone_manager.update(tid, cx, cy, idx):
                    evt_dict = {
                        "timestamp": time.strftime("%H:%M:%S"),
                        "frame_idx": evt.frame_idx,
                        "zone_name": evt.zone_name,
                        "tracker_id": evt.tracker_id,
                        "event_type": evt.event_type.value,
                        "class_name": class_name,
                    }
                    with self.lock:
                        self.events.insert(0, evt_dict)
                        if len(self.events) > 50:
                            self.events.pop()

            annotated = frame.copy()
            annotated = box_annotator.annotate(annotated, tracked)
            annotated = label_annotator.annotate(annotated, tracked, labels=labels)

            # Draw virtual fence
            pts = np.array([(int(x), int(y)) for x, y in fence_zone.polygon])
            overlay = annotated.copy()
            cv2.fillPoly(overlay, [pts], (0, 0, 235))
            cv2.addWeighted(overlay, 0.20, annotated, 0.80, 0, annotated)
            cv2.polylines(annotated, [pts], True, (0, 0, 255), 2)
            cv2.putText(annotated, "VIRTUAL FENCE ZONE", (int(w * 0.56), 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

            # Encode JPEG frame
            ok, jpeg = cv2.imencode('.jpg', annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok:
                with self.lock:
                    self.current_frame_bytes = jpeg.tobytes()
                    self.active_tracks = len(tracked)
                    self.total_detections += len(tracked)
                    frame_counter += 1
                    elapsed = time.time() - t0
                    self.fps = frame_counter / max(elapsed, 1e-5)

            time.sleep(0.01)  # small throttle for web server smooth streaming

        source.release()
        print("[ENGINE] Stopped.")

engine = SurveillanceEngine(source_uri=0)
engine.start()

@app.route("/")
def index():
    return render_template("index.html")

def generate_frames():
    while True:
        with engine.lock:
            frame = engine.current_frame_bytes
        if frame is not None:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        else:
            time.sleep(0.05)

@app.route("/video_feed")
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/api/stats")
def api_stats():
    with engine.lock:
        return jsonify({
            "fps": round(engine.fps, 1),
            "active_tracks": engine.active_tracks,
            "total_detections": engine.total_detections,
            "total_events": len(engine.events),
            "source_uri": str(engine.source_uri),
            "error_message": engine.error_message
        })

@app.route("/api/events")
def api_events():
    with engine.lock:
        return jsonify(engine.events)

@app.route("/api/switch_source", methods=["POST"])
def switch_source():
    data = request.json or {}
    source_type = data.get("type", "webcam")
    
    engine.stop()
    time.sleep(0.5)

    if source_type == "webcam":
        new_source = 0
    else:
        new_source = os.path.join(os.path.dirname(__file__), "sample_data", "synthetic_test_clip.mp4")
    
    engine.start(source_uri=new_source)
    return jsonify({"status": "success", "new_source": str(new_source)})

if __name__ == "__main__":
    print("\n=======================================================")
    print("  IBVAP Web Sentinel Dashboard")
    print("  Open URL in browser: http://localhost:5050")
    print("=======================================================\n")
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
