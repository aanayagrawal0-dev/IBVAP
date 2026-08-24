"""
Export base detector (yolo11n.pt) and pose estimator (yolo11n-pose.pt) to hardware-optimized formats
(TensorRT .engine for CUDA, OpenVINO for CPU).
Usage: python backend/src/export_models.py
"""

import os
import sys
import logging
from pathlib import Path
from ultralytics import YOLO

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Base models directory inside backend/models/
MODELS_DIR = Path(__file__).parent.parent / "models"


def export_single_model(weights: str):
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(weights).stem
    model = YOLO(weights)

    try:
        import torch
        cuda_available = torch.cuda.is_available()
    except ImportError:
        cuda_available = False

    if cuda_available:
        logger.info("CUDA detected! Exporting %s to TensorRT (FP16 engine)...", weights)
        engine_path = MODELS_DIR / f"{stem}.engine"
        if not engine_path.exists():
            exported_path = model.export(format="engine", half=True, device=0)
            logger.info("Successfully exported TensorRT model to %s", exported_path)
        else:
            logger.info("TensorRT model already exists at %s", engine_path)
    else:
        logger.info("CUDA not available. Exporting %s to OpenVINO format...", weights)
        openvino_dir = MODELS_DIR / f"{stem}_openvino_model"
        if not openvino_dir.exists():
            exported_path = model.export(format="openvino")
            logger.info("Successfully exported OpenVINO model to %s", exported_path)
        else:
            logger.info("OpenVINO model already exists at %s", openvino_dir)


def export_all_models(models=("yolo11n.pt", "yolo11n-pose.pt")):
    for weights in models:
        export_single_model(weights)


def export_optimized_model(weights="yolo11n-pose.pt"):
    export_single_model(weights)


if __name__ == "__main__":
    export_all_models()


