"""
Zero-DCE++ (Zero-Reference Deep Curve Estimation) & CLAHE Low-Light Enhancement.

Implements lightweight deep-learning illumination enhancement alongside an
optimized CPU/LAB-space CLAHE fallback pipeline to illuminate dark video streams
before YOLO detection.
"""

import os
import cv2
import numpy as np
import torch
import torch.nn as nn


class CConv(nn.Module):
    """Depthwise separable convolution block for Zero-DCE++."""
    def __init__(self, in_ch, out_ch):
        super(CConv, self).__init__()
        self.depth_conv = nn.Conv2d(in_ch, in_ch, kernel_size=3, padding=1, groups=in_ch, bias=True)
        self.point_conv = nn.Conv2d(in_ch, out_ch, kernel_size=1, bias=True)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        return self.relu(self.point_conv(self.depth_conv(x)))


class ZeroDCE_Net(nn.Module):
    """Zero-DCE++ network architecture (~10K parameters)."""
    def __init__(self, scale_factor=1):
        super(ZeroDCE_Net, self).__init__()
        self.scale_factor = scale_factor
        self.e_conv1 = CConv(3, 32)
        self.e_conv2 = CConv(32, 32)
        self.e_conv3 = CConv(32, 32)
        self.e_conv4 = CConv(32, 32)
        self.e_conv5 = CConv(64, 32)
        self.e_conv6 = CConv(64, 32)
        self.e_conv7 = nn.Conv2d(64, 24, kernel_size=1, bias=True)
        self.tanh = nn.Tanh()

    def forward(self, x):
        if self.scale_factor != 1:
            x_down = nn.functional.interpolate(x, scale_factor=1.0/self.scale_factor, mode='bilinear', align_corners=False)
        else:
            x_down = x

        x1 = self.e_conv1(x_down)
        x2 = self.e_conv2(x1)
        x3 = self.e_conv3(x2)
        x4 = self.e_conv4(x3)
        x5 = self.e_conv5(torch.cat([x3, x4], 1))
        x6 = self.e_conv6(torch.cat([x2, x5], 1))
        x_r = self.tanh(self.e_conv7(torch.cat([x1, x6], 1)))

        if self.scale_factor != 1:
            x_r = nn.functional.interpolate(x_r, size=(x.shape[2], x.shape[3]), mode='bilinear', align_corners=False)

        r = torch.split(x_r, 3, dim=1)
        x = x + r[0] * (torch.pow(x, 2) - x)
        x = x + r[1] * (torch.pow(x, 2) - x)
        x = x + r[2] * (torch.pow(x, 2) - x)
        x = x + r[3] * (torch.pow(x, 2) - x)
        x = x + r[4] * (torch.pow(x, 2) - x)
        x = x + r[5] * (torch.pow(x, 2) - x)
        x = x + r[6] * (torch.pow(x, 2) - x)
        x_enhance = x + r[7] * (torch.pow(x, 2) - x)
        return torch.clamp(x_enhance, 0.0, 1.0)


class ZeroDCEEnhancer:
    """
    Manages low-light video enhancement.
    - Initialized once per worker pipeline.
    - Runs inference with torch.no_grad() and float16 on GPU.
    - Seamlessly falls back to adaptive CLAHE in LAB space if weights are not present.
    """
    def __init__(self, weights_path="models/Zero_DCE_PP.pth", device=None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.use_half = (self.device == "cuda")
        self.has_weights = False
        self.clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))

        self.model = ZeroDCE_Net().to(self.device)
        if self.use_half:
            self.model = self.model.half()
        self.model.eval()

        if weights_path:
            from pathlib import Path
            weights_file = Path(weights_path)
            if not weights_file.exists():
                try:
                    weights_file.parent.mkdir(parents=True, exist_ok=True)
                    url = "https://github.com/Li-Chongyi/Zero-DCE_extension/raw/main/Zero-DCE%2B%2B/snapshots_Zero_DCE%2B%2B/Epoch99.pth"
                    print(f"[Zero-DCE++] Weights not found locally. Downloading from {url}...")
                    import urllib.request
                    urllib.request.urlretrieve(url, str(weights_file))
                    print(f"[Zero-DCE++] Weights downloaded successfully to {weights_file}")
                except Exception as e:
                    print(f"[Zero-DCE++] Failed to download weights automatically: {e}")

        if weights_path and os.path.isfile(weights_path):
            try:
                state_dict = torch.load(weights_path, map_location=self.device)
                self.model.load_state_dict(state_dict)
                self.has_weights = True
                print(f"[Zero-DCE++] Successfully loaded weights from {weights_path}")
            except Exception as e:
                print(f"[Zero-DCE++] Failed to load weights: {e}, falling back to CLAHE")

    def enhance(self, frame_bgr):
        """Enhances illumination on a BGR image."""
        if frame_bgr is None:
            return None

        if self.has_weights:
            return self._enhance_dce(frame_bgr)
        else:
            return self._enhance_clahe(frame_bgr)

    def _enhance_dce(self, frame_bgr):
        with torch.no_grad():
            img_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            img_tensor = torch.from_numpy(img_rgb).permute(2, 0, 1).unsqueeze(0).float() / 255.0
            img_tensor = img_tensor.to(self.device)
            if self.use_half:
                img_tensor = img_tensor.half()

            enhanced = self.model(img_tensor)

            enhanced_np = (enhanced.squeeze(0).permute(1, 2, 0).cpu().float().numpy() * 255.0)
            enhanced_np = np.clip(enhanced_np, 0, 255).astype(np.uint8)
            return cv2.cvtColor(enhanced_np, cv2.COLOR_RGB2BGR)

    def _enhance_clahe(self, frame_bgr):
        """High-speed CPU low-light enhancement using CLAHE in LAB space with gamma correction."""
        lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l_enhanced = self.clahe.apply(l)
        enhanced_lab = cv2.merge((l_enhanced, a, b))
        enhanced_bgr = cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)

        # Apply subtle gamma lift for deep dark shadow regions
        mean_lum = np.mean(l)
        if mean_lum < 60:
            gamma = 1.3
            inv_gamma = 1.0 / gamma
            table = np.array([((i / 255.0) ** inv_gamma) * 255 for i in range(256)]).astype("uint8")
            enhanced_bgr = cv2.LUT(enhanced_bgr, table)

        return enhanced_bgr
