"""
auto-router — Hermes plugin.

Fires on pre_gateway_dispatch, classifies the text, scores complexity,
and sets gateway._session_model_overrides so the right model is used
for the turn — no restart required.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROVIDER = "openrouter"

# ── Base model map ────────────────────────────────────────────────────────────
MODELS: dict[str, str] = {
    # Marketing
    "mkt_content":    "anthropic/claude-sonnet-4.6",
    "mkt_longform":   "anthropic/claude-opus-4.8",
    "mkt_ads":        "anthropic/claude-sonnet-4.6",
    "mkt_seo":        "deepseek/deepseek-v4-pro",
    "mkt_brand":      "anthropic/claude-opus-4.8",
    "mkt_analytics":  "deepseek/deepseek-v4-pro",
    "mkt_social":     "anthropic/claude-sonnet-4.6",
    "mkt_research":   "google/gemini-3.5-flash",
    # Code
    "code_write":     "deepseek/deepseek-v4-pro",
    "code_arch":      "anthropic/claude-opus-4.8",
    "code_review":    "anthropic/claude-sonnet-4.6",
    "code_debug":     "deepseek/deepseek-v4-pro",
    "code_test":      "deepseek/deepseek-v4-pro",
    "code_docs":      "anthropic/claude-sonnet-4.6",
    "code_large":     "google/gemini-3.1-pro-preview",
    # Research
    "research_web":   "google/gemini-3.5-flash",
    "research_docs":  "google/gemini-3.1-pro-preview",
    "research_trend": "x-ai/grok-4.3",
    # Creative (split by depth)
    "creative_light": "anthropic/claude-sonnet-4.6",
    "creative_deep":  "anthropic/claude-opus-4.8",
    # Specialised
    "math":           "deepseek/deepseek-v4-pro",
    "pdf":            "google/gemini-3.1-pro-preview",
    "voice":          "anthropic/claude-haiku-4.5",
}

# ── Complexity escalation: complex tasks get a more capable model ─────────────
ESCALATE: dict[str, str] = {
    "creative_light": "anthropic/claude-opus-4.8",
    "mkt_content":    "anthropic/claude-opus-4.8",
    "code_write":     "anthropic/claude-opus-4.8",
    "code_debug":     "anthropic/claude-sonnet-4.6",
    "research_web":   "google/gemini-3.1-pro-preview",
    "mkt_analytics":  "anthropic/claude-sonnet-4.6",
    "math":           "anthropic/claude-opus-4.8",
}

# ── Low-confidence fallback: score == 1 → cheaper safe model ─────────────────
FALLBACK: dict[str, str] = {
    "code_write":    "deepseek/deepseek-v4-flash",
    "mkt_content":   "anthropic/claude-sonnet-4.6",
    "mkt_ads":       "deepseek/deepseek-v4-flash",
    "research_web":  "google/gemini-3.5-flash",
    "mkt_analytics": "deepseek/deepseek-v4-flash",
}

# ── Keyword helpers ───────────────────────────────────────────────────────────

def _match(kws: list[str], text: str) -> int:
    return sum(1 for k in kws if k in text)


COMPLEXITY_KW = [
    'подробно', 'детально', 'комплексно', 'полный анализ', 'глубокий анализ',
    'comprehensive', 'in-depth', 'detailed', 'thorough', 'step by step',
    'шаг за шагом', 'полный цикл', 'с нуля', 'весь процесс', 'максимально',
    'расширенный', 'продвинутый', 'сложн', 'нетривиальн',
]

CREATIVE_DEEP_KW = [
    'роман', 'сценари', 'история', 'нарратив', 'сюжет', 'screenplay',
    'story', 'narrative', 'novel', 'цикл историй', 'полноценн', 'книга',
]
CREATIVE_LIGHT_KW = [
    'придумай', 'генерируй идеи', 'brainstorm', 'брейншторм', 'концепци',
    'нейминг', 'идеи для', 'варианты', 'предложи', 'накидай',
]

MATH_KW = [
    'вычисли', 'посчитай', 'математик', 'формул', 'уравнени', 'интеграл',
    'производн', 'статистик', 'вероятност', 'матриц', 'вектор', 'дифференц',
    'calculate', 'compute', 'equation', 'formula', 'math', 'algebra',
    'calculus', 'probability', 'statistics', 'derivative', 'integral',
    'regression', 'correlation', 'стандартное отклонени',
]

PDF_KW = [
    '.pdf', 'pdf', 'из документа', 'в документе', 'этот файл', 'этот документ',
    'прочитай файл', 'проанализируй файл', 'загруженн', 'приложенн',
    'read document', 'analyze file', 'summarize this file', 'из этого файла',
]

VOICE_KW = [
    'озвучь', 'синтез речи', 'tts', 'text to speech', 'аудио версию',
    'скрипт для видео', 'диктор', 'voice over', 'аудиокнига',
    'подкаст скрипт', 'запись голоса', 'текст для озвучки',
]


def _complexity(text: str) -> str:
    t = text.lower()
    words = len(t.split())
    depth = _match(COMPLEXITY_KW, t)
    if words > 250 or depth >= 2:
        return "complex"
    elif words > 70 or depth >= 1:
        return "medium"
    return "simple"


def _classify(text: str) -> tuple[str | None, int]:
    """Returns (category, confidence_score)."""
    t = text.lower()

    # Specialised fast-paths (checked before domain scoring)
    math_score = _match(MATH_KW, t)
    if math_score >= 2:
        return "math", math_score

    pdf_score = _match(PDF_KW, t)
    if pdf_score >= 1:
        return "pdf", pdf_score

    voice_score = _match(VOICE_KW, t)
    if voice_score >= 1:
        return "voice", voice_score

    # Marketing
    scores_mkt = {
        "mkt_brand":    _match(['бренд', 'tone of voice', 'позиционирован', 'утп', 'нейминг', 'слоган', 'brand', 'usp', 'айдентик', 'brand voice'], t),
        "mkt_longform": _match(['лонгрид', 'white paper', 'большую статью', 'подробную статью', 'лонг-рид'], t),
        "mkt_content":  _match(['напиши статью', 'напиши текст', 'напиши пост', 'напиши письмо', 'контент-план', 'контент план', 'email цепочк', 'рассылк', 'write article', 'blog post', 'newsletter'], t),
        "mkt_ads":      _match(['реклам', 'объявлени', 'таргет', 'баннер', 'google ads', 'meta ads', 'cta', 'рекламный текст', 'ad copy', 'яндекс директ'], t),
        "mkt_seo":      _match(['seo', 'сео', 'семантик', 'ключевые слов', 'мета-тег', 'meta description', 'title tag', 'поисковая оптимизаци', 'ранжирован'], t),
        "mkt_analytics":_match(['roas', 'cpa', 'ctr', 'roi', 'метрик', 'воронк', 'конверси', 'аналитик', 'дашборд', 'kpi', 'attribution', 'когорт', 'retention'], t),
        "mkt_social":   _match(['instagram', 'инстаграм', 'вконтакте', 'tiktok', 'telegram канал', 'stories', 'сторис', 'reels', 'smm', 'соцсет', 'youtube канал'], t),
        "mkt_research": _match(['анализ конкурент', 'анализ рынк', 'аудитори', 'скрапи', 'парси сайт', 'competitor', 'market research', 'портрет клиент'], t),
    }
    if scores_mkt["mkt_brand"] > 0 and scores_mkt["mkt_longform"] > 0:
        scores_mkt["mkt_longform"] += 1

    # Code
    scores_code = {
        "code_arch":  _match(['архитектур', 'спроектируй', 'system design', 'паттерн', 'микросервис', 'как построить', 'структура проект', 'scheme'], t),
        "code_write": _match(['напиши код', 'напиши функци', 'реализуй', 'написать скрипт', 'write code', 'implement', 'create class', 'добавь метод', 'сделай эндпоинт', 'def ', 'class ', 'async def'], t),
        "code_review":_match(['проверь код', 'code review', 'ревью', 'что не так с кодом', 'review this', 'улучши код', 'оптимизируй', 'рефактор', 'refactor', 'clean up'], t),
        "code_debug": _match(['баг', 'ошибк', 'exception', 'traceback', 'error:', 'не работает', 'почему падает', 'debug', 'исправь', 'не запускается', 'AttributeError', 'TypeError', 'ValueError', 'ImportError', 'SyntaxError'], t),
        "code_test":  _match(['напиши тест', 'unit test', 'integration test', 'pytest', 'тест для', 'test coverage', 'mock', 'fixture', 'write tests'], t),
        "code_docs":  _match(['документаци', 'readme', 'docstring', 'комментари к коду', 'документируй', 'write docs'], t),
        "code_large": _match(['весь репозитори', 'вся кодовая база', 'найди в коде', 'все файлы проект', 'search codebase', 'проблемы в коде', 'по всему проект', 'across the repo'], t),
    }
    if scores_code["code_large"] > 0:
        scores_code["code_large"] += 0.5

    # Research
    scores_res = {
        "research_trend": _match(['тренд', 'что сейчас', 'актуально', 'последние новости', 'что происходит', 'trends', 'current events', 'latest news'], t),
        "research_docs":  _match(['прочитай документ', 'проанализируй pdf', 'что в этом файле', 'read this document', 'summarize document', 'из этого pdf'], t),
        "research_web":   _match(['найди в интернете', 'поищи', 'что пишут', 'найди данные', 'search the web', 'find online', 'парси', 'lookup'], t),
    }

    # Creative (depth-aware)
    deep_score  = _match(CREATIVE_DEEP_KW, t)
    light_score = _match(CREATIVE_LIGHT_KW, t)

    mkt_total  = sum(scores_mkt.values())
    code_total = sum(scores_code.values())
    res_total  = sum(scores_res.values())
    cre_total  = deep_score + light_score

    totals = {"mkt": mkt_total, "code": code_total, "research": res_total, "creative": cre_total}
    winner = max(totals, key=totals.get)
    score  = totals[winner]

    if score == 0:
        return None, 0

    if winner == "mkt":
        return max(scores_mkt, key=scores_mkt.get), score
    if winner == "code":
        return max(scores_code, key=scores_code.get), score
    if winner == "research":
        return max(scores_res, key=scores_res.get), score

    # creative
    return ("creative_deep" if deep_score >= light_score and deep_score > 0 else "creative_light"), cre_total


def _get_openrouter_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        try:
            env_path = Path(os.environ.get("HERMES_HOME", "")) / ".env"
            if not env_path.exists():
                from hermes_constants import get_hermes_home
                env_path = get_hermes_home() / ".env"
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
        except Exception:
            pass
    return key


_api_key: str | None = None


def on_pre_gateway_dispatch(
    *,
    event: Any = None,
    gateway: Any = None,
    session_store: Any = None,
    **_: Any,
) -> dict | None:
    global _api_key

    if event is None or gateway is None:
        return None

    text: str = getattr(event, "text", None) or ""
    if not text or len(text.strip()) < 5:
        return None
    if text.strip().startswith("/"):
        return None

    category, score = _classify(text)
    if category is None:
        return None

    complexity = _complexity(text)

    # Choose model: escalate on complex, fallback on weak signal
    model = MODELS.get(category)
    if not model:
        return None

    if complexity == "complex" and category in ESCALATE:
        model = ESCALATE[category]
        logger.debug("auto-router: complexity escalation [%s] → %s", category, model)
    elif score == 1 and category in FALLBACK:
        model = FALLBACK[category]
        logger.debug("auto-router: low-confidence fallback [%s] → %s", category, model)

    # Resolve session key
    try:
        source = event.source
        session_key = gateway._session_key_for_source(source)
    except Exception as e:
        logger.debug("auto-router: could not resolve session key: %s", e)
        return None

    if _api_key is None:
        _api_key = _get_openrouter_api_key()

    # If API key is empty, skip override (let Hermes use configured fallback)
    if not _api_key:
        logger.warning("auto-router: OPENROUTER_API_KEY not found, skipping override")
        return None

    existing = gateway._session_model_overrides.get(session_key, {})
    if existing.get("model") == model and existing.get("provider") == PROVIDER:
        logger.debug("auto-router: model already set to %s, skipping", model)
        return None

    gateway._session_model_overrides[session_key] = {
        "model":    model,
        "provider": PROVIDER,
        "api_key":  _api_key,
        "base_url": "",
        "api_mode": "",
    }

    logger.info(
        "auto-router: [%s|%s|score=%d] → %s (%s)",
        category, complexity, score, model, session_key,
    )
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", on_pre_gateway_dispatch)
