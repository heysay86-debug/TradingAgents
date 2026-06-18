from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_global_news,
    get_instrument_context_from_state,
    get_language_instruction,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
)


def _is_korean_ticker(state) -> bool:
    """종목 코드가 한국 거래소(.KS/.KQ) 소속인지 판별합니다."""
    ticker = str(state.get("company_of_interest", ""))
    return ticker.upper().endswith(".KS") or ticker.upper().endswith(".KQ")


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        instrument_context = get_instrument_context_from_state(state)
        korean_market = _is_korean_ticker(state)

        tools = [
            get_news,
            get_global_news,
            get_macro_indicators,
            get_prediction_markets,
        ]

        if korean_market:
            macro_instruction = (
                "get_macro_indicators(indicator, curr_date, look_back_days) to ground macro commentary "
                "in actual data using a TWO-TRACK approach — call it at least twice: "
                "(1) Korean domestic indicators via BOK ECOS (e.g. '기준금리' for Bank of Korea base rate, "
                "'usd_krw' or '환율' for USD/KRW exchange rate, 'cpi' for Korean CPI, "
                "'3y_treasury' or '국고채3년' for 3-year Korean treasury yield, "
                "'exports' for Korean exports); "
                "(2) Global/US indicators via FRED (e.g. 'fed_funds_rate', '10y_treasury', "
                "'vix', 'dollar_index', 'real_gdp'). "
                "Synthesize both tracks to explain how global macro conditions "
                "(USD strength, US rates, risk-off sentiment) interact with domestic Korean "
                "macro conditions (BOK policy, KRW moves, trade balance) to affect this stock."
            )
            news_instruction = (
                f"get_news for {asset_label}-specific news searches "
                f"(try both Korean and English queries for broader coverage), "
                f"get_global_news for Korean market macro headlines"
            )
        else:
            macro_instruction = (
                "get_macro_indicators(indicator, curr_date, look_back_days) to ground macro commentary "
                "in actual data from FRED (e.g. 'cpi', 'core_pce', 'unemployment', "
                "'fed_funds_rate', '10y_treasury', 'yield_curve')"
            )
            news_instruction = (
                f"get_news for {asset_label}-specific or targeted news searches, "
                f"get_global_news for broader macroeconomic news"
            )

        system_message = (
            f"You are a news researcher tasked with analyzing recent news and trends over the past week. "
            f"Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. "
            f"Use the available tools: {news_instruction}, "
            f"{macro_instruction}, "
            f"and get_prediction_markets(topic, limit) for live market-implied probabilities of forward-looking events "
            f"(e.g. 'Fed rate cut', 'recession 2026', geopolitical or sector events). "
            f"Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node
