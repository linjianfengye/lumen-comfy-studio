# Lumen 生成的 Modal 应用：ComfyUI + 模型存储卷。由 Lumen 后端渲染后执行 `modal deploy`。
# 参考 Modal 官方 ComfyUI 示例：https://modal.com/docs/examples/comfyapp
# 本文件是模板，__CONFIG__ 会被替换成 JSON；也可以手动 `modal deploy` 这个渲染后的文件。
import json
import os
import subprocess

import modal

CONFIG = json.loads(r'''__CONFIG__''')
MODELS_DIR = "/models"
COMFY_DIR = "/root/comfy/ComfyUI"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg", "libgl1", "libglib2.0-0")
    .pip_install(f"comfy-cli=={CONFIG['comfyCli']}", "huggingface_hub[hf_transfer]")
    .run_commands(f"comfy --skip-prompt install --fast-deps --nvidia --version {CONFIG['comfyVersion']}")
)
# 工作流用到的自定义节点包（来自 Comfy Registry 的 cnr_id）
if CONFIG["nodePacks"]:
    image = image.run_commands(f"comfy node install --fast-deps {' '.join(CONFIG['nodePacks'])}")
image = image.env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})

app = modal.App(CONFIG["appName"], image=image)
vol = modal.Volume.from_name(CONFIG["volumeName"], create_if_missing=True)
hf_secret = modal.Secret.from_dict({"HF_TOKEN": os.environ.get("HF_TOKEN", "")})

# 让 ComfyUI 从存储卷读模型
EXTRA_PATHS = f"""lumen:
  base_path: {MODELS_DIR}
  checkpoints: checkpoints
  loras: loras
  vae: vae
  controlnet: controlnet
  diffusion_models: diffusion_models
  unet: diffusion_models
  text_encoders: text_encoders
  clip: text_encoders
  clip_vision: clip_vision
  upscale_models: upscale_models
  style_models: style_models
"""


@app.function(gpu=CONFIG["gpu"], volumes={MODELS_DIR: vol}, max_containers=1,
              scaledown_window=CONFIG["scaledownWindow"], timeout=3600)
@modal.concurrent(max_inputs=10)
@modal.web_server(8000, startup_timeout=300)
def ui():
    with open(f"{COMFY_DIR}/extra_model_paths.yaml", "w") as f:
        f.write(EXTRA_PATHS)
    subprocess.Popen("comfy launch --background -- --listen 0.0.0.0 --port 8000", shell=True)


@app.function(volumes={MODELS_DIR: vol}, secrets=[hf_secret], timeout=3600)
def download_model(url: str, subdir: str, filename: str):
    """把模型下载到存储卷 /models/<subdir>/<filename>，已存在则跳过。"""
    import urllib.request

    dest_dir = f"{MODELS_DIR}/{subdir}"
    os.makedirs(dest_dir, exist_ok=True)
    dest = f"{dest_dir}/{filename}"
    if os.path.exists(dest):
        print("LUMEN_PROGRESS 1.0")
        return "exists"
    req = urllib.request.Request(url)
    token = os.environ.get("HF_TOKEN")
    if token and "huggingface.co" in url:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as r, open(dest + ".part", "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, 0.0
        while chunk := r.read(8 << 20):
            f.write(chunk)
            done += len(chunk)
            if total and done / total - last >= 0.05:
                last = done / total
                print(f"LUMEN_PROGRESS {last:.3f}", flush=True)
    os.rename(dest + ".part", dest)
    vol.commit()
    print("LUMEN_PROGRESS 1.0")
    return "ok"
