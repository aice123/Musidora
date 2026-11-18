# 环境配置（配置阶段的坑都在这）

给 AI 助手的任务清单：照这个顺序做，每步都有验收方法。

## 1. ComfyUI

```
推荐：绘世（Aki）整合包，装完自带 Python、CUDA、常用节点
```

**坑 1：绘世是分离布局，`main.py` 不在根目录**

```
D:\comfyui\ComfyUI-aki-v2\
├── python\python.exe          ← 用它执行
├── ComfyUI\main.py            ← 不在根目录
└── ComfyUI\models\
```

启动命令（工作目录必须是 `ComfyUI\`）：

```powershell
D:\comfyui\ComfyUI-aki-v2\python\python.exe main.py --preview-method none --disable-xformers
```

验收：`http://127.0.0.1:8188/system_stats` 返回 JSON。

**坑 2：`/free` 是异步的**

调用 `POST /free {"unload_models":true,"free_memory":true}` 后，显存要 1~3 秒才真正退回来。立刻加载别的模型会失败或半驻留。必须轮询 `nvidia-smi` 等到稳定。

**坑 3：`/free` 在任务运行中无效**

`/queue` 里 `queue_running` 非空时，模型不会释放。残留任务还会让 GPU 一直被占，表现是"没干任何事但翻译奇慢"。清残留：

```powershell
curl -X POST http://127.0.0.1:8188/interrupt -d "{}"
```

清不掉就重启 ComfyUI 进程（实测有卡死的任务 `/interrupt` 也停不掉）。

## 2. Ollama

```powershell
ollama pull qwen3:8b
```

**坑 4：模型目录必须显式指定**

Ollama 默认装到系统盘用户目录。想放到 D 盘，启动前设环境变量：

```powershell
$env:OLLAMA_MODELS = "D:\ollama\models"
```

这个变量要在**启动 ollama 的那个进程**里设，事后改无效。

**坑 5：启动参数决定一半性能**

```
OLLAMA_FLASH_ATTENTION=1     实测生成 2.8 → 5.3 tok/s
OLLAMA_KV_CACHE_TYPE=q8_0    KV 缓存减半，8 GB 卡必需
OLLAMA_NUM_PARALLEL=1        单并发，不重复分配 KV
OLLAMA_MAX_LOADED_MODELS=1
```

不设这几项，8 GB 卡上跑 8B 模型会明显慢。

**坑 6：`ollama serve` 不弹窗**

用 `subprocess.Popen(..., creationflags=0x08000000)` 隐藏黑窗，否则每次启动软件闪一个 cmd。

## 3. 自定义节点

工作流用到 ReActor，必须先装：

```
ComfyUI-Manager → 搜索 ReActor → 安装 → 重启
```

**坑 7：节点装了但模型没下**

ReActor 首次运行会自动下载 6 个模型（inswapper、GFPGAN、retinaface、parsenet 等）。国内可能超时失败，表现为出图报 `value_not_in_list`。按 [MODELS.md](MODELS.md) 手动放好。

**坑 8：模型名必须和代码里一致**

```
ReActorFaceSwap 节点的 swap_model 参数是枚举，值不在列表里就整体失败：
{"error":{"type":"prompt_outputs_failed_validation", ...}}
```

换模型后要同步改代码或改工作流，两边不能只改一处。

## 4. Python 依赖

```powershell
pip install -r requirements.txt
```

**坑 9：GUI 不需要装任何东西**

Tkinter 随 Python 自带。只有「相似度评分」「人脸计数」需要 `insightface` + `opencv-python`，不装也不影响出图，只是这两个功能会静默跳过。

**坑 10：别用系统 Python 装 GPU 库**

用 ComfyUI 自带的 Python（`ComfyUI-aki-v2\python\python.exe`），它已经配好 CUDA。系统 Python 装 torch 经常版本不匹配。

## 5. 让 AI 助手配置时的检查清单

依次验证，任何一项失败就先修它：

```powershell
# 1 ComfyUI 活着
curl http://127.0.0.1:8188/system_stats
# 2 Ollama 活着且有模型
curl http://127.0.0.1:11434/api/tags
# 3 模型文件齐全（对照 docs/MODELS.md 的大小）
dir D:\comfyui\ComfyUI-aki-v2\ComfyUI\models\insightface
# 4 显存能跑起来
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
# 5 软件能启动
python app.py
```

## 6. 首次运行要点

- `设置.json` 会自动生成，`comfy_dir` 需填对（绘世用户填 `...\ComfyUI-aki-v2`）
- 三个输出目录默认在程序目录下，可改成任意路径
- 第一次出图会比较慢（ComfyUI 要加载模型），属于正常
