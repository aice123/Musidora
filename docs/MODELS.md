# 模型清单与下载

仓库**不包含任何模型权重**，需自行下载放进 ComfyUI 的 `models/` 对应目录。

> ⚠️ `inswapper_*.onnx` 属 InsightFace 系模型，官方授权为**非商业研究用途**。商用请自行取得授权。

## 必需

| 用途 | 文件 | 大小 | 放置位置 | 来源 |
|---|---|---|---|---|
| 出图主模型（SD1.5 写实） | `epicphotogasm_v1.safetensors` | 2.0 GB | `models/checkpoints/` | [Civitai 搜索 epicphotogasm](https://civitai.com/search/models?query=epicphotogasm)（需登录） |
| VAE | `vae-ft-mse-840000-ema-pruned.safetensors` | 319 MB | `models/vae/` | [HuggingFace](https://huggingface.co/stabilityai/sd-vae-ft-mse-original) |
| 特征迁移模型 | `inswapper_128.onnx` | 529 MB | `models/insightface/` | [HuggingFace deepinsight/inswapper](https://huggingface.co/deepinsight/inswapper)（需同意条款） |
| 脸部修复 | `GFPGANv1.4.pth` | 332 MB | `models/facerestore_models/` | [GitHub releases](https://github.com/TencentARC/GFPGAN/releases) |
| 人脸检测 | `detection_Resnet50_Final.pth` | 104 MB | `models/facedetection/` | 装 ReActor 节点时自动下载 |
| 人脸解析 | `parsing_parsenet.pth` | 81 MB | `models/facedetection/` | 同上 |

## 耦合页额外需要

| 用途 | 文件 | 大小 | 位置 | 来源 |
|---|---|---|---|---|
| 脸部 LoRA | 自训或他人训练 | 6~50 MB | `models/loras/` | 自训见 [TRAIN_LORA.md](TRAIN_LORA.md) |
| IPAdapter FaceID | `ip-adapter-faceid-plusv2_sd15.bin` | 149 MB | `models/ipadapter/` | [HuggingFace h94/IP-Adapter-FaceID](https://huggingface.co/h94/IP-Adapter-FaceID) |
| IPAdapter FaceID LoRA | `ip-adapter-faceid-plusv2_sd15_lora.safetensors` | 49 MB | `models/loras/` | 同上（同仓库内） |
| 视觉编码器 | `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` | 2.4 GB | `models/clip_vision/` | [HuggingFace](https://huggingface.co/lambdalabs/sd-image-variations-diffusers/tree/main/image_encoder) |

## 可选

| 用途 | 文件 | 大小 | 说明 |
|---|---|---|---|
| 保真修复 | `codeformer-v0.1.0.pth` | 359 MB | [GitHub releases](https://github.com/sczhou/CodeFormer/releases)。实测与 GFPGAN 差异极小，可不装 |
| 放大模型 | 任意 ESRGAN 系 | 60~70 MB | 想只放大不重绘时用；效果不如二次重绘 |

## 翻译模型（Ollama）

```bash
ollama pull qwen3:8b          # 推荐：8 GB 显存下的上限
ollama pull qwen3:4b          # 更快，质量略降
ollama pull qwen2.5:7b        # 中文同样不错
```

配置里把 `model` 改成对应名字即可。

## 国内下载建议

GitHub 直连不稳定，断在 90% 会留下残缺文件（本文档作者就踩过：10 MB 的 CodeFormer 被当成完整文件，运行时报 `PytorchStreamReader failed reading zip archive`）。

优先顺序：

1. 绘世/Aki 启动器自带的模型下载（有镜像和续传）
2. `https://ghfast.top/` + 原始 GitHub 链接（实测 1.8 MB/s）
3. `https://gh-proxy.com/` + 原始链接（约 0.3 MB/s）
4. 直连 GitHub（约 0.05 MB/s，容易断）

命令行续传示例：

```powershell
curl.exe -L --retry 5 -C - -o "模型路径" "https://ghfast.top/https://github.com/..."
```

**下完必须核对大小**，与上表差距太大就是断了。
