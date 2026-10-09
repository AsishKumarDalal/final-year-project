import os
import json
import urllib.request

BASE = os.getenv("KAGGLE_LLM_BASE_URL", "https://pond-breathing-foto-advocate.trycloudflare.com/v1")
client = __import__("openai").OpenAI(base_url=BASE, api_key="sk-local", timeout=60)


def ask(user_text, max_tokens=40):
    t0 = __import__("time").time()
    r = client.chat.completions.create(
        model="qwen2.5-32b-instruct",
        messages=[{"role": "user", "content": user_text}],
        max_tokens=max_tokens,
        temperature=0.0,
    )
    dt = round(__import__("time").time() - t0, 2)
    content = r.choices[0].message.content or ""
    print(f"[{dt}s] Q: {user_text!r}\n    A: {content.strip()!r}\n")
    return content


if __name__ == "__main__":
    ask("What are the three main types of blood cells?")
    ask("List the steps to perform hand hygiene using soap and water.")
    ask("What is the normal range for fasting blood glucose in mg/dL?")
    ask("Explain the difference between systolic and diastolic blood pressure.")
