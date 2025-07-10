import logging
import time
import numpy as np
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)
from config import TELEGRAM_BOT_TOKEN
from multi_exchange_client import MultiExchangeClient, DeribitClient, OKXClient, BybitClient, ExchangeError
from risk import RiskCalculator
from strategies import (
    hedge_protective_put,
    covered_call,
    collar,
    delta_neutral
)
from portfolio_analytics import PortfolioAnalytics
from greeks import OptionType
from risk_viz import plot_var_histogram, plot_stress_scenarios


# single, shared clients & state
logger = logging.getLogger(__name__)
main_client = MultiExchangeClient()
deribit_client = DeribitClient()
okx_client = OKXClient()
bybit_client = BybitClient()
analytics = PortfolioAnalytics()

portfolio = {
    'asset': None,
    'spot': None,
    'perp': None,
    'threshold': 10.0,
    'freq': 60,
    'preferred_exchange': 'auto'
}
hedge_log: dict[str, list[dict]] = {}

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _cast_args(arg_vals: list[str], types: list[type]) -> list:
    """
    Convert each string in arg_vals to the corresponding type.
    Raises ValueError if count mismatches or conversion fails.
    """
    if len(arg_vals) != len(types):
        raise ValueError(f"expected {len(types)} args, got {len(arg_vals)}")
    return [t(v) for t, v in zip(types, arg_vals)]

def _get_client_for_exchange(exchange: str = 'auto'):
    """Get appropriate client based on exchange preference."""
    if exchange == 'deribit':
        return deribit_client
    elif exchange == 'okx':
        return okx_client
    elif exchange == 'bybit':
        return bybit_client
    else:
        return main_client  # Auto-select best exchange
    
def _format_price_summary(asset: str, prices: dict) -> str:
    """Format price summary across exchanges."""
    if not prices:
        return "No price data available"
    
    lines = []
    for exchange, price in prices.items():
        lines.append(f"  {exchange.upper()}: ${price:,.2f}")
    
    return "\n".join(lines)

def _calculate_trade_costs(asset: str, size: float, exchange: str = 'auto') -> dict:
    """Calculate estimated trading costs including slippage."""
    client = _get_client_for_exchange(exchange)
    
    # Determine trade side and instrument type
    side = 'buy' if size > 0 else 'sell'
    instrument_type = 'perpetual'  # Most hedges use perpetuals
    
    # Get slippage estimate
    slippage = client.estimate_slippage(asset, abs(size), side, exchange, instrument_type)
    
    # Get current price
    try:
        current_price = client.get_perpetual_price(asset)
    except:
        current_price = slippage.get('market_price', 0)
    
    # Calculate total costs
    notional = abs(size) * current_price
    slippage_cost = notional * (slippage['slippage_pct'] / 100)
    
    # Estimate trading fees (typical range)
    fee_rate = 0.0005  # 0.05% typical maker fee
    trading_fees = notional * fee_rate
    
    total_cost = slippage_cost + trading_fees
    
    return {
        'notional': notional,
        'slippage_pct': slippage['slippage_pct'],
        'slippage_cost': slippage_cost,
        'trading_fees': trading_fees,
        'total_cost': total_cost,
        'average_price': slippage.get('average_price', current_price),
        'market_price': current_price,
        'filled_pct': slippage.get('filled_pct', 100),
        'error': slippage.get('error')
    }

# --- /start & /help ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Multi-Exchange Spot Hedging Bot**\n\n"
        "**Core Commands:**\n"
        "/start — show this message\n"
        "/monitor_risk <asset> <spot> <perp> <thr%> — monitor risk\n"
        "/auto_hedge — choose & run hedge strategy\n"
        "/risk_report <asset> <spot> <perp> <days> <conf> — risk analysis\n"
        "/return_dist — view return distribution graph\n"
        "/stress_test — run stress test on asset graph\n"
        "/start_monitoring — auto monitor every 60s\n"
        "/stop_monitoring — stop monitoring\n"
        "/configure threshold=<%> freq=<sec> exchange=<name>\n"
        "/hedge_now <asset> <size> — manual hedge\n"
        "/hedge_status <asset> — current hedge status\n"
        "/hedge_history <asset> <n> — hedge history\n"
        "/portfolio_analytics — portfolio analytics\n\n"
        "**Multi-Exchange Commands:**\n"
        "/exchange_prices <asset> — compare prices across exchanges\n"
        "/market_summary <asset> — comprehensive market data\n"
        "/set_exchange <exchange> — set preferred exchange\n"
        "/exchange_status — show current exchange settings\n\n"
        "**Supported Exchanges:** Deribit, OKX, Bybit\n"
        "/help — same as /start"
    )

async def show_return_dist(update, ctx):
    series = main_client.get_historical_prices(portfolio['asset'], days=7)
    returns = np.diff(np.log(series))
    if len(returns) < 2:
        return await update.message.reply_text("Not enough data for return distribution.")
    img = plot_var_histogram(returns)
    await update.message.reply_photo(photo=img)

async def stress_test(update, ctx):
    series = main_client.get_historical_prices(portfolio['asset'], days=7)
    if len(series) < 2:
        return await update.message.reply_text("Not enough data for stress test.")
    img = plot_stress_scenarios(series, shocks=[-0.1, +0.1])
    await update.message.reply_photo(photo=img)


async def monitor_risk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    if len(args) != 4:
        return await update.message.reply_text(
            "Usage: /monitor_risk <asset> <spot_qty> <perp_qty> <thr%>\n"
            "Example: /monitor_risk BTC 1.0 -0.5 10 auto\n"
            "Exchanges: auto, deribit, okx, bybit"
        )
    asset = args[0].upper()
    exchange = args[4] if len(args) > 4 else portfolio['preferred_exchange']
    # normalizing fancy dashes
    raw = [arg.replace("–","-").replace("—","-") for arg in args[1:4]]
    try:
        spot_qty, perp_qty, threshold = _cast_args(raw, [float, float, float])
    except ValueError:
        return await update.message.reply_text(
            "❌ Spot, perp and threshold must be plain numbers (e.g. `-0.5`)."
        )

    portfolio.update({'asset': asset, 'spot': spot_qty, 'perp': perp_qty, 'threshold': threshold, 'preferred_exchange': exchange})

    try:
        client = _get_client_for_exchange(exchange)

        if exchange == 'auto':
            sp_price, spot_exchange = main_client.get_best_price(asset, 'spot')
            pp_price, perp_exchange = main_client.get_best_price(asset, 'perpetual')
            exchange_info = f"auto\n"
        else:
            sp_price = client.get_spot_price(asset)
            pp_price = client.get_perpetual_price(asset)
            exchange_info = f"📊 Exchange: {exchange.upper()}"
        
    except ExchangeError as e:
        return await update.message.reply_text(f"❌ Error fetching price data: {e}")

    spot_val = spot_qty * sp_price
    perp_val = perp_qty * pp_price
    rc = RiskCalculator(spot_val, perp_val, threshold)
    net_val = rc.net_delta()
    lim_val = rc.threshold_limit()
    costs = _calculate_trade_costs(asset, perp_qty, exchange)

    msg = (
        f"**{asset} Risk Monitor**\n"
        f"{exchange_info}\n\n"
        f"**Positions:**\n"
        f"Spot: {spot_qty:.4f} @ ${sp_price:.2f} → ${spot_val:,.2f}\n"
        f"Perp: {perp_qty:.4f} @ ${pp_price:.2f} → ${perp_val:,.2f}\n\n"
        f"**Risk Metrics:**\n"
        f"Net Δ: ${net_val:,.2f}\n"
        f"Threshold: ±${lim_val:,.2f}\n\n"
        f"**Est. Trading Costs:**\n"
        f"Slippage: {costs['slippage_pct']:.3f}% (${costs['slippage_cost']:.2f})\n"
        f"Fees: ${costs['trading_fees']:.2f}\n"
        f"Total: ${costs['total_cost']:.2f}\n"
    )

    if costs['error']:
        msg += f"⚠️ Cost estimate: {costs['error']}\n"
    
    buttons = []
    if rc.needs_hedge():
        msg += "\n🚨 **THRESHOLD EXCEEDED** 🚨\n"
        buttons.append([InlineKeyboardButton("🔄 Hedge Now", callback_data="hedge_now")])
    else:
        msg += "\n✅ **Within Safe Range**\n"
    
    buttons.extend([
        [InlineKeyboardButton("⚙️ Adjust Threshold", callback_data="adjust_threshold")],
        [InlineKeyboardButton("📊 View Analytics", callback_data="view_analytics")],
        [InlineKeyboardButton("💱 Exchange Prices", callback_data=f"exchange_prices_{asset}")]
    ])

    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))


#--- Multi-Exchange Commands ---
async def exchange_prices(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Compare prices across all exchanges."""
    if not ctx.args:
        return await update.message.reply_text("Usage: /exchange_prices <asset>")
    
    asset = ctx.args[0].upper()
    
    try:
        spot_prices = main_client.get_all_exchange_prices(asset, 'spot')
        perp_prices = main_client.get_all_exchange_prices(asset, 'perpetual')
        
        msg = f"💱 **{asset} Exchange Prices**\n\n"
        
        if spot_prices:
            msg += "**Spot Prices:**\n"
            msg += _format_price_summary(asset, spot_prices)
            
            if len(spot_prices) > 1:
                prices = list(spot_prices.values())
                spread = max(prices) - min(prices)
                spread_pct = (spread / min(prices)) * 100
                msg += f"\n📈 Spread: ${spread:.2f} ({spread_pct:.2f}%)"
        
        if perp_prices:
            msg += "\n\n**Perpetual Prices:**\n"
            msg += _format_price_summary(asset, perp_prices)
            
            if len(perp_prices) > 1:
                prices = list(perp_prices.values())
                spread = max(prices) - min(prices)
                spread_pct = (spread / min(prices)) * 100
                msg += f"\n📈 Spread: ${spread:.2f} ({spread_pct:.2f}%)"
        
        if not spot_prices and not perp_prices:
            msg += "❌ No price data available for this asset."
            
    except ExchangeError as e:
        msg = f"❌ Error fetching prices: {e}"
    
    await update.message.reply_text(msg)

async def market_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Get comprehensive market summary."""
    if not ctx.args:
        return await update.message.reply_text("Usage: /market_summary <asset>")
    
    asset = ctx.args[0].upper()
    
    try:
        summary = main_client.get_market_summary(asset)
        
        msg = f"📊 **{asset} Market Summary**\n\n"
        
        if summary['best_spot']:
            msg += f"**Best Spot:** ${summary['best_spot']['price']:,.2f} ({summary['best_spot']['exchange'].upper()})\n"
        
        if summary['best_perpetual']:
            msg += f"**Best Perpetual:** ${summary['best_perpetual']['price']:,.2f} ({summary['best_perpetual']['exchange'].upper()})\n"
        
        msg += f"\n**All Exchanges:**\n"
        
        if summary['spot_prices']:
            msg += "Spot:\n"
            msg += _format_price_summary(asset, summary['spot_prices'])
        
        if summary['perpetual_prices']:
            msg += "\nPerpetual:\n"
            msg += _format_price_summary(asset, summary['perpetual_prices'])
        
        if 'spread_analysis' in summary and summary['spread_analysis']:
            spread = summary['spread_analysis'].get('spot_spread', {})
            if spread:
                msg += f"\n📈 **Spread Analysis:**\n"
                msg += f"Max-Min: ${spread['spread_abs']:.2f} ({spread['spread_pct']:.2f}%)"
        
        msg += f"\n\n🕐 Updated: {summary['timestamp'][:19]}"
        
    except ExchangeError as e:
        msg = f"❌ Error generating summary: {e}"
    
    await update.message.reply_text(msg)

async def set_exchange(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Set preferred exchange."""
    if not ctx.args:
        return await update.message.reply_text(
            "Usage: /set_exchange <exchange>\n"
            "Options: auto, deribit, okx, bybit"
        )
    
    exchange = ctx.args[0].lower()
    if exchange not in ['auto', 'deribit', 'okx', 'bybit']:
        return await update.message.reply_text(
            "❌ Invalid exchange. Options: auto, deribit, okx, bybit"
        )
    
    portfolio['preferred_exchange'] = exchange
    await update.message.reply_text(f"✅ Preferred exchange set to: {exchange.upper()}")

async def exchange_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show current exchange settings and status."""
    msg = f"🔧 **Exchange Settings**\n\n"
    msg += f"Preferred: {portfolio['preferred_exchange'].upper()}\n"
    msg += f"Current Asset: {portfolio.get('asset', 'None')}\n\n"
    
    msg += "**Exchange Status:**\n"
    
    # Test connectivity to each exchange
    for exchange_name, client in [
        ('Deribit', deribit_client),
        ('OKX', okx_client),
        ('Bybit', bybit_client)
    ]:
        try:
            # Test with BTC
            price = client.get_spot_price('BTC')
            status = f"✅ Online (BTC: ${price:,.0f})"
        except:
            status = "❌ Offline"
        
        msg += f"{exchange_name}: {status}\n"
    
    await update.message.reply_text(msg)

# --- /portfolio_analytics ---
async def portfolio_analytics(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    greeks = analytics.compute_portfolio_greeks()
    pnl = analytics.compute_pnl_attribution()

    text = "📊 Portfolio Greeks:\n"
    text +=f"Δ: {greeks['delta']:.4f}\n"
    text +=f"Γ: {greeks['gamma']:.4f}\n"
    text +=f"Θ: {greeks['theta']:.4f}\n"
    text +=f"Vega: {greeks['vega']:.4f}\n"

    text += "📈 P&L Attribution:\n"
    for leg in pnl["legs"]:
        text += (
            f"{leg['instrument']}: size {leg['size']:.4f}  "
            f"entry {leg['entry']:.2f}  cur {leg['current']:.2f}  "
            f"P&L {leg['pnl']:+.2f}\n"
        )
    text += f"\nTotal P&L: {pnl['total_pnl']:+.2f}  (as of {pnl['timestamp']})"

    await update.message.reply_text(text)

async def risk_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        a, s, p, d, c = ctx.args
        asset, spot, perp = a.upper(), float(s), float(p)
        days, conf = int(d), float(c)
    except Exception:
        return await update.message.reply_text(
            "Usage: /risk_report <asset> <spot> <perp> <days> <conf>"
        )
    
    try:
        series = main_client.get_historical_prices(asset, days)
    except ExchangeError as e:
        return await update.message.reply_text(f"Error fetching history: {e}")
    
    if len(series) < 2:
        return await update.message.reply_text("Not enough data for risk report")

    rc = RiskCalculator(spot, perp, threshold=portfolio['threshold'])
    var   = rc.var(series, conf)
    pnl   = [(x - series[0]) * perp for x in series]
    mdd   = rc.max_drawdown(pnl)
    corr  = rc.correlation_matrix({'spot': series, 'perp': series})

    await update.message.reply_text(
        f"📊 Risk Report for {asset} over {days}d @ {conf*100:.1f}%\n"
        f"• VaR: ${var:.2f}\n"
        f"• Max Drawdown: ${mdd:.2f}\n"
        f"• Corr (spot vs perp): {corr[0,1]:.3f}\n"
    )
    await update.message.reply_text(
        "📈 View distribution: /return_dist\n"
        "⚡ Run stress test: /stress_test"
    )


async def auto_hedge(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text(
            "📊 Available Strategies:\n"
            "• delta_neutral:    `/auto_hedge delta_neutral <asset> <spot> <perp>`\n"
            "• protective_put:   `/auto_hedge protective_put <asset> <spot> <strike> <days> <vol>`\n"
            "• covered_call:     `/auto_hedge covered_call <asset> <spot> <strike> <days> <vol>`\n"
            "• collar:           `/auto_hedge collar <asset> <spot> <put_strike> <call_strike> <days> <vol>`"
        )

    strat = ctx.args[0].lower()
    exchange = portfolio['preferred_exchange']
    client = _get_client_for_exchange(exchange)

    try:
        if strat == "delta_neutral":
            _, asset, s_str, p_str = ctx.args
            asset = asset.upper()
            spot_qty, perp_qty = float(s_str), float(p_str)
            res = delta_neutral(asset, spot_qty, perp_qty, portfolio['threshold'], client)

            analytics.add_perp(asset=asset, size=res["size"], entry_price=client.get_perpetual_price(asset))

        elif strat == "protective_put":
            _, asset, s_str, k_str, d_str, v_str = ctx.args
            asset = asset.upper()
            spot_qty, strike = float(s_str), float(k_str)
            days, vol = int(d_str), float(v_str)

            analytics.add_spot(asset=asset, size=spot_qty, entry_price=client.get_spot_price(asset))
            res = hedge_protective_put(asset, spot_qty, strike, days, vol, client)
            analytics.add_option(
                asset=asset, option_type=OptionType.PUT, strike=strike, days=days,
                volatility=vol, size=res["size"], entry_price=client.get_ticker(res["instrument"])
            )

        elif strat == "covered_call":
            _, asset, s_str, k_str, d_str, v_str = ctx.args
            asset = asset.upper()
            spot_qty, strike = float(s_str), float(k_str)
            days, vol = int(d_str), float(v_str)

            analytics.add_spot(asset=asset, size=spot_qty, entry_price=client.get_spot_price(asset))
            res = covered_call(asset, spot_qty, strike, days, vol, client)
            analytics.add_option(
                asset=asset, option_type=OptionType.CALL, strike=strike, days=days,
                volatility=vol, size=res["size"], entry_price=client.get_ticker(res["instrument"])
            )

        elif strat == "collar":
            _, asset, s_str, p_k_str, c_k_str, d_str, v_str = ctx.args
            asset = asset.upper()
            spot_qty = float(s_str)
            put_strike, call_strike = float(p_k_str), float(c_k_str)
            days, vol = int(d_str), float(v_str)

            analytics.add_spot(asset=asset, size=spot_qty, entry_price=client.get_spot_price(asset))
            res = collar(asset, spot_qty, put_strike, call_strike, days, vol, client)

            analytics.add_option(
                asset=asset, option_type=OptionType.PUT, strike=put_strike, days=days,
                volatility=vol, size=res["put"]["size"], entry_price=client.get_ticker(res["put"]["instrument"])
            )
            analytics.add_option(
                asset=asset, option_type=OptionType.CALL, strike=call_strike, days=days,
                volatility=vol, size=res["call"]["size"], entry_price=client.get_ticker(res["call"]["instrument"])
            )

        else:
            return await update.message.reply_text("❌ Unknown strategy; see `/auto_hedge`")

    except Exception as e:
        return await update.message.reply_text(f"Param error: {e}")

    hedge_log.setdefault(asset, []).append(res)
    costs = _calculate_trade_costs(asset, res['size'], exchange)

    msg = (
        f"✅ {res['strategy']} on {asset}\n"
        f"Size: {res['size']:.4f}\n"
        f"Est. Cost: ${costs['total_cost']:.2f} (slippage: {costs['slippage_pct']:.3f}%)\n"
        f"At: {res['timestamp']}"
    )
    for g in ("delta","gamma","theta","vega"):
        if g in res:
            msg += f"\n• {g.capitalize()}: {res[g]:.4f}"

    await update.message.reply_text(msg)


async def hedge_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        asset, size = ctx.args[0].upper(), float(ctx.args[1])
    except:
        return await update.message.reply_text("Usage: /hedge_now <asset> <size>")

    client = _get_client_for_exchange(portfolio['preferred_exchange'])
    res = delta_neutral(asset, size, portfolio.get('perp', 0.0), portfolio['threshold'])
    analytics.add_perp(asset=asset, size=res["size"], entry_price=client.get_perpetual_price(asset))
    hedge_log.setdefault(asset, []).append(res)

    await update.message.reply_text(
        f"✅ Manual delta_neutral on {asset}\n"
        f"Size: {res['size']:.4f}  Cost: {res['cost']:.2f}\n"
        f"At: {res['timestamp']}"
    )


async def hedge_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        asset = ctx.args[0].upper()
    except:
        return await update.message.reply_text("Usage: /hedge_status <asset>")
    hist = hedge_log.get(asset, [])
    if not hist:
        return await update.message.reply_text("No hedges for that asset")
    last = hist[-1]
    await update.message.reply_text(
        f"{asset} last hedge:\n"
        f"{last['strategy']} size {last['size']:.4f} cost {last.get('cost',0):.2f} at {last['timestamp']}"
    )


async def hedge_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        asset, n = ctx.args[0].upper(), int(ctx.args[1])
    except:
        return await update.message.reply_text("Usage: /hedge_history <asset> <n>")
    hist = hedge_log.get(asset, [])
    if not hist:
        return await update.message.reply_text("No history for that asset")
    lines = [
        f"{i+1}. {h['timestamp']} {h['strategy']} size {h['size']:.4f} cost {h.get('cost',0):.2f}"
        for i, h in enumerate(hist[-n:])
    ]
    await update.message.reply_text("📜 Hedge History:\n" + "\n".join(lines))


async def start_monitoring(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) >= 4:
        await monitor_risk(update, ctx)
    if len(ctx.args) == 5:
        try:
            portfolio['freq'] = int(ctx.args[4])
        except:
            pass
    freq = portfolio['freq']
    ctx.job_queue.run_repeating(_monitor_job, interval=freq, first=0, data=update.effective_chat.id)
    await update.message.reply_text(f"🔄 Monitoring every {freq}s")

async def stop_monitoring(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    for job in ctx.job_queue.jobs():
        job.schedule_removal()
    await update.message.reply_text("🛑 Monitoring stopped")

async def configure(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    for kv in ctx.args:
        if '=' in kv:
            k, v = kv.split('=',1)
            if k=='threshold':
                portfolio['threshold'] = float(v)
            elif k=='freq':
                portfolio['freq'] = int(v)
    await update.message.reply_text(f"⚙️ Config updated: {portfolio}")


async def _monitor_job(ctx: ContextTypes.DEFAULT_TYPE):
    cid = ctx.job.data
    asset, spot_qty, perp_qty, threshold = (
        portfolio.get("asset"), portfolio.get("spot"), portfolio.get("perp"), portfolio.get("threshold")
    )
    if not asset:
        return await ctx.bot.send_message(cid, "⚠️ Run /monitor_risk first")

    client = _get_client_for_exchange(portfolio['preferred_exchange'])
    try:
        sp_price = client.get_spot_price(asset)
        pp_price = client.get_perpetual_price(asset)
    except ExchangeError as e:
        return await ctx.bot.send_message(cid, f"❌ Error fetching prices: {e}")

    spot_val = spot_qty * sp_price
    perp_val = perp_qty * pp_price
    rc = RiskCalculator(spot_val, perp_val, threshold)
    net, lim = rc.net_delta(), rc.threshold_limit()

    msg = (
        f"🔁 Monitoring: {asset}\n"
        f"Spot: {spot_qty:.4f}@{sp_price:.2f}→${spot_val:,.2f}\n"
        f"Perp: {perp_qty:.4f}@{pp_price:.2f}→${perp_val:,.2f}\n"
        f"NetΔ:${net:,.2f}|Thr±${lim:,.2f}\n"
    )

    buttons = []
    if rc.needs_hedge():
        msg += "🚨 Threshold exceeded!"
        buttons.append([InlineKeyboardButton("Hedge Now", callback_data="hedge_now")])
    else:
        msg += "✅ Within safe range."
    buttons.append([InlineKeyboardButton("Adjust Thr%", callback_data="adjust_threshold")])
    buttons.append([InlineKeyboardButton("View Analytics", callback_data="view_analytics")])

    await ctx.bot.send_message(cid, msg, reply_markup=InlineKeyboardMarkup(buttons))


# --- inline button handler ---
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    asset = portfolio.get('asset')
    exchange = portfolio.get('preferred_exchange', 'auto')
    client = _get_client_for_exchange(exchange)

    if q.data == "hedge_now":
        # same as manual /hedge_now but inline
        res = delta_neutral(asset, portfolio['spot'], portfolio['perp'], portfolio['threshold'])
        portfolio['perp'] += res['size']
        hedge_log.setdefault(asset, []).append(res)
        analytics.add_perp(
            asset=asset,
            size=res['size'],
            entry_price=client.get_perpetual_price(asset)
        )
        await q.edit_message_text(
            f"✅ Hedged {res['size']:.4f}\nNew perp: {portfolio['perp']:.4f}"
        )

    elif q.data == "adjust_threshold":
        await q.edit_message_text("Send `/configure threshold=<value>`")

    elif q.data.startswith("view_analytics"):
        await q.edit_message_text(f"Analytics: {portfolio}")

# --- Main ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    job_queue = app.job_queue

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("monitor_risk", monitor_risk))
    app.add_handler(CommandHandler("portfolio_analytics", portfolio_analytics))
    app.add_handler(CommandHandler("risk_report", risk_report))
    app.add_handler(CommandHandler("auto_hedge", auto_hedge))
    app.add_handler(CommandHandler("hedge_now", hedge_now))
    app.add_handler(CommandHandler("hedge_status", hedge_status))
    app.add_handler(CommandHandler("hedge_history", hedge_history))
    app.add_handler(CommandHandler("start_monitoring", start_monitoring))
    app.add_handler(CommandHandler("stop_monitoring", stop_monitoring))
    app.add_handler(CommandHandler("configure", configure))
    app.add_handler(CommandHandler("exchange_prices", exchange_prices))
    app.add_handler(CommandHandler("market_summary", market_summary))
    app.add_handler(CommandHandler("set_exchange", set_exchange))
    app.add_handler(CommandHandler("exchange_status", exchange_status))
    app.add_handler(CommandHandler("return_dist", show_return_dist))
    app.add_handler(CommandHandler("stress_test", stress_test))

    app.add_handler(CallbackQueryHandler(button_handler))

    print("Bot running…")
    app.run_polling()
