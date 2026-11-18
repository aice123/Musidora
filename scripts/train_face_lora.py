#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
train_face_lora.py - 只有 1~3 张同人图时，用自动增强扩样后训一个 SD1.5 人脸 LoRA
用 ComfyUI 自带 python 跑（diffusers 0.34 / peft 0.17 / accelerate 已装）:

  python train_face_lora.py --src refs\face --name myface --steps 400
  产物: D:\comfyui\ComfyUI-aki-v2\ComfyUI\models\loras\<name>.safetensors

增强策略(1 张 → 16 张): 水平镜像 / 三档裁切缩放 / 三档亮度 / 两档轻微旋转。
训练: UNet attention 上的 LoRA(rank 8), 512px, batch 1, fp16, 梯度检查点。
导出: 把 peft 的 diffusers 键名转换成 ComfyUI 能直接读的 kohya 键名。
"""
import argparse
import os, glob, json, math, os, random, sys

import torch
from PIL import Image, ImageEnhance
from torch.utils.data import Dataset, DataLoader


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 数据增强
def augment(im, n=16, size=512):
    out = []
    w, h = im.size
    side = min(w, h)
    for i in range(n):
        img = im.copy()
        if i % 4 == 1:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        # 裁切缩放: 92% / 100% / 108% / 84%
        scale = [1.0, 0.92, 1.08, 0.84][i % 4]
        cw, ch = int(side * scale), int(side * scale)
        x = random.randint(0, max(0, w - cw))
        y = random.randint(0, max(0, h - ch))
        img = img.crop((x, y, x + cw, y + ch)).resize((size, size), Image.LANCZOS)
        # 亮度 / 对比度
        img = ImageEnhance.Brightness(img).enhance([1.0, 0.9, 1.1][i % 3])
        img = ImageEnhance.Contrast(img).enhance([1.0, 1.06][i % 2])
        if i % 5 == 0:
            img = img.rotate(random.uniform(-4, 4), resample=Image.BICUBIC)
        out.append(img.convert("RGB"))
    return out


class FaceDS(Dataset):
    def __init__(self, images, captions):
        self.images = images
        self.captions = captions

    def __len__(self):
        return len(self.images)

    def __getitem__(self, i):
        px = torch.from_numpy(
            (torch.ByteTensor(torch.ByteStorage.from_buffer(self.images[i].tobytes()))
             .float().div(255.0).numpy())
        ).reshape(512, 512, 3).permute(2, 0, 1) * 2 - 1
        return {"pixel_values": px, "caption": self.captions[i]}


def keys_to_kohya(sd):
    """peft(diffusers) 键 -> kohya 键, 供 ComfyUI 的 LoraLoader 读取"""
    MAP = {
        "attn1.to_q": "attn1_to_q", "attn1.to_k": "attn1_to_k",
        "attn1.to_v": "attn1_to_v", "attn1.to_out.0": "attn1_to_out_0",
        "attn2.to_q": "attn2_to_q", "attn2.to_k": "attn2_to_k",
        "attn2.to_v": "attn2_to_v", "attn2.to_out.0": "attn2_to_out_0",
        "ff.net.0.proj": "ff_net_0_proj", "ff.net.2": "ff_net_2",
        "proj_in": "proj_in", "proj_out": "proj_out",
        "attn1.to_out": "attn1_to_out", "attn2.to_out": "attn2_to_out",
    }
    out = {}
    for k, v in sd.items():
        k2 = k
        for pre in ("base_model.model.", "base_model.", "unet."):
            if k2.startswith(pre):
                k2 = k2[len(pre):]
                break
        _is_down = k2.endswith("lora_A.weight")
        k2 = k2.replace(".lora_A.weight", "").replace(".lora_B.weight", "")
        if k2.endswith(".alpha") or k2.endswith("alpha"):
            continue
        for a, b in sorted(MAP.items(), key=lambda x: -len(x[0])):
            k2 = k2.replace(a, b)
        k2 = k2.replace(".", "_")
        out["lora_unet_" + k2 + (".lora_down.weight" if _is_down else ".lora_up.weight")] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="放源图的目录")
    ap.add_argument("--name", default="myface")
    ap.add_argument("--ckpt", default="", help="底模路径（必填），如 ComfyUI/models/checkpoints/xxx.safetensors")
    ap.add_argument("--steps", type=int, default=400)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=8)
    ap.add_argument("--per-image", type=int, default=16)
    ap.add_argument("--token", default="ohwx woman")
    ap.add_argument("--face-crop", action="store_true", help="训练前先裁到脸+肩, 避免身材被学进去")
    ap.add_argument("--out-dir", default="loras", help="输出目录，默认 ./loras")
    a = ap.parse_args()
    if not a.ckpt:
        ap.error("请用 --ckpt 指定底模路径，例如 --ckpt ComfyUI/models/checkpoints/model.safetensors")
    if not os.path.exists(a.ckpt):
        ap.error("底模不存在: %s" % a.ckpt)

    from diffusers import StableDiffusionPipeline
    from peft import LoraConfig, get_peft_model, get_peft_model_state_dict
    from safetensors.torch import save_file

    files = [p for p in glob.glob(os.path.join(a.src, "*")) if p.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
    if not files:
        raise SystemExit("源图目录里没有图片: " + a.src)
    imgs = []
    _crop = None
    if a.face_crop:
        import cv2
        from insightface.app import FaceAnalysis
        _app = FaceAnalysis(name="buffalo_l",
                            root=r"D:\comfyui\ComfyUI-aki-v2\ComfyUI\models\insightface",
                            providers=["CPUExecutionProvider"])
        _app.prepare(ctx_id=-1, det_size=(640, 640))

        def _crop(path):
            arr = cv2.imread(path)
            fs = _app.get(arr)
            if not fs:
                log("  未检测到人脸, 原图使用:", os.path.basename(path))
                return Image.open(path).convert("RGB")
            f = max(fs, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
            x1, y1, x2, y2 = [int(v) for v in f.bbox]
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            w, h = int((x2 - x1) * 1.9), int((y2 - y1) * 2.2)
            x0, y0 = max(cx - w // 2, 0), max(cy - h // 2, 0)
            crop = arr[y0:min(y0 + h, arr.shape[0]), x0:min(x0 + w, arr.shape[1])]
            log("  裁脸 %s -> %dx%d" % (os.path.basename(path), crop.shape[1], crop.shape[0]))
            return Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))

    caps = []
    for f in files:
        base_img = _crop(f) if _crop else Image.open(f).convert("RGB")
        # 按脸朝向自动写 caption, 让 LoRA 学会"角度 -> 同一个人的不同朝向"
        angle = "front view"
        if a.face_crop:
            arr = cv2.imread(f)
            fs = _app.get(arr)
            if fs:
                fa = max(fs, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
                yaw = abs(float(fa.pose[1]))
                if yaw < 10:
                    angle = "front view"
                elif yaw < 25:
                    angle = "three quarter view"
                elif yaw < 40:
                    angle = "side view"
                else:
                    angle = "profile view"
                log("  %s  yaw=%.1f -> %s" % (os.path.basename(f), float(fa.pose[1]), angle))
        cap = "%s, %s" % (a.token, angle)
        batch = augment(base_img, a.per_image)
        imgs += batch
        caps += [cap] * len(batch)
    log("源图 %d 张 -> 增强后 %d 张, 512x512" % (len(files), len(imgs)))

    log("加载底模:", os.path.basename(a.ckpt))
    pipe = StableDiffusionPipeline.from_single_file(
        a.ckpt, torch_dtype=torch.float16, safety_checker=None, requires_safety_checker=False)
    pipe.to("cuda")
    pipe.unet.to(torch.float32)   # fp16 反传会出 nan
    pipe.unet.enable_gradient_checkpointing()
    pipe.vae.requires_grad_(False)
    pipe.text_encoder.requires_grad_(False)
    pipe.unet.requires_grad_(False)

    cfg = LoraConfig(r=a.rank, lora_alpha=a.rank, init_lora_weights="gaussian",
                     target_modules=["to_q", "to_k", "to_v", "to_out.0"])
    pipe.unet = get_peft_model(pipe.unet, cfg)
    for _n, _p in pipe.unet.named_parameters():
        _p.data = _p.data.float()          # peft 新增层跟随原 dtype, 这里统一转 fp32
    for _n, _b in pipe.unet.named_buffers():
        if _b.is_floating_point():
            _b.data = _b.data.float()
    pipe.unet.to("cuda")

    params = [p for p in pipe.unet.parameters() if p.requires_grad]
    log("可训练参数: %.2f M" % (sum(p.numel() for p in params) / 1e6))
    opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=1e-2)

    ds = FaceDS(imgs, caps)
    dl = DataLoader(ds, batch_size=1, shuffle=True, num_workers=0)
    pipe.scheduler.set_timesteps(1000)

    pipe.unet.train()
    step = 0
    t0 = __import__("time").time()
    while step < a.steps:
        for batch in dl:
            px = batch["pixel_values"].to("cuda", dtype=torch.float16)
            with torch.no_grad():
                lat = pipe.vae.encode(px).latent_dist.sample() * pipe.vae.config.scaling_factor
                txt = pipe.tokenizer([batch["caption"][0]], padding="max_length",
                                     max_length=pipe.tokenizer.model_max_length,
                                     truncation=True, return_tensors="pt")
                emb = pipe.text_encoder(txt.input_ids.to("cuda"))[0]
            noise = torch.randn_like(lat)
            ts = torch.randint(0, 1000, (1,), device="cuda")
            noisy = pipe.scheduler.add_noise(lat, noise, ts)
            pred = pipe.unet(noisy.float(), ts, encoder_hidden_states=emb.float()).sample
            loss = torch.nn.functional.mse_loss(pred.float(), noise.float())
            loss.backward()
            opt.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            if step % 20 == 0 or step == 1:
                el = __import__("time").time() - t0
                log("step %d/%d  loss %.4f  %.1fs  (%.2f s/step)"
                    % (step, a.steps, float(loss), el, el / step))
            if step >= a.steps:
                break

    os.makedirs(a.out_dir, exist_ok=True)
    out = os.path.join(a.out_dir, a.name + ".safetensors")
    from peft import get_peft_model_state_dict
    raw = get_peft_model_state_dict(pipe.unet)
    log("peft 参数键数:", len(raw))
    if raw:
        log("  样例键:", list(raw.keys())[:2])
    sd = {k: v.detach().to("cpu", dtype=torch.float16) for k, v in raw.items()}
    kohya = keys_to_kohya(sd)
    log("kohya 键数:", len(kohya))
    save_file(kohya, out, metadata={"ss_network_dim": str(a.rank), "ss_network_alpha": str(a.rank)})
    log("LoRA 已保存:", out, "| 键数:", len(kohya), "| 大小: %.1f MB" % (os.path.getsize(out) / 1e6))


if __name__ == "__main__":
    main()
