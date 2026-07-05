"""Multi-LLM provider drivers for the AI Strategy Builder (M12). Stdlib only.

Providers read credentials from environment (loaded from .env by server.py):
  anthropic -> ANTHROPIC_API_KEY            openai -> OPENAI_API_KEY
  ollama    -> OLLAMA_URL (default :11434)  lmstudio -> LMSTUDIO_URL (default :1234)
  custom    -> AI_ENDPOINT (+ AI_KEY, AI_MODEL)

Every driver returns a spec dict in the quantdesk.strategy.v1 schema or raises;
the server falls back to the built-in rule parser on any failure. Keys are
never logged. The LLM gets the same schema the builtin parser emits, so the
platform consumes identical specs regardless of provider.
"""
import json
import os
import re
import urllib.request

SCHEMA_PROMPT = """You convert a trader's verbal strategy description into a JSON spec.
Output ONLY a JSON object (no markdown, no commentary) with this exact schema:
{"schema":"quantdesk.strategy.v1","name":"<kebab-slug>","class":"market_making|order_flow|mean_reversion|momentum|unclassified",
"universe":["SYMBOLS"],"data":{"resolution":"tick_l2|second|minute|daily","warmup":"<spec or auto_from_lookbacks>"},
"regime":{"filter":"<rule or none>"},"entry":{"signal":"<precise rule>","persistence_ms":null,"spread_max_ticks":null},
"exit":{"take_profit_bps":null,"stop_bps":null,"timeout_s":null},
"sizing":{"base_pct_equity":null,"vol_adjust":"<rule or none>"},
"confidence":{"model":"<threshold|sigmoid>","note":""},
"risk":{"daily_loss_halt":"inherit_global","per_trade_stop":"<rule>"},
"kill_criterion":"<what invalidates the strategy — REQUIRED, extract or write <REQUIRED — not found>>",
"holding_period":"<seconds|minutes|hours|days>","generated_by":"<provider>","requires_review":true}
Use null for numbers not stated. Never invent symbols not mentioned."""


def _post(url, payload, headers, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in model output")
    spec = json.loads(m.group(0))
    if spec.get("schema") != "quantdesk.strategy.v1":
        spec["schema"] = "quantdesk.strategy.v1"
    for k in ("name", "class", "universe", "entry", "exit", "kill_criterion"):
        if k not in spec:
            raise ValueError(f"spec missing key: {k}")
    return spec


def parse(provider, text, model=None, endpoint=None, key=None):
    """Route to a provider. Raises on any failure (server falls back to builtin)."""
    user = f"Strategy description:\n{text}"
    if provider == "anthropic":
        api_key = key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        r = _post("https://api.anthropic.com/v1/messages",
                  {"model": model or "claude-sonnet-5", "max_tokens": 1500,
                   "system": SCHEMA_PROMPT, "messages": [{"role": "user", "content": user}]},
                  {"x-api-key": api_key, "anthropic-version": "2023-06-01"})
        out = "".join(b.get("text", "") for b in r.get("content", []))
    elif provider == "openai":
        api_key = key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")
        r = _post("https://api.openai.com/v1/chat/completions",
                  {"model": model or "gpt-4o-mini",
                   "messages": [{"role": "system", "content": SCHEMA_PROMPT},
                                {"role": "user", "content": user}]},
                  {"Authorization": f"Bearer {api_key}"})
        out = r["choices"][0]["message"]["content"]
    elif provider in ("ollama", "lmstudio", "custom"):
        base = endpoint or os.environ.get(
            {"ollama": "OLLAMA_URL", "lmstudio": "LMSTUDIO_URL", "custom": "AI_ENDPOINT"}[provider],
            {"ollama": "http://localhost:11434/v1", "lmstudio": "http://localhost:1234/v1",
             "custom": ""}[provider])
        if not base:
            raise RuntimeError(f"{provider}: endpoint not configured")
        headers = {}
        k = key or os.environ.get("AI_KEY")
        if k:
            headers["Authorization"] = f"Bearer {k}"
        r = _post(base.rstrip("/") + "/chat/completions",
                  {"model": model or os.environ.get("AI_MODEL", "llama3.1"),
                   "messages": [{"role": "system", "content": SCHEMA_PROMPT},
                                {"role": "user", "content": user}]},
                  headers, timeout=120)
        out = r["choices"][0]["message"]["content"]
    else:
        raise RuntimeError(f"unknown provider: {provider}")
    spec = _extract_json(out)
    spec["generated_by"] = provider
    spec["requires_review"] = True
    return spec
