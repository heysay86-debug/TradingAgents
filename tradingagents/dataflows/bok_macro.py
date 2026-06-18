"""한국은행 경제통계시스템(ECOS) 거시경제 벤더.

한국의 기준금리·물가·GDP·환율 등을 한국은행 ECOS 공식 API에서 가져옵니다.
FRED(fred.py)와 동일한 함수 시그니처(get_macro_data)를 구현해
라우팅 레이어에서 쉽게 교체·병행 사용할 수 있습니다.

무료 API 키 발급:
    https://ecos.bok.or.kr/ → 서비스 → Open API → 인증키 신청

환경변수:
    BOK_API_KEY — 한국은행 ECOS Open API 인증키

키가 미설정이면 BokNotConfiguredError(VendorNotConfiguredError)를 발생시켜
라우팅 레이어가 다른 벤더로 자동 폴백합니다.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

import requests

from .errors import VendorNotConfiguredError

logger = logging.getLogger(__name__)

BOK_API_BASE = "https://ecos.bok.or.kr/api"
REQUEST_TIMEOUT = 30
DEFAULT_LOOKBACK_DAYS = 365
MAX_ROWS = 40

# 주기 코드 → ECOS 날짜 포맷
_PERIOD_FMT = {
    "DD": "%Y%m%d",   # 일별
    "MM": "%Y%m",     # 월별
    "QQ": "%Y%m",     # 분기별 (YYYYQQ 형식이 아닌 YYYYMM 입력)
    "YY": "%Y",       # 연별
}

# (stat_code, period, item_code1) 형태의 ECOS 시리즈 매핑.
# 별칭은 소문자 + 언더스코어 정규화 후 조회됩니다.
# 코드 검색: https://ecos.bok.or.kr/#/StatisticSearch
MACRO_SERIES: dict[str, tuple[str, str, str]] = {
    # 기준금리
    "base_rate":         ("722Y001", "MM", "0101000"),
    "기준금리":           ("722Y001", "MM", "0101000"),
    "bok_rate":          ("722Y001", "MM", "0101000"),
    # 소비자물가지수 (2020=100)
    "cpi":               ("901Y009", "MM", "0"),
    "소비자물가":         ("901Y009", "MM", "0"),
    "inflation":         ("901Y009", "MM", "0"),
    # 실질 GDP (계절조정, 전기비)
    "gdp":               ("200Y002", "QQ", "10111"),
    "real_gdp":          ("200Y002", "QQ", "10111"),
    "실질gdp":           ("200Y002", "QQ", "10111"),
    # 실업률
    "unemployment":      ("036Y001", "MM", "9020000"),
    "unemployment_rate": ("036Y001", "MM", "9020000"),
    "실업률":            ("036Y001", "MM", "9020000"),
    # 원/달러 환율 (매매기준율)
    "usd_krw":           ("731Y003", "DD", "0000001"),
    "exchange_rate":     ("731Y003", "DD", "0000001"),
    "환율":              ("731Y003", "DD", "0000001"),
    "원달러":            ("731Y003", "DD", "0000001"),
    # M2 광의통화 (평잔)
    "m2":                ("101Y004", "MM", "BBGA00"),
    "money_supply":      ("101Y004", "MM", "BBGA00"),
    "통화량":            ("101Y004", "MM", "BBGA00"),
    # 국고채 3년물 수익률
    "3y_treasury":       ("817Y002", "DD", "010200000"),
    "국고채3년":          ("817Y002", "DD", "010200000"),
    "bond_3y":           ("817Y002", "DD", "010200000"),
    # 수출입 (통관기준, 억달러)
    "exports":           ("403Y003", "MM", "AAA"),
    "수출":              ("403Y003", "MM", "AAA"),
    "imports":           ("403Y003", "MM", "BBB"),
    "수입":              ("403Y003", "MM", "BBB"),
    # 생산자물가지수
    "ppi":               ("404Y014", "MM", "AA"),
    "생산자물가":         ("404Y014", "MM", "AA"),
    # 취업자수 (천명)
    "employment":        ("036Y001", "MM", "9010000"),
    "취업자":            ("036Y001", "MM", "9010000"),
}


class BokNotConfiguredError(VendorNotConfiguredError):
    """BOK_API_KEY 환경변수 미설정 시 발생."""


def _get_api_key() -> str:
    key = os.getenv("BOK_API_KEY")
    if not key:
        raise BokNotConfiguredError(
            "BOK_API_KEY 환경변수가 설정되지 않았습니다. "
            "https://ecos.bok.or.kr/ 에서 무료로 발급받으세요."
        )
    return key


def _resolve_series(indicator: str) -> tuple[str, str, str]:
    """별칭 또는 직접 코드를 (stat_code, period, item_code)로 변환합니다.

    직접 코드 입력 형식: '722Y001/MM/0101000'
    """
    key = indicator.strip().lower().replace(" ", "_").replace("-", "_")
    if key in MACRO_SERIES:
        return MACRO_SERIES[key]
    parts = indicator.strip().split("/")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    raise ValueError(
        f"알 수 없는 지표: '{indicator}'. "
        f"사용 가능한 별칭: {sorted(MACRO_SERIES.keys())} "
        f"또는 '통계코드/주기/항목코드' 형식으로 직접 입력하세요."
    )


def _ecos_date(dt: datetime, period: str) -> str:
    """datetime을 ECOS API 날짜 문자열로 변환합니다."""
    if period == "QQ":
        quarter = (dt.month - 1) // 3 + 1
        return f"{dt.year}{quarter:02d}"
    fmt = _PERIOD_FMT.get(period, "%Y%m")
    return dt.strftime(fmt)


def get_macro_data(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> str:
    """한국은행 ECOS에서 거시경제 지표를 가져와 마크다운 리포트로 반환합니다.

    Args:
        indicator: 별칭('기준금리', 'cpi', 'usd_krw', 'gdp' 등) 또는
                   ECOS 직접 코드('722Y001/MM/0101000' 형식).
        curr_date: 조회 기준일 (yyyy-mm-dd). 이 날짜 이후 데이터는 반환하지 않음.
        look_back_days: 조회 기간(일). None이면 DEFAULT_LOOKBACK_DAYS(365일) 사용.

    Returns:
        지표 요약과 관측값 테이블을 담은 마크다운 문자열.
    """
    if look_back_days is None:
        look_back_days = DEFAULT_LOOKBACK_DAYS

    api_key = _get_api_key()
    stat_code, period, item_code = _resolve_series(indicator)

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=look_back_days)
    start_str = _ecos_date(start_dt, period)
    end_str = _ecos_date(end_dt, period)

    url = (
        f"{BOK_API_BASE}/StatisticSearch/{api_key}/json/kr"
        f"/1/1000/{stat_code}/{period}/{start_str}/{end_str}/{item_code}"
    )

    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return f"<한국은행 ECOS API 오류: {exc}>"

    search_result = data.get("StatisticSearch", {})

    # ECOS INFO-200: 조회 결과 없음
    if search_result.get("CODE") == "INFO-200":
        return (
            f"<ECOS에서 '{indicator}' ({stat_code}) 데이터를 찾을 수 없습니다. "
            f"통계코드 또는 항목코드를 확인해 주세요.>"
        )

    rows = search_result.get("row", [])
    if not rows:
        return (
            f"<'{indicator}' 조회 결과가 없습니다 "
            f"(기간: {start_str} ~ {end_str}).>"
        )

    stat_name = rows[0].get("STAT_NAME", stat_code)
    item_name = rows[0].get("ITEM_NAME1", item_code)
    unit = rows[0].get("UNIT_NAME", "")

    points = [
        (r["TIME"], r["DATA_VALUE"])
        for r in rows
        if r.get("DATA_VALUE") not in (None, "", " ")
    ]

    header = (
        f"## 한국은행 ECOS: {stat_name} — {item_name}\n"
        f"- 통계코드: {stat_code} / 항목: {item_code}\n"
        f"- 단위: {unit} | 주기: {period}\n"
        f"- 조회기간: {start_str} ~ {end_str}\n"
    )

    if not points:
        return header + "\n해당 기간에 유효한 데이터가 없습니다."

    first_date, first_val = points[0]
    last_date, last_val = points[-1]
    try:
        delta = float(last_val) - float(first_val)
        base = float(first_val)
        pct = f" ({delta / base * 100:+.2f}%)" if base != 0 else ""
        summary = (
            f"\n**최신값:** {last_val} {unit} ({last_date}) | "
            f"**기간 변화:** {delta:+.4f}{pct} "
            f"(기준 {first_val} @ {first_date})\n"
        )
    except ValueError:
        summary = f"\n**최신값:** {last_val} {unit} ({last_date})\n"

    shown = points
    note = ""
    if len(points) > MAX_ROWS:
        shown = points[-MAX_ROWS:]
        note = f"\n_(최근 {MAX_ROWS}개 표시, 전체 {len(points)}개 관측값)_\n"

    table = (
        "\n| 기간 | 값 |\n| --- | --- |\n"
        + "\n".join(f"| {d} | {v} |" for d, v in shown)
        + "\n"
    )

    return header + summary + note + table
