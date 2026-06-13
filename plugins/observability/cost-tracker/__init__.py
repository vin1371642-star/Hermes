"""cost-tracker — Hermes observability plugin.

CLI-режим: печатает стоимость/модель/инструменты в stderr после каждого запроса.
Gateway-режим (Telegram, Discord и др.): добавляет компактный футер в конец
каждого ответа с именем модели, провайдером, инструментами и стоимостью.

Накопленный итог сохраняется в ~/.hermes/cost_tracker.json.

Активация:
  hermes plugins enable observability/cost-tracker
"""
from __future__ import annotations

import json
import sys
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

# Seen api_request_ids — защита от двойного учёта.
_SEEN: set[str] = set()

# Состояние текущего хода, ключ — session_id.
# Хранит модель, провайдера, стоимость и список инструментов.
_TURN: dict[str, dict] = defaultdict(lambda: {
    "model": "",
    "provider": "",
    "cost_usd": 0.0,
    "is_approx": False,
    "tools": [],
})

# ANSI-коды (для CLI / stderr)
_R  = "\033[0m"
_B  = "\033[1m"
_D  = "\033[2m"
_CY = "\033[36m"
_YL = "\033[33m"
_GR = "\033[32m"
_RD = "\033[31m"


def _out(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _cost_file() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home() / "cost_tracker.json"
    except Exception:
        return Path.home() / ".hermes" / "cost_tracker.json"


def _load() -> dict:
    try:
        p = _cost_file()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {
        "total_usd": 0.0,
        "request_count": 0,
        "by_provider": {},
        "by_model": {},
        "first_seen_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": "",
    }


def _save(data: dict) -> None:
    try:
        p = _cost_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _fmt_usd(v: float | None, *, approx: bool = False) -> str:
    if v is None:
        return "?"
    if v == 0.0:
        return "$0.00"
    s = f"${v:.6f}" if v < 0.0001 else (f"${v:.4f}" if v < 0.01 else f"${v:.3f}")
    return s + ("~" if approx else "")


def on_post_api_request(
    *,
    model: str = "",
    provider: str = "",
    base_url: str = "",
    api_mode: str = "",
    api_request_id: str = "",
    session_id: str = "",
    usage: Any = None,
    response: Any = None,
    api_duration: float = 0.0,
    **_: Any,
) -> None:
    # ── Защита от двойного учёта ─────────────────────────────────────────────
    if api_request_id:
        with _LOCK:
            if api_request_id in _SEEN:
                return
            _SEEN.add(api_request_id)
            if len(_SEEN) > 200:
                _SEEN.clear()

    # ── Токены ───────────────────────────────────────────────────────────────
    input_tok = output_tok = cache_read = cache_write = reasoning = 0
    if isinstance(usage, dict) and usage:
        input_tok  = usage.get("input_tokens", 0) or 0
        output_tok = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0) or 0
        cache_read  = usage.get("cache_read_tokens", 0) or 0
        cache_write = usage.get("cache_write_tokens", 0) or 0
        reasoning   = usage.get("reasoning_tokens", 0) or 0
    elif response is not None and hasattr(response, "usage") and response.usage:
        try:
            from agent.usage_pricing import normalize_usage
            cu = normalize_usage(response.usage, provider=provider, api_mode=api_mode)
            input_tok, output_tok = cu.input_tokens, cu.output_tokens
            cache_read, cache_write, reasoning = cu.cache_read_tokens, cu.cache_write_tokens, cu.reasoning_tokens
        except Exception:
            pass

    if input_tok == 0 and output_tok == 0:
        return

    # ── Стоимость ────────────────────────────────────────────────────────────
    cost_usd: float | None = None
    is_approx = False
    try:
        from agent.usage_pricing import CanonicalUsage, estimate_usage_cost
        cu = CanonicalUsage(
            input_tokens=input_tok, output_tokens=output_tok,
            cache_read_tokens=cache_read, cache_write_tokens=cache_write,
            reasoning_tokens=reasoning,
        )
        r = estimate_usage_cost(model, cu, provider=provider, base_url=base_url, api_key="")
        if r.amount_usd is not None:
            cost_usd = float(r.amount_usd)
            is_approx = r.status == "estimated"
    except Exception:
        pass

    # ── Обновляем накопленный итог ───────────────────────────────────────────
    with _LOCK:
        totals = _load()
        if cost_usd is not None:
            totals["total_usd"] = totals.get("total_usd", 0.0) + cost_usd
            bp = totals.setdefault("by_provider", {})
            bp[provider or "unknown"] = bp.get(provider or "unknown", 0.0) + cost_usd
            bm = totals.setdefault("by_model", {})
            bm[model or "unknown"] = bm.get(model or "unknown", 0.0) + cost_usd
        totals["request_count"] = totals.get("request_count", 0) + 1
        totals["updated_at"] = datetime.now(timezone.utc).isoformat()
        cumulative = totals.get("total_usd", 0.0)
        req_n = totals["request_count"]
        _save(totals)

        # Обновляем состояние текущего хода (для футера в gateway)
        key = session_id or "default"
        turn = _TURN[key]
        turn["model"]    = model or turn["model"]
        turn["provider"] = provider or turn["provider"]
        if cost_usd is not None:
            turn["cost_usd"] += cost_usd
            turn["is_approx"] = turn["is_approx"] or is_approx

    # ── CLI-вывод в stderr ───────────────────────────────────────────────────
    prov  = provider or ""
    mod   = model or "?"
    display = f"{mod} @ {prov}" if prov and prov.lower() not in mod.lower() else mod

    parts = [f"in:{input_tok:,}", f"out:{output_tok:,}"]
    if cache_read:  parts.append(f"↩{cache_read:,}")
    if cache_write: parts.append(f"↪{cache_write:,}")
    if reasoning:   parts.append(f"think:{reasoning:,}")

    cost_str = _fmt_usd(cost_usd, approx=is_approx)
    dur_str  = f"  {_D}{api_duration:.1f}s{_R}" if api_duration else ""

    _out(
        f"{_B}{_CY}[LLM]{_R} {_B}{display}{_R}  "
        f"{_D}{' '.join(parts)}{_R}  "
        f"{_B}{_GR}{cost_str}{_R}  "
        f"{_D}∑{_fmt_usd(cumulative)}  #{req_n}{_R}"
        + dur_str
    )


def on_pre_tool_call(*, tool_name: str = "", args: Any = None,
                     session_id: str = "", **_: Any) -> None:
    # CLI-вывод
    args_str = ""
    if isinstance(args, dict):
        for k, v in args.items():
            args_str = f"{k}={str(v).replace(chr(10), ' ')[:80]!r}"
            break
    _out(
        f"{_YL}[TOOL→]{_R} {_B}{tool_name}{_R}"
        + (f"  {_D}{args_str}{_R}" if args_str else "")
    )
    # Запоминаем инструмент для футера
    key = session_id or "default"
    with _LOCK:
        _TURN[key]["tools"].append(tool_name)


def on_post_tool_call(*, tool_name: str = "", status: str = "ok",
                      duration_ms: float = 0.0, error_message: str = "",
                      **_: Any) -> None:
    icons = {"ok": f"{_GR}✓{_R}", "error": f"{_RD}✗{_R}",
             "blocked": f"{_RD}⊘{_R}", "cancelled": f"{_D}⊘{_R}"}
    icon    = icons.get(status, f"{_D}{status}{_R}")
    dur_str = f"  {_D}{duration_ms/1000:.2f}s{_R}" if duration_ms else ""
    err_str = f"  {_RD}{error_message[:100]}{_R}" if error_message else ""
    _out(f"{_YL}[TOOL✓]{_R} {_B}{tool_name}{_R}  {icon}" + dur_str + err_str)


def on_transform_llm_output(*, response_text: str = "", session_id: str = "",
                             model: str = "", platform: str = "",
                             **_: Any) -> str | None:
    """Добавляет компактный футер с источником и стоимостью в каждый ответ."""
    key = session_id or "default"
    with _LOCK:
        turn = dict(_TURN.get(key, {}))
        # Сбрасываем состояние хода после прочтения
        if key in _TURN:
            _TURN[key] = {"model": "", "provider": "", "cost_usd": 0.0,
                          "is_approx": False, "tools": []}

    used_model    = turn.get("model") or model or "?"
    used_provider = turn.get("provider") or ""
    cost_usd      = turn.get("cost_usd", 0.0)
    is_approx     = turn.get("is_approx", False)
    tools         = turn.get("tools", [])

    # Строим лейбл модели
    prov = used_provider or ""
    if prov and prov.lower() not in used_model.lower():
        model_label = f"{used_model} @ {prov}"
    else:
        model_label = used_model

    # Инструменты (уникальные, в порядке первого появления)
    seen_tools: set[str] = set()
    unique_tools = [t for t in tools if not (t in seen_tools or seen_tools.add(t))]  # type: ignore[func-returns-value]

    # Накопленный итог
    try:
        totals = _load()
        cumulative = totals.get("total_usd", 0.0)
    except Exception:
        cumulative = 0.0

    # Собираем части футера
    parts: list[str] = [f"🤖 {model_label}"]
    if unique_tools:
        parts.append("🔧 " + ", ".join(unique_tools[:5])
                     + (f" +{len(unique_tools)-5}" if len(unique_tools) > 5 else ""))
    if cost_usd > 0 or is_approx:
        cost_str = _fmt_usd(cost_usd, approx=is_approx)
        cum_str  = _fmt_usd(cumulative)
        parts.append(f"💰 {cost_str} (∑ {cum_str})")

    if not parts or model_label in ("?", ""):
        return None  # нечего добавлять

    footer = "\n\n" + " · ".join(parts)
    return response_text + footer


def register(ctx) -> None:
    ctx.register_hook("post_api_request",    on_post_api_request)
    ctx.register_hook("post_llm_call",       on_post_api_request)   # fallback
    ctx.register_hook("pre_tool_call",       on_pre_tool_call)
    ctx.register_hook("post_tool_call",      on_post_tool_call)
    ctx.register_hook("transform_llm_output", on_transform_llm_output)
