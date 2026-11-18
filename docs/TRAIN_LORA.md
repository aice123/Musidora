# 训练自己的人脸 LoRA

目标：用 5~20 张同一人的照片，训练一个可在 ComfyUI 里加载的 LoRA，让模型记住这张脸。

脚本：`scripts/train_face_lora.py`

---

## 一、准备数据

| 项 | 建议 |
|---|---|
| 数量 | 5~20 张（少于 5 张容易过拟合，多于 20 张收益递减） |
| 角度 | 正脸 3~5 张 + 半侧 3~5 张 + 侧面 2~3 张 |
| 光线 | 至少两种（顺光 / 侧光），避免全是同一环境 |
| 清晰度 | 人脸区域 ≥ 300 px 宽；模糊的图会显著拉低质量 |
| 内容 | 只放**同一个主体**；混入其他人会让 LoRA 学到“平均脸” |
| 权利 | 必须是你可以合法使用的照片 |

放置：

```
dataset/
├── 01.jpg
├── 02.jpg
└── ...
```

---

## 二、跑训练

```bash
python scripts/train_face_lora.py \
  --src dataset \
  --name my_face \
  --steps 700 \
  --per-image 40 \
  --rank 16 \
  --lr 1e-4 \
  --face-crop
```

| 参数 | 说明 |
|---|---|
| `--src` | 照片目录 |
| `--name` | 输出 LoRA 名字（生成 `my_face.safetensors`） |
| `--steps` | 总步数，默认 ≈ `--per-image × 图片数` |
| `--per-image` | 每张图重复训练多少次；10~40 之间 |
| `--rank` | LoRA 秩，8（轻）/ 16（推荐）/ 32（重，易过拟合） |
| `--lr` | 学习率，1e-4 稳妥 |
| `--face-crop` | 自动裁出人脸区域再训练（**强烈建议开**，否则背景信息会污染） |

显存参考（8 GB）：rank 16、512×512、batch 1、开梯度检查点 → 约 4~6 GB，30 分钟左右。

---

## 三、脚本做了什么

1. **裁脸**：用 insightface 检测人脸，按 bbox 外扩 1.4 倍裁切并缩放到 512×512
2. **自动打标**：按人脸朝向生成标签（`front view` / `three quarter view` / `side view` / `profile`），让模型区分角度
3. **注入 LoRA**：只对 UNet 注意力层 `to_q / to_k / to_v / to_out.0` 加低秩分支
4. **fp32 训练**：fp16 会出现 NaN，必须用 fp32（速度换稳定）
5. **导出**：按 kohya 规范重命名键名，ComfyUI 的 `LoraLoader` 可直接读取

## 四、关键实现细节（自己写训练脚本时容易踩）

### 1. 键名格式必须是 kohya 规范

```python
# 错的（加载后不报错但完全无效）
"unet.down_blocks.0.attentions.0.to_q.lora_down.weight"
"lora_unet_down_blocks_0_attentions_0_to_q_lora_down_weight"

# 对的
"lora_unet_down_blocks_0_attentions_0_to_q.lora_down.weight"
```

判断标准：ComfyUI 加载后，用同一提示词对比开关 LoRA 的两张图，**必须有可见差异**；没差异就是键名没被识别。

### 2. 必须用 fp32

```python
unet = unet.to(torch.float32)   # fp16 训练会在几百步后出现 NaN loss
```

### 3. 梯度检查点省显存

```python
unet.enable_gradient_checkpointing()
```

8 GB 显卡上这是能不能训起来的关键。

### 4. 只训 UNet 注意力层

全参数微调不可行（显存、时间都不够）。LoRA 只加在 attention 的 q/k/v/out 上就足够学到面部特征。

### 5. 输出 metadata 要写对

```python
metadata = {
    "ss_network_module": "networks.lora",
    "ss_network_dim": str(rank),
    "ss_network_alpha": str(rank),
}
```

缺了这两个字段，某些加载器会按 rank=1 处理，效果等于没加载。

---

## 五、验证效果

```bash
# 1. 固定种子，同一提示词出两张：一张不带 LoRA，一张带
python gen.py --prompt "portrait of a woman, studio light" --seed 1234 --lora my_face:0.6

# 2. 用相似度脚本量一下
python scripts/face_similarity.py dataset/01.jpg output.png
```

参考经验值（同一张参考脸）：

| 配置 | 相似度 |
|---|---|
| 不带 LoRA | 0.15~0.25（随机脸） |
| LoRA 权重 0.4 | 0.45~0.50 |
| LoRA 权重 0.6 | 0.53~0.55 |
| LoRA 0.6 + IP-Adapter FaceID 0.8 | 0.73 左右 |

---

## 六、常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| 训完完全没效果 | LoRA 键名不兼容 | 检查键名是否含 `.lora_down.weight` |
| 几百步后 loss 变 NaN | 用了 fp16 | 改 fp32 |
| 出图糊成一团 | rank 太高 + 步数太多（过拟合） | rank 降到 8、步数减半 |
| 只会出正脸 | 训练集角度单一 | 补侧面照片，或调高 `three quarter view` 标签权重 |
| 背景也跟着变 | 没开 `--face-crop` | 开启裁脸 |
| 换了提示词就不像了 | LoRA 权重太低 | 从 0.6 往上试到 0.8 |

---

## 七、许可证说明

你用自己的照片训出来的 LoRA **可以自由分发**（前提是照片你有权使用）。

但如果训练数据里包含他人肖像，分发前需要取得本人同意——这属于肖像权范畴，不是模型许可证能覆盖的。
