# Hosting Qwen on Kaggle (T4 x2) + Cloudflare tunnel

> **Status:** user-supplied script, saved 2026-10-09. Always-on
> OpenAI-compatible LLM endpoint (llama.cpp server, GGUF) + Cloudflare tunnel.
> Needs: Internet ON, GPU T4 x2. Save Version -> Save & Run All (Commit); the
> script never exits, so the commit runs until the 12h limit. Tool calls are
> NOT handled here — the client parses them (see `test/local_kaggle_llm.py`).

Config: `bartowski/Qwen2.5-32B-Instruct-GGUF` / `Qwen2.5-32B-Instruct-Q4_K_M.gguf`
(~20 GB, fits 2x15 GB VRAM), alias `qwen2.5-32b-instruct`, `N_CTX=8192`
(lower to 4096 on OOM), `TENSOR_SPLIT=[0.5, 0.5]`, port 8000.

## Part 1 — install + download

```python
import os, re, sys, json, time, subprocess, requests

REPO_ID = "bartowski/Qwen2.5-32B-Instruct-GGUF"
FILENAME = "Qwen2.5-32B-Instruct-Q4_K_M.gguf"
MODEL_ALIAS = "qwen2.5-32b-instruct"
N_CTX = 8192
TENSOR_SPLIT = [0.5, 0.5]
PORT = 8000
LOCAL = f"http://127.0.0.1:{PORT}"
OUT = "/kaggle/working" if os.path.isdir("/kaggle/working") else "."
MODEL_DIR = "/tmp/models"

def sh(cmd):
    print("$", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True)

sh(f"{sys.executable} -m pip install -q 'llama-cpp-python[server]' "
   "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121 "
   "--prefer-binary")
sh(f"{sys.executable} -m pip install -q huggingface_hub")
sh("wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/"
   "cloudflared-linux-amd64 -O /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared")

from huggingface_hub import hf_hub_download
os.makedirs(MODEL_DIR, exist_ok=True)
model_path = hf_hub_download(repo_id=REPO_ID, filename=FILENAME, local_dir=MODEL_DIR)
print("model:", model_path, flush=True)
subprocess.run("nvidia-smi --query-gpu=name,memory.total --format=csv", shell=True)
```

## Part 2 — server + tunnel helpers

```python
server_proc = None
tunnel_proc = None
public_url = None

def server_alive():
    try:
        return requests.get(f"{LOCAL}/v1/models", timeout=5).ok
    except Exception:
        return False

def start_server():
    global server_proc
    if server_proc and server_proc.poll() is None:
        server_proc.kill()
    subprocess.run(["pkill", "-f", "llama_cpp.server"], check=False)
    time.sleep(2)
    cfg = {"host": "0.0.0.0", "port": PORT, "models": [{
        "model": model_path, "model_alias": MODEL_ALIAS,
        "n_gpu_layers": -1, "n_ctx": N_CTX, "tensor_split": TENSOR_SPLIT,
        "main_gpu": 0, "flash_attn": True, "verbose": True}]}
    cfg_path = f"{OUT}/server_config.json"
    json.dump(cfg, open(cfg_path, "w"), indent=2)
    log = open(f"{OUT}/llama_server.log", "a")
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "llama_cpp.server", "--config_file", cfg_path],
        stdout=log, stderr=subprocess.STDOUT)
    for _ in range(600):
        if server_proc.poll() is not None:
            raise RuntimeError(f"server exited, check {OUT}/llama_server.log")
        if server_alive():
            return
        time.sleep(1)
    raise RuntimeError(f"server failed to start, check {OUT}/llama_server.log")

def warm_model():
    r = requests.post(f"{LOCAL}/v1/chat/completions",
                      json={"model": MODEL_ALIAS, "max_tokens": 8,
                            "messages": [{"role": "user", "content": "hi"}]},
                      timeout=600)
    r.raise_for_status()

def start_tunnel():
    global tunnel_proc, public_url
    if tunnel_proc and tunnel_proc.poll() is None:
        tunnel_proc.kill()
    logpath = f"{OUT}/cloudflared.log"
    open(logpath, "w").close()
    tunnel_proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", LOCAL, "--no-autoupdate"],
        stdout=open(logpath, "a"), stderr=subprocess.STDOUT)
    for _ in range(60):
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", open(logpath).read())
        if m:
            public_url = m.group(0)
            return
        time.sleep(1)
    raise RuntimeError(f"tunnel failed, check {logpath}")
```

## Part 3 — start, watchdog, client usage

```python
def print_usage():
    print("=" * 60)
    print("ENDPOINT READY")
    print("base_url :", public_url + "/v1")
    print("api_key  : anything (e.g. 'sk-local')")
    print("model    :", MODEL_ALIAS)
    print("=" * 60)

start_server()
warm_model()
start_tunnel()
print_usage()

tick = 0
while True:  # never exits — commit runs until the 12h limit
    time.sleep(30)
    tick += 1
    try:
        if not server_alive():
            print(time.strftime("%H:%M:%S"), "server down, restarting", flush=True)
            start_server()
            warm_model()
        if tunnel_proc.poll() is not None:
            print(time.strftime("%H:%M:%S"), "tunnel down, restarting", flush=True)
            start_tunnel()
            print_usage()
    except Exception as e:
        print("watchdog error:", repr(e), flush=True)
    if tick % 10 == 0:
        print(time.strftime("%H:%M:%S"), "alive |", public_url, flush=True)
```

Client side (this repo):

```python
from openai import OpenAI
client = OpenAI(base_url="https://pond-breathing-foto-advocate.trycloudflare.com/v1", api_key="sk-local")
r = client.chat.completions.create(model="qwen2.5-32b-instruct", messages=[{"role":"user","content":"Hello"}])
print(r.choices[0].message.content)
```

Notes: full client-side tool loop in `test/local_kaggle_llm.py` (override URL
via `KAGGLE_LLM_BASE_URL` when the tunnel rotates — URLs change on restart,
model stays `qwen2.5-32b-instruct` on hosted Kaggle T4 x2). Server has no native
tool support — the schema lives in the system prompt (Qwen `<tools>` XML) and
the client parses `<tool_call>` text.
##FULL CODE 
"""
Always-on OpenAI-compatible LLM endpoint on Kaggle using llama.cpp
(llama-cpp-python server, GGUF model) + Cloudflare tunnel.
Needs: Internet ON, GPU accelerator. Run via Save Version -> Save & Run All (Commit).
The script never exits, so the commit keeps running until the 12h limit.
Tool calls are NOT handled here. The client parses them (see client_tool_loop.py).
"""
import os, re, sys, json, time, subprocess, requests

# ---------- config ----------
# Needs accelerator "GPU T4 x2" (2 x 15 GB = 30 GB VRAM). A 32B Q4_K_M model (~20 GB) fits with room for context.
REPO_ID = "bartowski/Qwen2.5-32B-Instruct-GGUF"
FILENAME = "Qwen2.5-32B-Instruct-Q4_K_M.gguf"
MODEL_ALIAS = "qwen2.5-32b-instruct"           # name clients pass as `model`
N_CTX = 8192                                   # lower to 4096 if you hit out-of-memory
TENSOR_SPLIT = [0.5, 0.5]                      # split layers evenly across both T4s
PORT = 8000
LOCAL = f"http://127.0.0.1:{PORT}"
OUT = "/kaggle/working" if os.path.isdir("/kaggle/working") else "."
MODEL_DIR = "/tmp/models"


def sh(cmd):
    print("$", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True)


# ---------- 1. install ----------
# prebuilt CUDA wheel (no 15 min compile). Falls back to default index if the wheel index is unreachable.
sh(f"{sys.executable} -m pip install -q 'llama-cpp-python[server]' "
   "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121 "
   "--prefer-binary")
sh(f"{sys.executable} -m pip install -q huggingface_hub")
sh("wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/"
   "cloudflared-linux-amd64 -O /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared")

# ---------- 2. download model ----------
from huggingface_hub import hf_hub_download
os.makedirs(MODEL_DIR, exist_ok=True)
model_path = hf_hub_download(repo_id=REPO_ID, filename=FILENAME, local_dir=MODEL_DIR)
print("model:", model_path, flush=True)
subprocess.run("nvidia-smi --query-gpu=name,memory.total --format=csv", shell=True)

# ---------- 3. helpers ----------
server_proc = None
tunnel_proc = None
public_url = None


def server_alive():
    try:
        return requests.get(f"{LOCAL}/v1/models", timeout=5).ok
    except Exception:
        return False


def start_server():
    global server_proc
    if server_proc and server_proc.poll() is None:
        server_proc.kill()
    subprocess.run(["pkill", "-f", "llama_cpp.server"], check=False)
    time.sleep(2)
    cfg = {
        "host": "0.0.0.0",
        "port": PORT,
        "models": [{
            "model": model_path,
            "model_alias": MODEL_ALIAS,
            "n_gpu_layers": -1,            # every layer on GPU
            "n_ctx": N_CTX,
            "tensor_split": TENSOR_SPLIT,  # use both GPUs
            "main_gpu": 0,
            "flash_attn": True,
            "verbose": True,
        }],
    }
    cfg_path = f"{OUT}/server_config.json"
    json.dump(cfg, open(cfg_path, "w"), indent=2)
    log = open(f"{OUT}/llama_server.log", "a")
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "llama_cpp.server", "--config_file", cfg_path],
        stdout=log, stderr=subprocess.STDOUT)
    for _ in range(600):
        if server_proc.poll() is not None:
            raise RuntimeError(f"server exited, check {OUT}/llama_server.log")
        if server_alive():
            return
        time.sleep(1)
    raise RuntimeError(f"server failed to start, check {OUT}/llama_server.log")


def warm_model():
    r = requests.post(f"{LOCAL}/v1/chat/completions",
                      json={"model": MODEL_ALIAS, "max_tokens": 8,
                            "messages": [{"role": "user", "content": "hi"}]},
                      timeout=600)
    r.raise_for_status()


def start_tunnel():
    global tunnel_proc, public_url
    if tunnel_proc and tunnel_proc.poll() is None:
        tunnel_proc.kill()
    logpath = f"{OUT}/cloudflared.log"
    open(logpath, "w").close()
    tunnel_proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", LOCAL, "--no-autoupdate"],
        stdout=open(logpath, "a"), stderr=subprocess.STDOUT)
    for _ in range(60):
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", open(logpath).read())
        if m:
            public_url = m.group(0)
            return
        time.sleep(1)
    raise RuntimeError(f"tunnel failed, check {logpath}")


def print_usage():
    print("=" * 60)
    print("ENDPOINT READY")
    print("base_url :", public_url + "/v1")
    print("api_key  : anything (e.g. 'sk-local')")
    print("model    :", MODEL_ALIAS)
    print("=" * 60)
    print(f'''
from openai import OpenAI
client = OpenAI(base_url="{public_url}/v1", api_key="sk-local")
r = client.chat.completions.create(model="{MODEL_ALIAS}", messages=[{{"role":"user","content":"Hello"}}])
print(r.choices[0].message.content)
''', flush=True)


# ---------- 4. start everything ----------
start_server()
warm_model()
start_tunnel()
print_usage()

# ---------- 5. watchdog: never exits ----------
tick = 0
while True:
    time.sleep(30)
    tick += 1
    try:
        if not server_alive():
            print(time.strftime("%H:%M:%S"), "server down, restarting", flush=True)
            start_server()
            warm_model()
        if tunnel_proc.poll() is not None:
            print(time.strftime("%H:%M:%S"), "tunnel down, restarting", flush=True)
            start_tunnel()
            print_usage()
    except Exception as e:
        print("watchdog error:", repr(e), flush=True)
    if tick % 10 == 0:
        print(time.strftime("%H:%M:%S"), "alive |", public_url, flush=True)