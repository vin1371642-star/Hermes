"""
Hermes auto-router — pre_llm_call hook (v2).
Classifies the user request into a sub-category and injects routing context
so the agent knows exactly which model to use for each sub-task.

v2 improvements (14.06.2026):
  • Учитывает контекст диалога (последние 3 хода, не только последнее сообщение)
  • Триггер по сложности: длина, наличие кода, тех.термины → рекомендация Opus/Pro
  • Fallback-логика: при неясной категории даёт общую рекомендацию по сложности
  • Сохранена обратная совместимость с v1 (тот же формат вывода)
"""
import json
import re
import sys

# ── Marketing sub-categories ──────────────────────────────────────────────────

MKT_RESEARCH = [
    'анализ рынк', 'анализируй рынок', 'исследование рынк', 'конкурент',
    'целевая аудитори', 'аудитори', 'сегмент', 'портрет клиент',
    'customer research', 'market research', 'competitor analysis', 'target audience',
    'парси сайт конкурент', 'scrape competitor', 'мониторинг бренд',
]

MKT_CONTENT = [
    'напиши статью', 'напиши текст', 'напиши пост', 'напиши письмо',
    'лонгрид', 'white paper', 'контент-план', 'контент план', 'контент для',
    'write article', 'write copy', 'blog post', 'newsletter',
    'email цепочк', 'email-рассылк', 'рассылк', 'письма для',
]

MKT_ADS = [
    'реклам', 'объявлени', 'таргет', 'креатив для', 'баннер',
    'google ads', 'meta ads', 'facebook ads', 'вк реклам', 'яндекс директ',
    'ad copy', 'рекламный текст', 'заголовок для рекламы', 'cta',
    'utm', 'a/b тест рекламы',
]

MKT_SEO = [
    'seo', 'сео', 'семантическ', 'ключевые слов', 'мета-тег', 'meta description',
    'title tag', 'seo-текст', 'поисковая оптимизаци', 'ранжировани',
    'keyword', 'organic traffic', 'sitemap', 'robots.txt',
]

MKT_BRAND = [
    'бренд', 'tone of voice', 'позиционировани', 'уникальное торговое',
    'уtp', 'brand identity', 'нейминг', 'слоган', 'айдентик',
    'brand voice', 'messaging', 'value proposition',
]

MKT_ANALYTICS = [
    'аналитик', 'метрик', 'конверси', 'roas', 'roi', 'cpa', 'ctr',
    'воронк', 'отчёт по', 'дашборд', 'kpi', 'analytics', 'dashboard',
    'performance', 'attribution', 'когорт', 'retention',
]

MKT_SOCIAL = [
    'соцсет', 'instagram', 'вконтакте', 'telegram канал', 'tiktok',
    'youtube', 'linkedin', 'twitter', 'reels', 'stories', 'сторис',
    'social media', 'smm', 'контент для инстаграм', 'контент для тг',
]

# ── Code sub-categories ───────────────────────────────────────────────────────

CODE_ARCH = [
    'архитектур', 'спроектируй', 'как организовать', 'структура проект',
    'design pattern', 'паттерн', 'микросервис', 'system design',
    'how to design', 'как построить систему', 'scheme', 'схема системы',
]

CODE_WRITE = [
    'напиши код', 'напиши функци', 'реализуй', 'написать скрипт',
    'write code', 'write function', 'implement', 'create class',
    'добавь метод', 'сделай эндпоинт', 'создай api',
    'def ', 'class ', 'async def',
]

CODE_REVIEW = [
    'проверь код', 'code review', 'ревью', 'что не так с кодом',
    'review this', 'посмотри код', 'улучши код', 'оптимизируй',
    'refactor', 'рефактор', 'clean up',
]

CODE_DEBUG = [
    'баг', 'ошибк', 'exception', 'traceback', 'error:', 'не работает',
    'почему падает', 'debug', 'fix this', 'исправь', 'не запускается',
    'AttributeError', 'TypeError', 'ValueError', 'ImportError',
    'ModuleNotFoundError', 'SyntaxError', 'RuntimeError',
]

CODE_TEST = [
    'напиши тест', 'unit test', 'integration test', 'pytest', 'тест для',
    'test coverage', 'тестирован', 'mock', 'fixture', 'write tests',
]

CODE_DOCS = [
    'документаци', 'readme', 'docstring', 'комментари к коду',
    'документируй', 'documentation', 'write docs', 'describe function',
]

CODE_LARGE = [
    'весь репозитори', 'вся кодовая база', 'найди в коде', 'где в коде',
    'search codebase', 'across the repo', 'all files', 'проанализируй кодовую базу',
    'проблемы в коде', 'по всему проект', 'все файлы',
]

# ── Research sub-categories ───────────────────────────────────────────────────

RES_WEB = [
    'найди в интернете', 'поищи в сети', 'что пишут', 'найди данные',
    'search the web', 'find online', 'lookup', 'парси', 'scrape',
]

RES_DOCS = [
    'прочитай документ', 'проанализируй pdf', 'изучи файл', 'что в этом файле',
    'read this document', 'summarize document', 'из этого pdf',
]

RES_TRENDS = [
    'тренд', 'что сейчас популярно', 'актуально ли', 'последние новости',
    'trends', 'current events', 'latest news', 'что происходит',
]

# ── Creative ──────────────────────────────────────────────────────────────────

CREATIVE_KW = [
    'придумай', 'генерируй идеи', 'brainstorm', 'брейншторм', 'концепци',
    'нейминг', 'слоган', 'сценари', 'история', 'креатив', 'creative',
    'идеи для', 'варианты названий', 'предложи идеи',
]

# ── v2: Триггеры сложности ────────────────────────────────────────────────────

# Технические/сложные термины → рекомендация Opus / Pro
COMPLEX_TERMS = [
    'архитектур', 'масштабирован', 'распределённ', 'concurrency', 'race condition',
    'микросервис', 'kubernetes', 'k8s', 'distributed', 'optimization', 'оптимизац',
    'алгоритм', 'сложност', 'big-o', 'asymptotic',
]

# Длинные сообщения → длинный контент → Opus
LONG_TEXT_THRESHOLD = 800  # символов

# Индикаторы кода (для определения тех.задачи)
CODE_INDICATORS = re.compile(r'```|def\s+\w+|class\s+\w+|import\s+\w+|function\s+\w+|const\s+\w+|var\s+\w+')


def get_recent_text(payload: dict, turns: int = 3) -> str:
    """v2: Берёт последние N ходов диалога для контекстной классификации."""
    messages = payload.get('messages', [])
    user_texts = []
    for m in reversed(messages):
        if m.get('role') == 'user':
            content = m.get('content', '')
            if isinstance(content, list):
                for p in content:
                    if isinstance(p, dict) and p.get('type') == 'text':
                        user_texts.append(p.get('text', ''))
            elif isinstance(content, str):
                user_texts.append(content)
            if len(user_texts) >= turns:
                break
    return ' '.join(reversed(user_texts))


def get_last_user_text(payload: dict) -> str:
    """v1 compat: только последнее сообщение пользователя."""
    return get_recent_text(payload, turns=1)


def has_code(text: str) -> bool:
    """v2: Детектор кода в тексте."""
    return bool(CODE_INDICATORS.search(text))


def is_complex(text: str) -> tuple[bool, str]:
    """v2: Возвращает (сложная_ли_задача, причина)."""
    if len(text) > LONG_TEXT_THRESHOLD:
        return True, f"длинный текст ({len(text)} символов)"
    t = text.lower()
    for term in COMPLEX_TERMS:
        if term in t:
            return True, f"технический термин: '{term}'"
    if has_code(text):
        return True, "содержит код"
    return False, ""


def match(keywords, text):
    return sum(1 for k in keywords if k in text)


def classify(text: str) -> tuple[str, str, str]:
    """Returns (category, sub_category, hint)"""
    t = text.lower()

    # ── Marketing ──
    scores_mkt = {
        'research':   match(MKT_RESEARCH, t),
        'content':    match(MKT_CONTENT, t),
        'ads':        match(MKT_ADS, t),
        'seo':        match(MKT_SEO, t),
        'brand':      match(MKT_BRAND, t),
        'analytics':  match(MKT_ANALYTICS, t),
        'social':     match(MKT_SOCIAL, t),
    }
    mkt_total = sum(scores_mkt.values())

    # ── Code ──
    scores_code = {
        'arch':    match(CODE_ARCH, t),
        'write':   match(CODE_WRITE, t),
        'review':  match(CODE_REVIEW, t),
        'debug':   match(CODE_DEBUG, t),
        'test':    match(CODE_TEST, t),
        'docs':    match(CODE_DOCS, t),
        'large':   match(CODE_LARGE, t),
    }
    code_total = sum(scores_code.values())

    # ── Research ──
    scores_res = {
        'web':    match(RES_WEB, t),
        'docs':   match(RES_DOCS, t),
        'trends': match(RES_TRENDS, t),
    }
    res_total = sum(scores_res.values())

    creative_total = match(CREATIVE_KW, t)

    totals = {
        'marketing': mkt_total,
        'code':      code_total,
        'research':  res_total,
        'creative':  creative_total,
    }
    best_cat = max(totals, key=totals.get)
    if totals[best_cat] == 0:
        return 'general', '', ''

    # ── Build hint ──
    if best_cat == 'marketing':
        sub = max(scores_mkt, key=scores_mkt.get)
        hints = {
            'research': (
                "ROUTING → МАРКЕТИНГ: Исследование рынка\n"
                "• Анализ конкурентов / скрапинг: google/gemini-3.5-flash + Firecrawl MCP\n"
                "• Глубокий анализ аудитории: anthropic/claude-sonnet-4.6\n"
                "• Тренды и актуальные данные: x-ai/grok-4.3\n"
                "• Большие отчёты / PDF: google/gemini-3.1-pro-preview"
            ),
            'content': (
                "ROUTING → МАРКЕТИНГ: Создание контента\n"
                "• Длинные статьи / лонгриды: anthropic/claude-opus-4.8\n"
                "• Посты, письма, короткий копирайтинг: anthropic/claude-sonnet-4.6\n"
                "• Мультиязычный контент (RU/EN/CN): qwen/qwen3.7-max\n"
                "• Email-цепочки: anthropic/claude-sonnet-4.6"
            ),
            'ads': (
                "ROUTING → МАРКЕТИНГ: Реклама\n"
                "• Рекламные тексты / CTA: anthropic/claude-sonnet-4.6\n"
                "• Массовая генерация вариантов: deepseek/deepseek-v4-flash\n"
                "• Анализ эффективности кампаний: deepseek/deepseek-v4-pro\n"
                "• Креативные концепции: anthropic/claude-opus-4.8"
            ),
            'seo': (
                "ROUTING → МАРКЕТИНГ: SEO\n"
                "• Семантика / кластеры ключей: deepseek/deepseek-v4-pro\n"
                "• SEO-тексты: anthropic/claude-sonnet-4.6\n"
                "• Массовые мета-теги: deepseek/deepseek-v4-flash\n"
                "• Технический аудит сайта через Firecrawl: google/gemini-3.5-flash"
            ),
            'brand': (
                "ROUTING → МАРКЕТИНГ: Бренд и позиционирование\n"
                "• Tone of voice / brand identity: anthropic/claude-opus-4.8\n"
                "• УТП, слоганы, нейминг: anthropic/claude-sonnet-4.6\n"
                "• Концепции и варианты: minimax/minimax-m3"
            ),
            'analytics': (
                "ROUTING → МАРКЕТИНГ: Аналитика\n"
                "• Расчёты метрик (ROAS, CPA, ROI): deepseek/deepseek-v4-pro\n"
                "• Отчёты и дашборды: deepseek/deepseek-v4-pro\n"
                "• Интерпретация данных: anthropic/claude-sonnet-4.6\n"
                "• Когортный анализ: deepseek/deepseek-v4-pro"
            ),
            'social': (
                "ROUTING → МАРКЕТИНГ: Соцсети\n"
                "• Посты и сторис: anthropic/claude-sonnet-4.6\n"
                "• Контент-планы: anthropic/claude-sonnet-4.6\n"
                "• Мультиязычные посты: qwen/qwen3.7-max\n"
                "• Тренды платформ: x-ai/grok-4.3"
            ),
        }
        return 'marketing', sub, hints.get(sub, hints['content'])

    elif best_cat == 'code':
        # 'large' is more specific — boost it to break ties
        if scores_code['large'] > 0:
            scores_code['large'] += 0.5
        sub = max(scores_code, key=scores_code.get)
        hints = {
            'arch': (
                "ROUTING → КОД: Архитектура\n"
                "• Проектирование системы: anthropic/claude-opus-4.8\n"
                "• Выбор паттернов / технологий: anthropic/claude-sonnet-4.6\n"
                "• Диаграммы и схемы: google/gemini-3.1-pro-preview"
            ),
            'write': (
                "ROUTING → КОД: Написание кода\n"
                "• Реализация / имплементация: deepseek/deepseek-v4-pro\n"
                "• Быстрый boilerplate: deepseek/deepseek-v4-flash\n"
                "• Сложная логика: anthropic/claude-sonnet-4.6\n"
                "• Алгоритмы / математика: qwen/qwen3.7-max"
            ),
            'review': (
                "ROUTING → КОД: Ревью\n"
                "• Глубокий code review: anthropic/claude-sonnet-4.6\n"
                "• Security audit: anthropic/claude-opus-4.8\n"
                "• Быстрая проверка синтаксиса: deepseek/deepseek-v4-flash"
            ),
            'debug': (
                "ROUTING → КОД: Отладка\n"
                "• Анализ ошибки / traceback: deepseek/deepseek-v4-pro\n"
                "• Сложный баг: anthropic/claude-sonnet-4.6\n"
                "• Race conditions / memory: anthropic/claude-opus-4.8"
            ),
            'test': (
                "ROUTING → КОД: Тесты\n"
                "• Unit / integration тесты: deepseek/deepseek-v4-pro\n"
                "• Тестовые сценарии / edge cases: anthropic/claude-sonnet-4.6\n"
                "• Моки и фикстуры: deepseek/deepseek-v4-pro"
            ),
            'docs': (
                "ROUTING → КОД: Документация\n"
                "• README / docstrings: anthropic/claude-sonnet-4.6\n"
                "• Массовые комментарии: deepseek/deepseek-v4-flash"
            ),
            'large': (
                "ROUTING → КОД: Большая кодовая база\n"
                "• Анализ всего репозитория: google/gemini-3.1-pro-preview (1M ctx)\n"
                "• Поиск по коду: google/gemini-3.1-pro-preview\n"
                "• Рефакторинг с контекстом: anthropic/claude-sonnet-4.6"
            ),
        }
        return 'code', sub, hints.get(sub, hints['write'])

    elif best_cat == 'research':
        sub = max(scores_res, key=scores_res.get)
        hints = {
            'web': (
                "ROUTING → РЕСЁРЧ: Веб-поиск\n"
                "• Скрапинг / Firecrawl: google/gemini-3.5-flash\n"
                "• Синтез найденного: anthropic/claude-sonnet-4.6"
            ),
            'docs': (
                "ROUTING → РЕСЁРЧ: Документы\n"
                "• Большие PDF / файлы: google/gemini-3.1-pro-preview\n"
                "• Краткое резюме: deepseek/deepseek-v4-pro"
            ),
            'trends': (
                "ROUTING → РЕСЁРЧ: Тренды\n"
                "• Актуальные события: x-ai/grok-4.3\n"
                "• Аналитика трендов: anthropic/claude-sonnet-4.6"
            ),
        }
        return 'research', sub, hints.get(sub, hints['web'])

    else:  # creative
        hint = (
            "ROUTING → КРЕАТИВ\n"
            "• Идеи / концепции: anthropic/claude-sonnet-4.6\n"
            "• Нарратив / сценарии: anthropic/claude-opus-4.8\n"
            "• Мультиязычный creativ: qwen/qwen3.7-max"
        )
        return 'creative', '', hint


def build_complexity_hint(reason: str) -> str:
    """v2: Рекомендация по сложной задаче, когда категория неясна."""
    return (
        f"ROUTING → СЛОЖНАЯ ЗАДАЧА ({reason})\n"
        "• Глубокий анализ / рассуждение: anthropic/claude-opus-4.8\n"
        "• Альтернатива: google/gemini-3.1-pro-preview (1M контекст)\n"
        "• Синтез / нарратив: anthropic/claude-sonnet-4.6"
    )


def main():
    try:
        raw = sys.stdin.buffer.read().decode('utf-8-sig')  # strips BOM if present
        payload = json.loads(raw)
    except Exception:
        sys.exit(0)

    if payload.get('api_call_count', 0) > 1:
        sys.exit(0)

    # v2: Берём контекст последних 3 ходов для более точной классификации
    recent_text = get_recent_text(payload, turns=3)
    if not recent_text or len(recent_text) < 8:
        sys.exit(0)

    cat, sub, hint = classify(recent_text)

    if cat == 'general' or not hint:
        # v2: Fallback — если категория неясна, но задача сложная, даём подсказку
        is_cx, reason = is_complex(recent_text)
        if is_cx:
            hint = build_complexity_hint(reason)
            cat = 'complex'
        else:
            sys.exit(0)

    print(json.dumps({"context": hint}))


if __name__ == '__main__':
    main()
