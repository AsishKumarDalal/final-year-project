import os
import json
import urllib.request
import time

BASE = os.getenv("KAGGLE_LLM_BASE_URL", "https://pond-breathing-foto-advocate.trycloudflare.com/v1")


def ask(user_text, max_tokens=80, temperature=0, label=""):
    payload = {
        "model": "qwen2.5-32b-instruct",
        "messages": [{"role": "user", "content": user_text}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode()
    dt = round(time.time() - t0, 2)
    content = json.loads(body)["choices"][0]["message"]["content"].strip()
    print(f"[{dt}s] {label or user_text}")
    print("   ->", content)
    print()
    return content


if __name__ == "__main__":
    ask(
        "A 55-year-old smoker has chest pain radiating to the left arm. Name the two most likely cardiac conditions.",
        label="CARDIAC",
    )
    ask("What is the first-line medication for a patient in rapid afib with non-ischemic chest pain?", label="AFIB")
    # connectivity + fresh single-shot probe
    import urllib.request
    import json as _json

    base = os.getenv("KAGGLE_LLM_BASE_URL", "https://pond-breathing-foto-advocate.trycloudflare.com/v1")
    t0 = time.time()
    try:
        with urllib.request.urlopen(base + "/v1/models", timeout=15) as r:
            print("[OK:", round(time.time() - t0, 2), "s] models 200 ->",
                  r.read().decode()[:80])
    except Exception as e:
        print("[ERR:", type(e).__name__, e, "]")

    payload = {
        "model": "qwen2.5-32b-instruct",
        "messages": [{"role": "user", "content": "What is 2 + 2? Reply with only the number."}],
        "max_tokens": 8,
        "temperature": 0,
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode()
    print("[OK:", round(time.time() - t0, 2), "s] 2+2 ->",
          json.loads(body)["choices"][0]["message"]["content"].strip())
