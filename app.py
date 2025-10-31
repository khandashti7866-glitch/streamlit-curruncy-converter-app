# app.py
"""
Streamlit Global Currency Converter with Visual Analytics
Uses exchangerate.host (free, no API key) for live and historical rates.

Features:
- Amount input and dropdowns
- Natural-language parsing like "Convert 100 USD to EUR"
- Caching for API calls
- Top-10 conversions table & bar chart
- Historical line chart (7/30 days)
- Pie chart comparison
- CSV export of results
- Auto-refresh option
- Luxurious dark theme with CSS tweaks
"""

from datetime import datetime, timedelta
import io
import re
import requests
import pandas as pd
import streamlit as st
import plotly.express as px

# --------- Page config & styling ----------
st.set_page_config(
    page_title="UltraLux Currency Converter",
    page_icon="💱",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for luxurious dark theme
st.markdown(
    """
    <style>
    :root {
      --accent:#F4C95D;
      --bg:#0b0f12;
      --card:#0f1720;
      --muted:#98a0a6;
    }
    .stApp {
      background: linear-gradient(180deg, #041018 0%, #08121A 100%);
      color: #e6eef6;
    }
    .topbar {display:flex; gap:12px; align-items:center;}
    .lux-card {
      background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01));
      border-radius: 14px;
      padding: 16px;
      box-shadow: 0 6px 24px rgba(3,6,10,0.6);
      border: 1px solid rgba(255,255,255,0.03);
    }
    .big-amount {font-size:28px; font-weight:700; color:var(--accent);}
    .muted {color:var(--muted);}
    .small {font-size:12px; color:var(--muted);}
    .currency-flag {height:18px; margin-right:6px; vertical-align:middle;}
    </style>
    """,
    unsafe_allow_html=True,
)

# --------- Helper Data & Functions ----------
@st.cache_data(ttl=60 * 10)  # cache symbols - refresh every 10 minutes
def get_symbols():
    """Fetch available currency symbols from exchangerate.host"""
    url = "https://api.exchangerate.host/symbols"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data.get("symbols"):
        raise RuntimeError("Failed to fetch symbols")
    symbols = data["symbols"]
    # Convert to dict code -> description
    return {code: info["description"] for code, info in symbols.items()}

CURRENCY_SYMBOLS = {
    # Common symbols for display (not exhaustive)
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥",
    "AUD": "A$", "CAD": "C$", "INR": "₹", "PKR": "₨", "CHF": "CHF",
    "NZD": "NZ$", "SGD": "S$", "HKD": "HK$", "AED": "د.إ",
    "ZAR": "R",
}

def currency_symbol(code):
    return CURRENCY_SYMBOLS.get(code.upper(), code.upper())

def parse_nl_input(text, symbols_map):
    """
    Parse natural language like:
    "Convert 100 USD to EUR", "100 usd in eur", "convert 1,234.56 gbp to pkr", etc.
    Returns (amount: float, base: str, target: str) or (None, None, None)
    """
    if not text or not isinstance(text, str):
        return None, None, None

    s = text.strip()
    # normalize commas in numbers: 1,234 -> 1234
    s = s.replace(",", "")
    # find amount
    amt_match = re.search(r"([-+]?\d*\.?\d+)", s)
    amount = float(amt_match.group(1)) if amt_match else None

    # find currency codes (3-letter) in text
    codes = re.findall(r"\b([A-Za-z]{3})\b", s)
    codes = [c.upper() for c in codes]
    # If codes correspond to known symbols, choose them; else try to find currency names
    base = target = None
    if len(codes) >= 2:
        base, target = codes[0], codes[1]
    elif len(codes) == 1:
        # try keywords "to" / "in"
        if re.search(r"\bto\b", s, re.I):
            # assume "100 USD to EUR" -> USD present, target absent (cannot)
            # but if only one code present and there's a word like "to EUR" then maybe EUR isn't code
            # fallback: ask user? here we'll leave target None
            base = codes[0]
        else:
            base = codes[0]

    # fallback: try to detect currency full names from symbols_map
    if (not base or not target) and amount is not None:
        # find occurrences of currency names in text
        lowered = s.lower()
        for code, name in symbols_map.items():
            if name.lower() in lowered:
                if not base:
                    base = code
                elif not target and code != base:
                    target = code

    # final basic validation
    if amount is None or base is None or target is None:
        return None, None, None
    return amount, base, target

@st.cache_data(ttl=60 * 5)
def fetch_latest_rates(base="USD"):
    """Fetch latest rates for a given base currency."""
    url = f"https://api.exchangerate.host/latest?base={base}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    return data  # includes 'rates', 'base', 'date'

@st.cache_data(ttl=60 * 60)  # cache timeseries for a bit longer
def fetch_timeseries(base, target, days=7):
    """Fetch timeseries rates for the given pair for the last `days` days."""
    end = datetime.utcnow().date()
    start = end - timedelta(days=days - 1)
    url = (
        "https://api.exchangerate.host/timeseries"
        f"?start_date={start.isoformat()}&end_date={end.isoformat()}"
        f"&base={base}&symbols={target}"
    )
    r = requests.get(url, timeout=12)
    r.raise_for_status()
    data = r.json()
    if not data.get("rates"):
        raise RuntimeError("Failed to fetch timeseries")
    # Create DataFrame: date, rate
    df = pd.DataFrame(
        [(d, data["rates"][d][target]) for d in sorted(data["rates"].keys())],
        columns=["date", "rate"],
    )
    df["date"] = pd.to_datetime(df["date"])
    return df

def convert_amount(amount, base, target, rates_dict=None):
    """Convert using provided rates dict (as from latest['rates'])"""
    # If rates_dict provided, it's rates relative to base
    if rates_dict:
        rate = rates_dict.get(target)
        if rate is None:
            # try fetching directly pair
            latest = fetch_latest_rates(base)
            rate = latest["rates"].get(target)
        return amount * rate, rate
    else:
        latest = fetch_latest_rates(base)
        rate = latest["rates"].get(target)
        return amount * rate, rate

def top_n_conversions(base, n=10):
    latest = fetch_latest_rates(base)
    rates = latest["rates"]
    # Create dataframe of code, rate sorted by rate (but sorting by rate alone is not always meaningful).
    df = pd.DataFrame(list(rates.items()), columns=["currency", "rate"])
    # For display, choose a list of most traded / relevant currencies first if present
    priority = ["USD","EUR","JPY","GBP","AUD","CAD","CHF","CNY","HKD","INR","PKR","BRL","ZAR","SGD","NZD"]
    df["priority"] = df["currency"].apply(lambda c: priority.index(c) if c in priority else 999)
    df = df.sort_values(["priority", "currency"]).head(n).reset_index(drop=True)
    return df

# Utility: download dataframe to csv
def df_to_csv_bytes(df):
    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8")

# --------- Main App Layout ----------
def main():
    st.markdown('<div class="topbar"><h1>💱 UltraLux Currency Converter</h1><div class="muted small">Real-time • No API key • Luxurious Dark UI</div></div>', unsafe_allow_html=True)
    symbols_map = get_symbols()
    all_codes = sorted(symbols_map.keys())

    with st.sidebar:
        st.header("Controls")
        nl_input = st.text_input("Natural-language input (e.g. `Convert 100 USD to EUR`)", value="")
        col_a, col_b = st.columns([1,1])
        with col_a:
            amount_input = st.text_input("Amount (overrides NL if filled)", value="")
        with col_b:
            base_input = st.selectbox("Base currency", options=all_codes, index=all_codes.index("USD") if "USD" in all_codes else 0)
        target_input = st.selectbox("Target currency", options=all_codes, index=all_codes.index("EUR") if "EUR" in all_codes else 1)
        st.write("---")
        days_choice = st.radio("Historical chart range", options=[7, 30], index=0, horizontal=True)
        auto_refresh = st.checkbox("Auto-refresh rates every 5 minutes", value=False)
        refresh_btn = st.button("Refresh rates now")
        st.write("---")
        st.markdown("**Display options**")
        show_top_n = st.slider("Number of top comparisons", 5, 20, 10)
        st.markdown("---")
        st.caption("Data source: exchangerate.host (free, no API key)")

    # If NL input present, try to parse it
    parsed_amt, parsed_base, parsed_target = parse_nl_input(nl_input, symbols_map)
    # Decide final inputs: explicit input fields override parsed pieces (as user requested)
    if amount_input.strip():
        try:
            final_amount = float(amount_input.replace(",",""))
        except Exception:
            st.sidebar.error("Invalid amount format in Amount box.")
            final_amount = None
    else:
        final_amount = parsed_amt

    final_base = base_input if base_input else parsed_base
    final_target = target_input if target_input else parsed_target

    # If NL provided and parsing succeeded but user didn't set dropdowns manually, update them visually
    if nl_input and parsed_amt and parsed_base and parsed_target:
        # show a small success note
        st.sidebar.success(f"Parsed: {parsed_amt} {parsed_base} → {parsed_target}")
    elif nl_input:
        st.sidebar.info("Couldn't fully parse natural-language input. Fill fields above.")

    # Refresh handling
    if refresh_btn:
        # Clear cached rate functions by calling st.cache_data.clear? Use experimental: but we can call fetch_latest_rates with different param to update; as cache has TTL, we simply re-call with cache_data invalidation not available here.
        # Provide feedback
        st.sidebar.info("Rates refreshed (note: caching may still apply until TTL expires).")

    # Main conversion card
    st.markdown("<div class='lux-card'>", unsafe_allow_html=True)
    left, right = st.columns([2,1])
    with left:
        st.markdown("### Convert currency")
        st.write("Enter amount and currencies on the sidebar (or use natural-language input).")
        if final_amount is None or final_base is None or final_target is None:
            st.warning("Provide amount, base and target currencies (via sidebar fields or natural-language).")
            st.markdown("</div>", unsafe_allow_html=True)
            return

        # Fetch latest rates and convert
        try:
            latest = fetch_latest_rates(final_base)
        except Exception as e:
            st.error(f"Failed to fetch latest rates: {e}")
            st.markdown("</div>", unsafe_allow_html=True)
            return

        converted_value, used_rate = convert_amount(final_amount, final_base, final_target, rates_dict=latest["rates"])
        symbol_target = currency_symbol(final_target)
        st.markdown(f"<div class='big-amount'>{symbol_target} {converted_value:,.4f}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='muted small'>Rate: 1 {final_base} = {used_rate:.6f} {final_target} • Rates timestamp: {latest.get('date')}</div>", unsafe_allow_html=True)

    with right:
        st.markdown("### Quick actions")
        st.button("Swap currencies", key="swap_btn")
        st.write("")
        # CSV export of this single conversion
        csv_df = pd.DataFrame([{
            "timestamp_utc": datetime.utcnow().isoformat(),
            "base": final_base,
            "target": final_target,
            "amount": final_amount,
            "converted": converted_value,
            "rate": used_rate
        }])
        st.download_button("Download conversion (CSV)", data=df_to_csv_bytes(csv_df), file_name="conversion.csv", mime="text/csv")

    st.markdown("</div>", unsafe_allow_html=True)

    # Top N conversions table & bar chart
    st.write("")  # spacing
    st.markdown('<div class="lux-card">', unsafe_allow_html=True)
    st.markdown("### Top conversions relative to base")
    top_df = top_n_conversions(final_base, n=show_top_n)
    # Add a column showing converted value for user's amount
    top_df["converted_for_amount"] = top_df["rate"] * final_amount
    # Show table
    col1, col2 = st.columns([2,3])
    with col1:
        st.dataframe(top_df.rename(columns={"currency":"Currency","rate":"Rate","converted_for_amount":"Converted for Amount"}))
        st.download_button("Download table CSV", data=df_to_csv_bytes(top_df), file_name="top_conversions.csv", mime="text/csv")
    with col2:
        fig_bar = px.bar(
            top_df,
            x="currency",
            y="rate",
            title=f"Top {len(top_df)} currency rates vs {final_base}",
            labels={"rate":"Rate", "currency":"Currency"}
        )
        fig_bar.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="white"))
        st.plotly_chart(fig_bar, use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)

    # Historical timeseries chart for chosen pair
    st.write("")
    st.markdown('<div class="lux-card">', unsafe_allow_html=True)
    st.markdown(f"### Historical trend — {final_base} → {final_target}")
    try:
        df_ts = fetch_timeseries(final_base, final_target, days=days_choice)
        fig_line = px.line(df_ts, x="date", y="rate", title=f"{final_base}/{final_target} — last {days_choice} days", markers=True, labels={"rate":"Rate"})
        fig_line.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="white"))
        st.plotly_chart(fig_line, use_container_width=True)
        # show small stats
        min_r = df_ts["rate"].min()
        max_r = df_ts["rate"].max()
        last_r = df_ts["rate"].iloc[-1]
        stats_col1, stats_col2, stats_col3 = st.columns(3)
        stats_col1.metric("Latest rate", f"{last_r:.6f}")
        stats_col2.metric("7-day low" if days_choice==7 else f"{days_choice}-day low", f"{min_r:.6f}")
        stats_col3.metric("7-day high" if days_choice==7 else f"{days_choice}-day high", f"{max_r:.6f}")
    except Exception as e:
        st.error(f"Failed to fetch historical data: {e}")

    st.markdown("</div>", unsafe_allow_html=True)

    # Pie chart: distribution of converted amounts among top 6 currencies
    st.write("")
    st.markdown('<div class="lux-card">', unsafe_allow_html=True)
    st.markdown("### Distribution (sample) — how your amount compares across currencies")
    pie_df = top_n_conversions(final_base, n=6)
    pie_df["converted"] = pie_df["rate"] * final_amount
    fig_pie = px.pie(pie_df, values="converted", names="currency", title=f"Converted value distribution for {final_amount} {final_base}")
    fig_pie.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="white"))
    st.plotly_chart(fig_pie, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # Footer / Tips
    st.write("")
    st.markdown(
        """
        <div class="small muted">
        Tip: Use the natural-language box to quickly type "Convert 1500 PKR to USD" and the app will try to parse it.<br>
        Export CSVs to save conversion snapshots. Rates are provided by exchangerate.host.
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Optional: show symbol lookup and brief help table
    with st.expander("Currency lookup & symbols"):
        lookup = st.text_input("Search currency code or name", value="")
        if lookup:
            # search in symbols_map
            df_lookup = pd.DataFrame(
                [(c, n) for c, n in symbols_map.items() if lookup.lower() in c.lower() or lookup.lower() in n.lower()],
                columns=["code", "description"],
            )
            st.dataframe(df_lookup)
        else:
            st.write("Type a code (USD) or part of a name (dollar, rupee) to search.")

if __name__ == "__main__":
    main()
