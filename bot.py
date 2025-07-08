import logging
import time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)
from config import TELEGRAM_BOT_TOKEN
from deribit_client import DeribitClient, DeribitError
from risk import RiskCalculator
from strategies import (
    hedge_protective_put,
    covered_call,
    collar,
    delta_neutral
)
from portfolio_analytics import PortfolioAnalytics
from greeks import OptionType

# single, shared clients & state
logger = logging.getLogger(__name__)
client = DeribitClient()
analytics = PortfolioAnalytics()

portfolio = {
    'asset': None,
    'spot': None,
    'perp': None,
    'threshold': 10.0,
    'freq': 60
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

# --- /start & /help ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! Commands:\n"
        "/start — show this message\n"
        "/monitor_risk <asset> <spot> <perp> <thr%>\n"
        "/auto_hedge — choose & run a hedge strategy\n"
        "/risk_report <asset> <spot> <perp> <days> <conf>\n"
        "/start_monitoring — auto monitor every 60s\n"
        "/stop_monitoring\n"
        "/configure threshold=<%> freq=<seconds>\n"
        "/hedge_now <asset> <size>\n"
        "/hedge_status <asset>\n"
        "/hedge_history <asset> <n>\n"
        "/portfolio_greeks — show portfolio Greeks\n"
        "/help — same as /start\n"
    )

# --- /monitor_risk ---
async def monitor_risk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    if len(args) != 4:
        return await update.message.reply_text(
            "Usage: /monitor_risk <asset> <spot_qty> <perp_qty> <thr%>\n"
            "Example: /monitor_risk BTC 1.0 -0.5 10"
        )
    asset = args[0].upper()
    # normalize fancy dashes
    raw = [arg.replace("–","-").replace("—","-") for arg in args[1:]]
    try:
        spot_qty, perp_qty, threshold = _cast_args(raw, [float, float, float])
    except ValueError:
        return await update.message.reply_text(
            "❌ Spot, perp and threshold must be plain numbers (e.g. `-0.5`)."
        )

    portfolio.update({'asset': asset, 'spot': spot_qty, 'perp': perp_qty, 'threshold': threshold})

    try:
        sp_price = client.get_spot_price(asset)
        pp_price = client.get_perpetual_price(asset)
    except DeribitError as e:
        return await update.message.reply_text(f"Error fetching price data: {e}")

    spot_val = spot_qty * sp_price
    perp_val = perp_qty * pp_price
    rc = RiskCalculator(spot_val, perp_val, threshold)
    net_val = rc.net_delta()
    lim_val = rc.threshold_limit()

    msg = (
        f"Asset: {asset}\n"
        f"Spot: {spot_qty:.4f} @ {sp_price:.2f} → ${spot_val:,.2f}\n"
        f"Perp: {perp_qty:.4f} @ {pp_price:.2f} → ${perp_val:,.2f}\n"
        f"Net Δ (value): ${net_val:,.2f}\n"
        f"Threshold: ±${lim_val:,.2f}\n"
    )
    buttons = []
    if rc.needs_hedge():
        msg += "⚠️ Threshold exceeded.\n"
        buttons.append([InlineKeyboardButton("Hedge Now", callback_data="hedge_now")])
    else:
        msg += "✅ Within limits.\n"
    buttons.append([InlineKeyboardButton("Adjust Thr%", callback_data="adjust_threshold")])
    buttons.append([InlineKeyboardButton("View Analytics", callback_data="view_analytics")])

    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))

# --- /portfolio_greeks ---
async def portfolio_greeks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    totals = analytics.compute_portfolio_greeks()
    await update.message.reply_text(
        "📊 Portfolio Greeks:\n"
        f"Δ: {totals['delta']:.4f}\n"
        f"Γ: {totals['gamma']:.4f}\n"
        f"Θ: {totals['theta']:.4f}\n"
        f"Vega: {totals['vega']:.4f}"
    )

# --- /risk_report ---
async def risk_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        a, s, p, d, c = ctx.args
        asset, spot, perp = a.upper(), float(s), float(p)
        days, conf = int(d), float(c)
    except Exception:
        return await update.message.reply_text(
            "Usage: /risk_report <asset> <spot> <perp> <days> <conf>"
        )

    end_ts = int(time.time() * 1000)
    start_ts = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    try:
        series = client.get_historical_prices(f"{asset}-PERPETUAL", start_ts, end_ts)
    except DeribitError as e:
        return await update.message.reply_text(f"Error fetching history: {e}")
    if len(series) < 2:
        return await update.message.reply_text("Not enough data for risk_report")

    rc = RiskCalculator(spot, perp)
    var   = rc.var(series, conf)
    pnl   = [(x - series[0]) * perp for x in series]
    mdd   = rc.max_drawdown(pnl)
    corr  = rc.correlation_matrix({'spot': series, 'perp': series})

    await update.message.reply_text(
        f"📊 Risk Report for {asset} over {days}d @ {conf*100:.1f}%\n"
        f"• VaR: {var:.2f}\n"
        f"• Max Drawdown: {mdd:.2f}\n"
        f"• Corr (spot vs perp): {corr[0,1]:.3f}"
    )

# --- /auto_hedge ---
async def auto_hedge(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text(
            "📊 Available Strategies:\n"
            "• delta_neutral: `/auto_hedge delta_neutral <asset> <spot> <perp>`\n"
            "• protective_put:`/auto_hedge protective_put <asset> <spot> <strike> <days> <vol>`\n"
            "• covered_call: `/auto_hedge covered_call <asset> <spot> <strike> <days> <vol>`\n"
            "• collar:       `/auto_hedge collar <asset> <spot> <put_strike> <call_strike> <days> <vol>`"
        )

    strat = ctx.args[0].lower()
    try:
        if strat == "delta_neutral":
            _, asset, *rest = ctx.args
            spot, perp = _cast_args(rest, [float, float])
            res = delta_neutral(asset.upper(), spot, perp, portfolio['threshold'])
            analytics.add_perp(
                asset=asset.upper(),
                size=res["size"],
                entry_price=client.get_perpetual_price(asset.upper()),
            )

        elif strat == "protective_put":
            _, asset, *rest = ctx.args
            spot, strike, days, vol = _cast_args(rest, [float, float, int, float])
            res = hedge_protective_put(asset.upper(), spot, strike, days, vol)
            analytics.add_option(
                asset=asset.upper(),
                option_type=OptionType.PUT,
                strike=strike,
                days=days,
                volatility=vol,
                size=res["size"],
                entry_price=client.get_ticker(res["instrument"])
            )

        elif strat == "covered_call":
            _, asset, *rest = ctx.args
            spot, strike, days, vol = _cast_args(rest, [float, float, int, float])
            res = covered_call(asset.upper(), spot, strike, days, vol)
            analytics.add_option(
                asset=asset.upper(),
                option_type=OptionType.CALL,
                strike=strike,
                days=days,
                volatility=vol,
                size=-res["size"],  # negative because sold
                entry_price=client.get_ticker(res["instrument"])
            )

        elif strat == "collar":
            _, asset, *rest = ctx.args
            spot, p_str, c_str, days, vol = _cast_args(rest, [float, float, float, int, float])
            res = collar(asset.upper(), spot, p_str, c_str, days, vol)
            # collar has two legs
            analytics.add_option(
                asset=asset.upper(),
                option_type=OptionType.PUT,
                strike=p_str,
                days=days,
                volatility=vol,
                size=res["put"]["size"],
                entry_price=client.get_ticker(res["put"]["instrument"])
            )
            analytics.add_option(
                asset=asset.upper(),
                option_type=OptionType.CALL,
                strike=c_str,
                days=days,
                volatility=vol,
                size=-res["call"]["size"],
                entry_price=client.get_ticker(res["call"]["instrument"])
            )

        else:
            return await update.message.reply_text("❌ Unknown strategy")
    except Exception as e:
        return await update.message.reply_text(f"Param error: {e}")

    # log & report
    hedge_log.setdefault(asset.upper(), []).append(res)

    msg = (
        f"✅ {res['strategy']} on {asset.upper()}\n"
        f"Size: {res['size']:.4f}  Cost: {res.get('cost',0):.2f}\n"
        f"At: {res['timestamp']}"
    )
    # append Greeks if present
    for g in ("delta","gamma","theta","vega"):
        if g in res:
            msg += f"\n• {g.capitalize()}: {res[g]:.4f}"

    await update.message.reply_text(msg)

# --- /hedge_now (manual perp hedge) ---
async def hedge_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        asset, size = ctx.args[0].upper(), float(ctx.args[1])
    except:
        return await update.message.reply_text("Usage: /hedge_now <asset> <size>")

    res = delta_neutral(asset, size, portfolio.get('perp',0.0), portfolio['threshold'])
    analytics.add_perp(
        asset=asset,
        size=res["size"],
        entry_price=client.get_perpetual_price(asset)
    )
    hedge_log.setdefault(asset, []).append(res)

    await update.message.reply_text(
        f"✅ Manual delta_neutral on {asset}\n"
        f"Size: {res['size']:.4f}  Cost: {res['cost']:.2f}\n"
        f"At: {res['timestamp']}"
    )

# --- hedge_status & hedge_history ---
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

# --- /start_monitoring, /stop_monitoring, /configure & job ---
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
    a, sp, pp, thr = portfolio.values()
    if not a:
        return await ctx.bot.send_message(cid, "⚠️ Run /monitor_risk first")
    try:
        sp_price = client.get_spot_price(a)
        pp_price = client.get_perpetual_price(a)
    except DeribitError as e:
        return await ctx.bot.send_message(cid, f"Error fetching prices: {e}")

    spot_val = sp * sp_price
    perp_val = pp * pp_price
    rc = RiskCalculator(spot_val, perp_val, thr)
    net, lim = rc.net_delta(), rc.threshold_limit()

    msg = (
        f"🔁 Monitoring: {a}\n"
        f"Spot: {sp:.4f}@{sp_price:.2f}→${spot_val:,.2f}\n"
        f"Perp: {pp:.4f}@{pp_price:.2f}→${perp_val:,.2f}\n"
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
    q = update.callback_query; await q.answer()
    if q.data == "hedge_now":
        # same as manual /hedge_now but inline
        res = delta_neutral(portfolio['asset'], portfolio['spot'], portfolio['perp'], portfolio['threshold'])
        portfolio['perp'] += res['size']
        hedge_log.setdefault(portfolio['asset'], []).append(res)
        analytics.add_perp(
            asset=portfolio['asset'],
            size=res['size'],
            entry_price=client.get_perpetual_price(portfolio['asset'])
        )
        await q.edit_message_text(
            f"✅ Hedged {res['size']:.4f}\nNew perp: {portfolio['perp']:.4f}"
        )
    elif q.data == "adjust_threshold":
        await q.edit_message_text("Send `/configure threshold=<value>`")
    else:  # view_analytics
        await q.edit_message_text(f"Analytics: {portfolio}")

# --- Main ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    job_queue = app.job_queue

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("monitor_risk", monitor_risk))
    app.add_handler(CommandHandler("portfolio_greeks", portfolio_greeks))
    app.add_handler(CommandHandler("risk_report", risk_report))
    app.add_handler(CommandHandler("auto_hedge", auto_hedge))
    app.add_handler(CommandHandler("hedge_now", hedge_now))
    app.add_handler(CommandHandler("hedge_status", hedge_status))
    app.add_handler(CommandHandler("hedge_history", hedge_history))
    app.add_handler(CommandHandler("start_monitoring", start_monitoring))
    app.add_handler(CommandHandler("stop_monitoring", stop_monitoring))
    app.add_handler(CommandHandler("configure", configure))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Bot running…")
    app.run_polling()
