"""
Cross-Camera Re-Identification (Re-ID) Module.

Provides:
- TargetEmbedder: computes visual feature vectors from cropped target images.
  Uses a lightweight MobileNetV2-based backbone when PyTorch is available,
  with an automatic HSV color histogram fallback.
- GlobalTargetRegistry: in-memory registry mapping feature vectors to persistent
  global IDs across all camera feeds.

Performance Rule:
  Embedding extraction is designed to be called SPARINGLY — only on a target's
  first appearance or every N frames (see tracker.py REID_INTERVAL). The caller
  is responsible for throttling; this module always computes when asked.
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)

# ───────────────────────── Feature Extraction ──────────────────────────

_TORCH_AVAILABLE = False
try:
    import torch
    import torch.nn as nn
    _TORCH_AVAILABLE = True
except ImportError:
    logger.info("PyTorch not found — Re-ID will use HSV histogram fallback.")


class _MiniEmbedNet(nn.Module if _TORCH_AVAILABLE else object):
    """Ultra-lightweight embedding network (~0.5M params).

    Architecture: MobileNetV2 feature extractor (first 8 bottleneck blocks)
    followed by global average pooling and a 128-d L2-normalized embedding.
    """

    def __init__(self):
        if not _TORCH_AVAILABLE:
            return
        super().__init__()
        from torchvision.models import mobilenet_v2, MobileNet_V2_Weights
        backbone = mobilenet_v2(weights=MobileNet_V2_Weights.DEFAULT)
        # Take only early features (lightweight) — up to inverted-residual block 8
        self.features = nn.Sequential(*list(backbone.features[:9]))
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Linear(64, 128)

    def forward(self, x):
        x = self.features(x)
        x = self.pool(x).flatten(1)
        x = self.fc(x)
        # L2 normalize so cosine similarity == dot product
        return x / (x.norm(dim=1, keepdim=True) + 1e-8)


class TargetEmbedder:
    """Computes 128-d feature vectors for cropped person images.

    Falls back to a 180-bin HSV color histogram if PyTorch/torchvision is
    unavailable or GPU resources are constrained.
    """

    def __init__(self, use_gpu=True):
        self._use_nn = False
        if _TORCH_AVAILABLE:
            try:
                self._device = torch.device(
                    "cuda" if use_gpu and torch.cuda.is_available() else "cpu"
                )
                self._model = _MiniEmbedNet().to(self._device).eval()
                if self._device.type == "cuda":
                    self._model = self._model.half()
                self._use_nn = True
                logger.info("Re-ID embedder initialised on %s", self._device)
            except Exception as exc:
                logger.warning("NN embedder init failed (%s), using histogram fallback.", exc)

    # ---- public API ---------------------------------------------------------

    def embed(self, crop_bgr: np.ndarray) -> np.ndarray:
        """Return a 1-D feature vector for *crop_bgr* (H×W×3 uint8 BGR)."""
        if self._use_nn:
            return self._embed_nn(crop_bgr)
        return self._embed_histogram(crop_bgr)

    # ---- neural path --------------------------------------------------------

    def _embed_nn(self, crop_bgr: np.ndarray) -> np.ndarray:
        import cv2
        img = cv2.resize(crop_bgr, (128, 256))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        # Normalise to ImageNet stats
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        img = (img - mean) / std
        tensor = torch.from_numpy(img.transpose(2, 0, 1)).unsqueeze(0).to(self._device)
        if self._device.type == "cuda":
            tensor = tensor.half()
        with torch.no_grad():
            vec = self._model(tensor).cpu().numpy().flatten()
        return vec

    # ---- histogram fallback -------------------------------------------------

    @staticmethod
    def _embed_histogram(crop_bgr: np.ndarray) -> np.ndarray:
        import cv2
        hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
        # 180 hue bins, 32 saturation bins → 212-d vector
        hist_h = cv2.calcHist([hsv], [0], None, [180], [0, 180]).flatten()
        hist_s = cv2.calcHist([hsv], [1], None, [32], [0, 256]).flatten()
        vec = np.concatenate([hist_h, hist_s])
        norm = np.linalg.norm(vec) + 1e-8
        return vec / norm


# ──────────────────────── Global Target Registry ───────────────────────

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


class GlobalTargetRegistry:
    """In-memory registry of cross-camera global identities.

    Each identity has a `global_id`, a running-average feature vector, and the
    camera_id where it was last seen.
    """

    def __init__(self, similarity_threshold=0.70):
        self.similarity_threshold = similarity_threshold
        self._next_id = 1
        # global_id -> {"vec": np.ndarray, "camera": str, "count": int}
        self._identities: dict = {}

    def query(self, feature_vec: np.ndarray, camera_id: str = "") -> int:
        """Match *feature_vec* against known identities.

        Returns the matched `global_id`, or assigns a new one if no match
        exceeds the similarity threshold.  The stored vector is updated as a
        running average to accommodate lighting / angle changes over time.
        """
        best_id, best_sim = None, -1.0
        for gid, entry in self._identities.items():
            sim = _cosine_similarity(feature_vec, entry["vec"])
            if sim > best_sim:
                best_sim = sim
                best_id = gid

        if best_id is not None and best_sim >= self.similarity_threshold:
            # Running-average update of stored vector
            entry = self._identities[best_id]
            n = entry["count"]
            entry["vec"] = (entry["vec"] * n + feature_vec) / (n + 1)
            entry["vec"] /= (np.linalg.norm(entry["vec"]) + 1e-8)  # re-normalise
            entry["count"] = n + 1
            entry["camera"] = camera_id
            return best_id

        # New identity
        gid = self._next_id
        self._next_id += 1
        self._identities[gid] = {
            "vec": feature_vec.copy(),
            "camera": camera_id,
            "count": 1,
        }
        return gid

    def known_ids(self) -> list:
        return list(self._identities.keys())

    def identity_count(self) -> int:
        return len(self._identities)
