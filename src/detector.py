"""
Detection layer — YOLOv8 wrapper scoped to the classes IBVAP cares about
(person + vehicle types). Swappable for a fine-tuned/IR-trained checkpoint
later without touching any downstream code.
"""

from ultralytics import YOLO
import supervision as sv

# COCO class ids we care about for border surveillance.
# person=0, bicycle=1, car=2, motorcycle=3, bus=5, train=6, truck=7
RELEVANT_CLASS_IDS = {0, 1, 2, 3, 5, 7}

CLASS_NAME_OVERRIDES = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}


class Detector:
    def __init__(self, weights="yolov8n.pt", conf_threshold=0.35, device=None):
        """
        weights: path to a .pt checkpoint. Starts from stock COCO weights
                 (yolov8n.pt) for pipeline validation; swap for a
                 fine-tuned/IR checkpoint before field deployment.
        conf_threshold: minimum detection confidence to keep.
        """
        self.model = YOLO(weights)
        self.conf_threshold = conf_threshold
        self.device = device

    def detect(self, frame):
        """Run detection on a single BGR frame.
        Returns an sv.Detections object filtered to RELEVANT_CLASS_IDS."""
        results = self.model(
            frame,
            conf=self.conf_threshold,
            device=self.device,
            verbose=False,
        )[0]

        detections = sv.Detections.from_ultralytics(results)

        if len(detections) > 0:
            mask = [cls_id in RELEVANT_CLASS_IDS for cls_id in detections.class_id]
            detections = detections[mask]

        return detections

    def class_name(self, class_id):
        return CLASS_NAME_OVERRIDES.get(int(class_id), str(class_id))
