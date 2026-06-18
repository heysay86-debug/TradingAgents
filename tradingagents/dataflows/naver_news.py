"""네이버 뉴스 검색 API 기반 국내 뉴스 벤더.

네이버 공식 검색 API를 사용해 종목별/거시경제 뉴스를 가져옵니다.
API 키는 https://developers.naver.com 에서 무료로 발급받을 수 있습니다.
(1일 25,000회 무료)

환경변수:
    NAVER_CLIENT_ID     — 네이버 개발자센터 애플리케이션 Client ID
    NAVER_CLIENT_SECRET — 네이버 개발자센터 애플리케이션 Client Secret

키가 미설정이면 VendorNotConfiguredError를 발생시켜 라우팅 레이어가
다른 벤더(yfinance 등)로 자동 폴백합니다.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import ssl
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi

from .errors import VendorNotConfiguredError

logger = logging.getLogger(__name__)

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
_NEWS_API = "https://openapi.naver.com/v1/search/news.json"
REQUEST_TIMEOUT = 15
DEFAULT_LOOKBACK_DAYS = 7

# 국내 거시경제 뉴스용 기본 검색어
_GLOBAL_QUERIES = [
    "한국은행 기준금리 통화정책",
    "코스피 코스닥 증시 전망",
    "한국 경제 GDP 물가 성장률",
    "외국인 기관 수급 매매 동향",
    "원달러 환율 외환",
]


class NaverNotConfiguredError(VendorNotConfiguredError):
    """NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET 미설정 시 발생."""


def _get_credentials() -> tuple[str, str]:
    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise NaverNotConfiguredError(
            "NAVER_CLIENT_ID 및 NAVER_CLIENT_SECRET 환경변수가 필요합니다. "
            "https://developers.naver.com 에서 애플리케이션을 등록하고 "
            "검색 API 권한을 추가하면 무료로 발급받을 수 있습니다."
        )
    return client_id, client_secret


def _strip_html(text: str) -> str:
    """네이버 API 응답의 HTML 태그 및 엔티티를 제거합니다."""
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _parse_pub_date(date_str: str) -> datetime | None:
    """'Mon, 01 Jan 2024 12:00:00 +0900' 형식의 날짜를 파싱합니다."""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str).replace(tzinfo=None)
    except Exception:
        return None


def _ticker_to_query(ticker: str) -> str:
    """티커 심볼을 검색어로 변환합니다.

    005930.KS → '삼성전자 005930' (yfinance로 회사명 조회 시도)
    조회 실패 시 종목코드만 반환합니다.
    """
    code = ticker.split(".")[0]
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        name = info.get("longName") or info.get("shortName") or ""
        if name:
            return f"{name} {code}"
    except Exception:
        pass
    return code


def _fetch_naver_news(query: str, display: int = 10) -> list[dict]:
    """네이버 뉴스 검색 API를 호출하고 결과 목록을 반환합니다."""
    client_id, client_secret = _get_credentials()
    qs = urlencode({"query": query, "display": display, "sort": "date"})
    req = Request(
        f"{_NEWS_API}?{qs}",
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        },
    )
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT, context=_SSL_CTX) as resp:
            return json.loads(resp.read()).get("items", [])
    except HTTPError as exc:
        logger.warning("Naver News API HTTP error: %s", exc)
        return []
    except Exception as exc:
        logger.warning("Naver News fetch failed: %s", exc)
        return []


def _format_item(item: dict, pub_date: datetime | None) -> str:
    title = _strip_html(item.get("title", ""))
    desc = _strip_html(item.get("description", ""))
    link = item.get("originallink") or item.get("link", "")
    date_str = pub_date.strftime("%Y-%m-%d") if pub_date else "날짜 미상"
    text = f"### {title} ({date_str})\n"
    if desc:
        text += f"{desc}\n"
    if link:
        text += f"Link: {link}\n"
    return text


def get_news_naver(ticker: str, start_date: str, end_date: str) -> str:
    """네이버 검색 API로 특정 종목의 국내 뉴스를 가져옵니다.

    Args:
        ticker: 종목 코드 (예: '005930.KS', '035720.KQ')
        start_date: 조회 시작일 (yyyy-mm-dd)
        end_date: 조회 종료일 (yyyy-mm-dd)

    Returns:
        뉴스 기사 목록을 담은 마크다운 문자열.
    """
    query = _ticker_to_query(ticker)
    items = _fetch_naver_news(query, display=20)

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    blocks = []
    for item in items:
        pub_date = _parse_pub_date(item.get("pubDate", ""))
        if pub_date and not (start_dt <= pub_date <= end_dt + timedelta(days=1)):
            continue
        blocks.append(_format_item(item, pub_date))

    if not blocks:
        return (
            f"<{ticker} 관련 네이버 뉴스를 {start_date}~{end_date} 기간에서 "
            f"찾을 수 없습니다>"
        )
    header = f"## {ticker} 네이버 뉴스 ({start_date} ~ {end_date}):\n\n"
    return header + "\n".join(blocks)


def get_global_news_naver(
    curr_date: str,
    look_back_days: int | None = None,
    limit: int | None = None,
) -> str:
    """네이버 검색 API로 국내 주요 거시경제 뉴스를 가져옵니다.

    Args:
        curr_date: 기준일 (yyyy-mm-dd)
        look_back_days: 조회 기간(일). None이면 DEFAULT_LOOKBACK_DAYS 사용.
        limit: 최대 기사 수. None이면 10.

    Returns:
        거시경제 뉴스 기사 목록을 담은 마크다운 문자열.
    """
    if look_back_days is None:
        look_back_days = DEFAULT_LOOKBACK_DAYS
    if limit is None:
        limit = 10

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = curr_dt - timedelta(days=look_back_days)

    seen_titles: set[str] = set()
    blocks: list[str] = []

    for query in _GLOBAL_QUERIES:
        items = _fetch_naver_news(query, display=5)
        for item in items:
            title = _strip_html(item.get("title", ""))
            if title in seen_titles:
                continue
            seen_titles.add(title)
            pub_date = _parse_pub_date(item.get("pubDate", ""))
            if pub_date and not (start_dt <= pub_date <= curr_dt + timedelta(days=1)):
                continue
            blocks.append(_format_item(item, pub_date))
            if len(blocks) >= limit:
                break
        if len(blocks) >= limit:
            break

    if not blocks:
        return f"<{curr_date} 기준 국내 거시경제 뉴스를 찾을 수 없습니다>"

    start_date = start_dt.strftime("%Y-%m-%d")
    return f"## 국내 거시경제 뉴스 ({start_date} ~ {curr_date}):\n\n" + "\n".join(blocks)
