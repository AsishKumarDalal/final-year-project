"""Client-side tool calling probe for the Kaggle-hosted Qwen2.5 (no server tool support needed).

The model writes <tool_call>{...}</tool_call> as plain text; we find it, run the tool,
and send the result back inside <tool_response> tags.

MANUAL ONLY: not named `test_*`, not imported, never part of `make validate`.
No secret is involved (local dummy key `sk-local`).

Usage:
    python3 test/local_kaggle_llm.py
    KAGGLE_LLM_BASE_URL=https://<new-tunnel>.trycloudflare.com/v1 python3 test/local_kaggle_llm.py
"""
import json
import os
import re
from openai import OpenAI

BASE_URL = os.getenv(
    "KAGGLE_LLM_BASE_URL",
    "https://pond-breathing-foto-advocate.trycloudflare.com/v1",
)
MODEL = os.getenv("KAGGLE_LLM_MODEL", "qwen2.5-32b-instruct")
client = OpenAI(base_url=BASE_URL, api_key="sk-local", timeout=600)


# ---------- 1. your tools ----------
def get_weather(city):
    return {"city": city, "temp_c": 31, "sky": "humid, partly cloudy"}   # replace with real code


TOOL_FUNCS = {"get_weather": get_weather}

TOOL_SPECS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string"}},
                       "required": ["city"]},
    },
}]


# ---------- 2. put the tools in the system prompt (Qwen's own format) ----------
SYSTEM = (
    "You are a helpful assistant.\n\n"
    "# Tools\n\n"
    "You may call one or more functions to assist with the user query.\n\n"
    "You are provided with function signatures within <tools></tools> XML tags:\n<tools>\n"
    + "\n".join(json.dumps(t) for t in TOOL_SPECS)
    + "\n</tools>\n\n"
    "For each function call, return a json object with function name and arguments "
    "within <tool_call></tool_call> XML tags:\n"
    "<tool_call>\n"
    '{"name": <function-name>, "arguments": <args-json-object>}\n'
    "</tool_call>\n"
    "If no tool is needed, just answer normally."
)


# ---------- 3. find the tool calls in the reply ----------
CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)


def parse_tool_calls(text):
    """Return (calls, errors). calls = [{'name':..., 'arguments':{...}}]"""
    calls, errors = [], []
    for raw in CALL_RE.findall(text):
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()   # remove code fences if present
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict) or "name" not in obj:
                raise ValueError("missing 'name'")
            args = obj.get("arguments", {})
            if isinstance(args, str):          # sometimes arguments come as a JSON string
                args = json.loads(args)
            calls.append({"name": obj["name"], "arguments": args})
        except Exception as e:
            errors.append(f"Bad tool call {raw[:200]!r}: {e}")
    if "<tool_call>" in text and not calls and not errors:
        errors.append("Tool call block was not closed with </tool_call>.")   # reply was cut off
    return calls, errors


# ---------- 4. the loop ----------
def run_agent(user_text, max_steps=10):
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_text}]
    for _ in range(max_steps):
        r = client.chat.completions.create(model=MODEL, messages=messages,
                                           max_tokens=1024, temperature=0.2)
        text = r.choices[0].message.content or ""
        calls, errors = parse_tool_calls(text)

        if not calls and not errors:           # no tool call: this is the final answer
            return text

        messages.append({"role": "assistant", "content": text})
        results = list(errors)
        for c in calls:
            fn = TOOL_FUNCS.get(c["name"])
            if fn is None:
                results.append(f"Unknown tool {c['name']!r}")
                continue
            try:
                out = fn(**c["arguments"])
            except Exception as e:
                out = f"Tool error: {e}"
            results.append(json.dumps(out) if not isinstance(out, str) else out)
        # Qwen's own format: tool results go back in a user turn inside <tool_response> tags
        messages.append({"role": "user",
                         "content": "\n".join(f"<tool_response>\n{x[:4000]}\n</tool_response>" for x in results)})
    return "Stopped: too many steps."


if __name__ == "__main__":
    print(run_agent("What is the weather in Kolkata?"))
