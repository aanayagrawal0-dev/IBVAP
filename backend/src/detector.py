"""
Detection layer — Two-Model Top-Down Architecture:
1. Base Detector (yolo11n.pt): Detects humans (0) and vehicles (car=2, motorcycle=3, bus=5, truck=7).
2. Pose Estimator (yolo11n-pose.pt): Runs on cropped person bounding boxes to extract 17 keypoint body skeletons.
Supports hardware acceleration via TensorRT (.engine) and OpenVINO formats for both models.
"""

import os
import logging
from pathlib import Path
from ultralytics import YOLO
import numpy as np
import supervision as sv

logger = logging.getLogger(__name__)

# COCO class ids for vehicles and humans
RELEVANT_CLASS_IDS = {0, 2, 3, 5, 7}

CLASS_NAME_OVERRIDES = {
    0: "person",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}

MODELS_DIR = Path(__file__).parent.parent / "models"


class Detector:
    def __init__(self, base_weights="yolo11n.pt", pose_weights="yolo11n-pose.pt",
                 conf_threshold=0.35, device=None, weights=None):
        """
        Dual-model initializer.
        base_weights: path/name for standard object detector (yolo11n.pt).
        pose_weights: path/name for pose estimator (yolo11n-pose.pt).
        weights: backwards-compatibility parameter.
        conf_threshold: minimum detection confidence.
        """
        if weights is not None and "pose" in weights:
            pose_weights = weights

        base_path = self._resolve_model_path(base_weights)
        pose_path = self._resolve_model_path(pose_weights)

        logger.info("Initializing Base Detector with: %s", base_path)
        self.base_detector = YOLO(base_path)

        logger.info("Initializing Pose Estimator with: %s", pose_path)
        self.pose_estimator = YOLO(pose_path)

        self.conf_threshold = conf_threshold
        self.device = device

    def _resolve_model_path(self, default_weights: str) -> str:
        """Checks backend/models/ for hardware-optimized engine files matching default_weights."""
        stem = Path(default_weights).stem  # e.g., 'yolo11n' or 'yolo11n-pose'
        engine_path = MODELS_DIR / f"{stem}.engine"
        openvino_path = MODELS_DIR / f"{stem}_openvino_model"

        if engine_path.exists():
            return str(engine_path)
        if openvino_path.exists():
            return str(openvino_path)

        if os.path.exists(default_weights):
            return default_weights

        return default_weights

    def detect(self, frame):
        """Run top-down detection:
        1. Run base_detector on full BGR frame. Filter for RELEVANT_CLASS_IDS.
        2. For 'person' (class_id == 0) detections, crop bbox and run pose_estimator.
        3. Map local crop keypoints back to global frame coordinates.
        4. Leave vehicle keypoints zeroed out.
        Returns sv.Detections with keypoints in detections.data."""
        frame_h, frame_w = frame.shape[:2]

        base_results = self.base_detector(
            frame,
            conf=self.conf_threshold,
            device=self.device,
            verbose=False,
        )[0]

        detections = sv.Detections.from_ultralytics(base_results)

        if len(detections) == 0:
            return detections

        # Filter for relevant human and vehicle class IDs
        mask = np.array([cls_id in RELEVANT_CLASS_IDS for cls_id in detections.class_id], dtype=bool)
        detections = detections[mask]

        num_det = len(detections)
        if num_det == 0:
            return detections

        keypoints_xy = np.zeros((num_det, 17, 2), dtype=np.float32)
        keypoints_conf = np.zeros((num_det, 17), dtype=np.float32)

        for i in range(num_det):
            cls_id = int(detections.class_id[i])
            if cls_id == 0:  # Person: run pose estimation on crop
                x1, y1, x2, y2 = detections.xyxy[i].astype(int)
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(frame_w, x2), min(frame_h, y2)

                if (x2 - x1) > 5 and (y2 - y1) > 5:
                    crop = frame[y1:y2, x1:x2]
                    try:
                        pose_results = self.pose_estimator(
                            crop,
                            conf=max(0.20, self.conf_threshold - 0.10),
                            device=self.device,
                            verbose=False,
                        )[0]

                        if pose_results.keypoints is not None and pose_results.keypoints.data is not None:
                            kpts_data = pose_results.keypoints.data.cpu().numpy()
                            if len(kpts_data) > 0:
                                # Use top pose detection within crop
                                crop_xy = kpts_data[0, :, :2]
                                crop_conf = kpts_data[0, :, 2] if kpts_data.shape[-1] >= 3 else np.ones(17)

                                # Map local crop coordinates to global frame
                                global_xy = crop_xy.copy()
                                global_xy[:, 0] += x1
                                global_xy[:, 1] += y1

                                keypoints_xy[i] = global_xy
                                keypoints_conf[i] = crop_conf
                    except Exception as exc:
                        logger.warning("Crop pose estimation failed for detection %d: %s", i, exc)

        if detections.data is None:
            detections.data = {}
        detections.data["keypoints_xy"] = keypoints_xy
        detections.data["keypoints_conf"] = keypoints_conf

        return detections

    def class_name(self, class_id):
        return CLASS_NAME_OVERRIDES.get(int(class_id), str(class_id))



