from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_macro_indicators(
    indicator: Annotated[
        str,
        "Macro indicator alias or series ID. "
        "Global/US (FRED): 'cpi', 'core_pce', 'unemployment', 'fed_funds_rate', "
        "'10y_treasury', 'yield_curve', 'real_gdp', 'vix', 'dollar_index', "
        "or a raw FRED series ID such as 'CPIAUCSL'. "
        "Korean (BOK ECOS): '기준금리', 'usd_krw', '환율', 'base_rate', "
        "'3y_treasury', '국고채3년', 'exports', '수출', 'imports', '수입', "
        "or a raw ECOS code such as '722Y001/MM/0101000'.",
    ],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format; the end of the window"],
    look_back_days: Annotated[
        int | None, "Trailing window length in days; omit for a 1-year window"
    ] = None,
) -> str:
    """
    Retrieve a macroeconomic indicator time series.

    Supports two data sources depending on the configured macro_data vendor:
    - FRED (Federal Reserve Economic Data): US/global indicators — policy rates,
      Treasury yields, inflation (CPI/PCE), labor market, GDP, VIX, dollar index.
    - BOK ECOS (한국은행 경제통계시스템): Korean indicators — base rate (기준금리),
      CPI, GDP, USD/KRW exchange rate, Korean treasury yields, trade data (exports/imports).

    When analyzing Korean stocks (.KS / .KQ), call this tool TWICE:
    once for a key global indicator (e.g. 'fed_funds_rate') and once for a
    key Korean indicator (e.g. '기준금리' or 'usd_krw') to capture both
    the domestic and global macro environment.

    Args:
        indicator (str): Friendly alias or raw series ID (FRED or BOK ECOS)
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Trailing window length; omit for a 1-year window

    Returns:
        str: A formatted markdown report of the macro series
    """
    return route_to_vendor("get_macro_indicators", indicator, curr_date, look_back_days)
