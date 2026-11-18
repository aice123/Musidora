#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
face_similarity.py —— 用 ArcFace 余弦相似度判断两张图是否是同一个人

用法：
    python face_similarity.py 参考脸.jpg 生成图.png [更多图...]

输出：
    每张图与参考脸的相似度（0~1）
    判定：>0.45 同一人 / 0.30~0.45 有点像 / <0.30 不是同一人

依赖：insightface、opencv-python、numpy、onnxruntime
    pip install insightface opencv-python numpy onnxruntime
"""
import os
import sys

import numpy as np

THRESHOLD_SAME = 0.45
THRESHOLD_SIMILAR = 0.30


def load_app(models_root=None):
    from insightface.app import FaceAnalysis
    kwargs = {"name": "buffalo_l", "providers": ["CPUExecutionProvider"]}
    if models_root:
        kwargs["root"] = models_root
    app = FaceAnalysis(**kwargs)
    app.prepare(ctx_id=-1, det_size=(640, 640))
    return app


def read_image(path):
    import cv2
    data = np.fromfile(path, dtype=np.uint8)      # 支持中文路径
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def embedding(app, path):
    img = read_image(path)
    if img is None:
        raise RuntimeError("读不到图片: %s" % path)
    faces = app.get(img)
    if not faces:
        return None
    f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
    e = f.normed_embedding.astype(np.float32)
    return e / np.linalg.norm(e)


def verdict(sim):
    if sim >= THRESHOLD_SAME:
        return "同一人"
    if sim >= THRESHOLD_SIMILAR:
        return "有点像"
    return "不是同一人"


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    ref_path, targets = argv[1], argv[2:]
    app = load_app(os.environ.get("INSIGHTFACE_ROOT"))
    ref = embedding(app, ref_path)
    if ref is None:
        print("参考图里没检测到人脸:", ref_path)
        return 1
    print("参考脸: %s" % os.path.basename(ref_path))
    print("-" * 58)
    for t in targets:
        e = embedding(app, t)
        if e is None:
            print("%-36s 没检测到人脸" % os.path.basename(t)[:36])
            continue
        sim = float(np.dot(ref, e))
        print("%-36s %.3f  %s" % (os.path.basename(t)[:36], sim, verdict(sim)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))