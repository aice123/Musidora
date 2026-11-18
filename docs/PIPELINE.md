# 让生成的角色“像同一个人”：三条路线

目标：同一张脸，出现在不同场景、不同姿势、不同服装的图里。

这件事有三条技术路线，效果、成本、许可证各不相同。本文说明原理与选择依据，具体获取方式见 [MODELS.md](MODELS.md)。

---

## 路线对比

| | A. IP-Adapter FaceID | B. 人脸 LoRA | C. 人脸特征迁移（inswapper 系） |
|---|---|---|---|
| 原理 | 把参考人脸的 embedding 注入生成过程 | 用少量照片微调模型，让它“学会”这张脸 | 生成完再把脸替换掉 |
| 数据需求 | 1 张人脸图 | 5~20 张同一人的照片 | 1 张人脸图 + 目标图 |
| 训练耗时 | 无 | 30 分钟~2 小时（8 GB 显存） | 无 |
| 相似度（ArcFace 余弦） | 0.70~0.78 | 0.50~0.55（单独用） | 0.83~0.90 |
| 生成阶段是否可控 | ✅ 姿势/构图/服装都能改 | ✅ 同左 | ❌ 只能改已有照片 |
| 许可证 | Apache-2.0，可公开 | 自己训练的产物，可公开 | ❌ 非商用、不可再分发 |
| 适合 | 通用场景 | 长期固定同一角色 | 处理已有照片 |

> 相似度参考阈值：**>0.45 视为同一人**，0.30~0.45 只是“有点像”，<0.30 基本是另一个人。

---

## A. IP-Adapter FaceID：生成时锁身份

### 节点链

```
LoadImage(参考人脸)
    ├─→ IPAdapterInsightFaceLoader  ─┐
    └─→ (image)                      ├→ IPAdapterFaceID → KSampler.model
CheckpointLoader ──→ IPAdapterUnifiedLoaderFaceID ─┘
```

### 关键参数

| 参数 | 建议值 | 说明 |
|---|---|---|
| `weight` | 0.6~0.9 | 身份强度。太高会让所有图都长一个样、姿势僵硬 |
| `weight_faceidv2` | 1.0 | 配合 Plus V2 模型时的第二权重 |
| `weight_type` | `linear` / `ease in` | 影响强度在采样过程中的分布 |
| `start_at` / `end_at` | 0.0 / 1.0 | 只在前半程注入身份，后半程放开细节，会更自然 |

### 配合 LoRA 的增益

实测（同一张参考脸、同一提示词）：

```
仅 LoRA            → 0.535
仅 IP-Adapter      → 0.70 左右
LoRA + IP-Adapter  → 0.734      ← 两者互补
再叠加人脸特征迁移          → 0.83~0.90  （路线 C，不可公开分发）
```

---

## B. 人脸 LoRA：自己训练

完整步骤见 [TRAIN_LORA.md](TRAIN_LORA.md)。核心要点：

1. 准备 5~20 张同一人的照片（正脸 / 半侧 / 侧面 / 不同光线）
2. 自动裁脸、按角度生成标签（front / three quarter / side / profile）
3. UNet 注意力层的 `to_q / to_k / to_v / to_out.0` 加 LoRA（rank 8~32）
4. **必须 fp32 训练**（fp16 会 NaN），开梯度检查点省显存
5. 导出成 kohya 格式键名（`lora_unet_*.lora_down.weight`），ComfyUI 可直接加载

训练产物只有 4~50 MB，可以随仓库分发——**这是唯一"能被公开分发"的身份方案**（前提是训练数据你有权使用）。

---

## C. 人脸特征迁移：效果最好但有许可证限制

ReActor / inswapper 系节点的工作方式：

```
检测目标图人脸（5 点关键点）
  → 对齐成 128×128
  → 用人脸 embedding 重建像素
  → 按原角度贴回
  → GFPGAN 修复 + 与原图混合
```

### 为什么相似度更高

它直接在**像素层面**替人脸特征迁移，不经过扩散采样，所以不会“被提示词带偏”；代价是分辨率受限于 128×128，必须靠修复模型补细节。

### 为什么不能公开发布

`inswapper_128.onnx` / `buffalo_l` 属 InsightFace 的**非商用、不可再分发**权重，且并非公开授权发布。GitHub 曾据此对同类项目发出 DMCA 下架。

因此本仓库：

- 不包含、不链接、不写进自动下载清单
- 只在此文档中说明**技术原理**，便于你了解与自建

如果你需要这条能力，请自行按其许可条款获取，并**仅用于本机个人用途**。

---

## 调参经验（踩过的坑）

| 现象 | 原因 | 处理 |
|---|---|---|
| 特征迁移后相似度 0.0 | 人脸识别模型是伪造/损坏的 `buffalo_l`（`w600k_r50.onnx` 被替换成别的模型） | 换成真正的 ArcFace `w600k_r50.onnx` |
| 特征迁移结果全黑 | 节点内置 NSFW 检测命中后返回空图 | 换模型或调整内容，不要在代码里绕过检测 |
| LoRA 加载了但完全无效 | 键名格式不对（`..._lora_down_weight` vs `...lora.down.weight`） | 按 kohya 规范导出 |
| LoRA 训练后相似度反而下降 | 训练集混了不同人的照片 | 只用一个主体的图 |
| 生成出来是绿色噪点 | fp16 精度溢出（常见于 SDXL） | 加 `--force-fp32` |
| 脸越修越不像 | 修复强度过高，把特征磨平了 | 修复强度 0.35 左右，别超过 0.6 |

---

## 判断“像不像”的方法

别靠肉眼，用数字：

```bash
python scripts/face_similarity.py 参考脸.jpg 生成的图.png
```

输出 ArcFace 余弦相似度：

```
0.83  ← 同一人，肉眼也认得出
0.55  ← 有点像，气质接近
0.25  ← 完全不是一个人
```

自己搭的话也可以用 insightface：

```python
from insightface.app import FaceAnalysis
app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
app.prepare(ctx_id=-1, det_size=(640, 640))
e1 = app.get(img1)[0].normed_embedding
e2 = app.get(img2)[0].normed_embedding
sim = float(e1 @ e2 / (np.linalg.norm(e1) * np.linalg.norm(e2)))
```
