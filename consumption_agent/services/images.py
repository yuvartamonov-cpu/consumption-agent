from __future__ import annotations

import html
import logging
import re
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


def _fetch_html(url: str) -> str:
    req = Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    with urlopen(req, timeout=12) as resp:
        return resp.read().decode('utf-8', errors='ignore')


def _clean_image_url(url: str) -> str:
    url = html.unescape(url or '')
    url = url.replace('\\/', '/')
    # Часто regex захватывает хвосты JSON/HTML после &quot;
    url = url.split('&quot;')[0].split('"')[0].split("'")[0].strip()
    return url


def find_product_image_urls(query: str) -> dict:
    """Best-effort: по 1-3 картинкам из Bing, Yandex, Pinterest."""
    result = {}
    q = quote_plus(query)

    # --- Bing: собираем несколько murl, берём первые 2 непохожие ---
    try:
        data = _fetch_html(f'https://www.bing.com/images/search?q={q}')
        murls = re.findall(r'&quot;murl&quot;:&quot;(.*?)&quot;', data) or re.findall(r'"murl"\s*:\s*"(.*?)"', data)
        murls = [_clean_image_url(u) for u in murls if u]
        seen = set()
        for u in murls:
            key = u.split('/')[-1][:30]
            if key not in seen:
                if 'Bing' not in result:
                    result['Bing'] = u
                elif 'Bing2' not in result:
                    result['Bing2'] = u
                seen.add(key)
            if 'Bing' in result and 'Bing2' in result:
                break
    except Exception as e:
        log.warning(f"Bing image search failed: {e}")

    # --- Yandex: img_href (оригинал) или avatars thumbnail ---
    try:
        data = _fetch_html(f'https://yandex.ru/images/search?text={q}')
        # Сначала ищем оригиналы
        img_hrefs = re.findall(r'"img_href":"(https?:\\/\\/[^"\\]+(?:\\.[^"\\]+)*)"', data)
        if img_hrefs:
            result['Yandex'] = _clean_image_url(img_hrefs[0])
        if not result.get('Yandex'):
            thumbs = re.findall(r'https://avatars\.mds\.yandex\.net/[^"<\\]+', data)
            if thumbs:
                result['Yandex'] = _clean_image_url(thumbs[0])
    except Exception as e:
        log.warning(f"Yandex image search failed: {e}")

    # --- Pinterest: часто есть прямые URL в og:image ---
    try:
        data = _fetch_html(f'https://www.pinterest.com/search/pins/?q={q}')
        # Pinterest отдаёт JSON в <script> с pin-images
        pin_imgs = re.findall(r'https://i\.pinimg\.com/originals/[a-z0-9/]+\.(?:jpg|png|webp)', data)
        if pin_imgs:
            seen = set()
            for url in pin_imgs:
                key = url.split('/')[-1][:25]
                if key not in seen:
                    if 'Pinterest' not in result:
                        result['Pinterest'] = url
                    seen.add(key)
    except Exception as e:
        log.warning(f"Pinterest search failed: {e}")

    # Google отказался от прямых URL. Вместо него пишем ссылку на поиск.
    if not result:
        result['Google'] = f'https://www.google.com/search?tbm=isch&q={q}'

    return result
