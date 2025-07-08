import logging, time
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

logger = logging.getLogger(__name__)
client = DeribitClient()

# In‑memory state
portfolio = {
    'asset': None,
    'spot': None,
    'perp': None,
    'threshold': 10.0,
    'freq': 60
}
hedge_log: dict[str, list[dict]] = {}

# --- Helper to format timestamps ---
def _now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# --- /start ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! Commands:\n"
        "/start — show this message\n"
        "/monitor_risk <asset> <spot> <perp> <thr%>\n"
        "/auto_hedge — choose and run a hedge strategy\n"
        "/risk_report <asset> <spot> <perp> <days> <conf>\n"
        "/start_monitoring — monitors last trade every 60secs\n"
        "/stop_monitoring\n"
        "/configure threshold=<%> freq=<seconds>\n"
        "/hedge_now <asset> <size>\n"
        "/hedge_status <asset>\n"
        "/hedge_history <asset> <n>\n"
        "/help — show start message again\n"
    )


# --- /monitor_risk ---
# --- /monitor_risk ---
async def monitor_risk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    if len(args) != 4:
        return await update.message.reply_text(
            "Usage: /monitor_risk <asset> <spot_qty> <perp_qty> <thr%>\n"
            "Example: /monitor_risk BTC 1.0 -0.5 10"
        )

    asset = args[0].upper()
    # normalize dashes
    raw_spot, raw_perp, raw_thr = args[1], args[2], args[3]
    for ch in ("\u2013", "\u2014"):  # en‑dash, em‑dash
        raw_spot = raw_spot.replace(ch, "-")
        raw_perp = raw_perp.replace(ch, "-")
        raw_thr  = raw_thr.replace(ch,  "-")

    try:
        spot_qty   = float(raw_spot)
        perp_qty   = float(raw_perp)
        threshold  = float(raw_thr)
    except ValueError:
        return await update.message.reply_text(
            "❌ Spot, perp and threshold must be *plain* numbers (use `-0.5` not `–0.5`).\n"
            "Usage: `/monitor_risk BTC 1.0 -0.5 10`"
        )

    # store for monitoring jobs and buttons
    portfolio.update({
        'asset': asset,
        'spot': spot_qty,
        'perp': perp_qty,
        'threshold': threshold
    })

    # fetch live prices
    try:
        spot_price = client.get_spot_price(asset)
        perp_price = client.get_perpetual_price(asset)
    except DeribitError as e:
        return await update.message.reply_text(f"Error fetching price data: {e}")

    # convert to dollar exposures
    spot_val = spot_qty * spot_price
    perp_val = perp_qty * perp_price

    rc = RiskCalculator(spot_val, perp_val, threshold)
    net_val = rc.net_delta()
    limit_val = rc.threshold_limit()

    # build message
    msg = (
        f"Asset: {asset}\n"
        f"Spot: {spot_qty:.4f} @ {spot_price:.2f} → ${spot_val:,.2f}\n"
        f"Perp: {perp_qty:.4f} @ {perp_price:.2f} → ${perp_val:,.2f}\n"
        f"Net Δ (value): ${net_val:,.2f}\n"
        f"Threshold: ±${limit_val:,.2f}\n"
    )

    # inline buttons
    buttons = []
    if rc.needs_hedge():
        msg += "⚠️ Threshold exceeded.\n"
        buttons.append([InlineKeyboardButton("Hedge Now", callback_data="hedge_now")])
    else:
        msg += "✅ Within limits.\n"
    buttons.append([InlineKeyboardButton("Adjust Thr%", callback_data="adjust_threshold")])
    buttons.append([InlineKeyboardButton("View Analytics", callback_data="view_analytics")])

    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))


# --- /risk_report ---
async def risk_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        a,s,p,d,c = ctx.args
        asset, spot, perp = a.upper(), float(s), float(p)
        days, conf = int(d), float(c)
    except:
        return await update.message.reply_text(
            "Usage: /risk_report <asset> <spot> <perp> <days> <conf>"
        )

    end_ts = int(time.time()*1000)
    start_ts = int((datetime.now()-timedelta(days=days)).timestamp()*1000)
    try:
        series = client.get_historical_prices(f"{asset}-PERPETUAL", start_ts, end_ts)
    except DeribitError as e:
        return await update.message.reply_text(f"Error fetching history: {e}")

    if len(series)<2:
        return await update.message.reply_text("Not enough data for risk_report")

    rc = RiskCalculator(spot, perp)
    var = rc.var(series, conf)
    pnl = [(x-series[0])*perp for x in series]
    mdd = rc.max_drawdown(pnl)
    corr = rc.correlation_matrix({'spot':series,'perp':series})

    msg = (
        f"📊 Risk Report for {asset} over {days}d @ {conf*100:.1f}%\n"
        f"• VaR: {var:.2f}\n"
        f"• Max Drawdown: {mdd:.2f}\n"
        f"• Corr (spot vs perp): {corr[0,1]:.3f}\n"
    )
    await update.message.reply_text(msg)

# --- /auto_hedge ---
async def auto_hedge(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # If no strategy specified, show the list:
    if not ctx.args:
        return await update.message.reply_text(
            "📊 Available Hedging Strategies:\n\n"
            "• delta_neutral\n"
            "    /auto_hedge delta_neutral <asset> <spot_qty> <perp_qty>\n\n"
            "• protective_put\n"
            "    /auto_hedge protective_put <asset> <spot_qty> <strike> <days> <vol>\n\n"
            "• covered_call\n"
            "    /auto_hedge covered_call <asset> <spot_qty> <strike> <days> <vol>\n\n"
            "• collar\n"
            "    /auto_hedge collar <asset> <spot_qty> <put_strike> <call_strike> <days> <vol>\n"
        )

    strat = ctx.args[0].lower()
    if not ctx.args:
        return await update.message.reply_text("Usage: see /start for strategy parameters")
    strat = ctx.args[0].lower()
    try:
        if strat=="delta_neutral":
            _,asset,spot,perp = ctx.args
            res = delta_neutral(asset.upper(), float(spot), float(perp), portfolio['threshold'])
        elif strat=="protective_put":
            _,asset,spot,strike,days,vol = ctx.args
            res = hedge_protective_put(asset.upper(), float(spot), float(strike), int(days), float(vol))
        elif strat=="covered_call":
            _,asset,spot,strike,days,vol = ctx.args
            res = covered_call(asset.upper(), float(spot), float(strike), int(days), float(vol))
        elif strat=="collar":
            _,asset,spot,pstr,cstr,days,vol = ctx.args
            res = collar(asset.upper(), float(spot), float(pstr), float(cstr), int(days), float(vol))
        else:
            return await update.message.reply_text("Unknown strategy")
    except Exception as e:
        return await update.message.reply_text(f"Param error: {e}")

    hedge_log.setdefault(res.get("strategy"), []).append(res)
    msg = (
        f"✅ {res['strategy']} on {asset.upper()}\n"
        f"Size: {res['size']:.4f}  Cost: {res.get('cost',0):.2f}\n"
        f"At: {res['timestamp']}"
    )

    # If the strategy returned any Greeks, append them
    greek_keys = ['delta', 'gamma', 'theta', 'vega']
    lines = []
    for g in greek_keys:
        if g in res:
            lines.append(f"{g.capitalize()}: {res[g]:.4f}")
    if lines:
        msg += "\n\n📉 Greeks:\n" + "\n".join(lines)

    await update.message.reply_text(msg)

# --- /hedge_now (manual perp hedge) ---
async def hedge_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        asset, size = ctx.args[0].upper(), float(ctx.args[1])
    except:
        return await update.message.reply_text("Usage: /hedge_now <asset> <size>")
    res = delta_neutral(asset, size, portfolio.get('perp',0.0), portfolio['threshold'])
    hedge_log.setdefault(asset, []).append(res)
    await update.message.reply_text(
        f"✅ Manual delta_neutral on {asset}\n"
        f"Size: {res['size']}  Cost: {res['cost']:.2f}\n"
        f"At: {res['timestamp']}"
    )

# --- /hedge_status ---
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
        f"{last['strategy']} size {last['size']} cost {last.get('cost',0):.2f} at {last['timestamp']}"
    )

# --- /hedge_history ---
async def hedge_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        asset, n = ctx.args[0].upper(), int(ctx.args[1])
    except:
        return await update.message.reply_text("Usage: /hedge_history <asset> <n>")
    hist = hedge_log.get(asset, [])
    if not hist:
        return await update.message.reply_text("No history for that asset")
    lines = [f"{i+1}. {h['timestamp']} {h['strategy']} size {h['size']} cost {h.get('cost',0):.2f}"
             for i,h in enumerate(hist[-n:])]
    await update.message.reply_text("📜 Hedge History:\n" + "\n".join(lines))

# --- /start_monitoring & /stop_monitoring & /configure ---

async def start_monitoring(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Optional init if args >=4
    if len(ctx.args) >= 4:
        await monitor_risk(update, ctx)
    if len(ctx.args) == 5:
        try: portfolio['freq'] = int(ctx.args[4])
        except: pass
    freq = portfolio['freq']
    ctx.job_queue.run_repeating(_monitor_job, interval=freq, first=0, data=update.effective_chat.id)
    await update.message.reply_text(f"🔄 Monitoring every {freq}s")

async def stop_monitoring(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    for job in ctx.job_queue.jobs(): job.schedule_removal()
    await update.message.reply_text("🛑 Monitoring stopped")

async def configure(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    for kv in ctx.args:
        if '=' in kv:
            k,v = kv.split('=',1)
            if k=='threshold': portfolio['threshold']=float(v)
            if k=='freq': portfolio['freq']=int(v)
    await update.message.reply_text(f"⚙️ Config updated: {portfolio}")

async def _monitor_job(ctx: ContextTypes.DEFAULT_TYPE):
    cid = ctx.job.data
    a, sp, pp, thr = portfolio['asset'], portfolio['spot'], portfolio['perp'], portfolio['threshold']
    if not a:
        return await ctx.bot.send_message(cid, "⚠️ Run /monitor_risk first")

    # Fetch latest prices
    try:
        spot_price = client.get_spot_price(a)
        perp_price = client.get_perpetual_price(a)
    except DeribitError as e:
        return await ctx.bot.send_message(cid, f"⚠️ Error fetching prices: {e}")

    # Dollar exposure
    spot_val = sp * spot_price
    perp_val = pp * perp_price
    rc = RiskCalculator(spot_val, perp_val, thr)
    net = rc.net_delta()
    limit = rc.threshold_limit()

    msg = (
        f"🔁 Monitoring: {a}\n"
        f"Spot: {sp:.4f} @ {spot_price:.2f} → ${spot_val:,.2f}\n"
        f"Perp: {pp:.4f} @ {perp_price:.2f} → ${perp_val:,.2f}\n"
        f"Net Δ: ${net:,.2f} | Threshold ±${limit:,.2f}\n"
    )

    buttons = []
    if rc.needs_hedge():
        msg += "🚨 Delta threshold exceeded!"
        buttons.append([InlineKeyboardButton("Hedge Now", callback_data="hedge_now")])
    else:
        msg += "✅ Within safe range."
    buttons.append([InlineKeyboardButton("Adjust Thr%", callback_data="adjust_threshold")])
    buttons.append([InlineKeyboardButton("View Analytics", callback_data="view_analytics")])

    await ctx.bot.send_message(cid, msg, reply_markup=InlineKeyboardMarkup(buttons))

# --- CallbackQueryHandler for inline buttons ---
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data=="hedge_now":
        res = delta_neutral(portfolio['asset'], portfolio['spot'], portfolio['perp'], portfolio['threshold'])
        portfolio['perp'] += res['size']  # update perp in memory
        hedge_log.setdefault(portfolio['asset'], []).append(res)
        
        await q.edit_message_text(
            f"✅ Hedged {res['size']:.4f}\n"
            f"New perp: {portfolio['perp']:.4f}"
        )
    elif q.data=="adjust_threshold":
        await q.edit_message_text("Send /configure threshold=<value>")
    else:  # view_analytics
        await q.edit_message_text(f"Analytics: {portfolio}")

# --- Main ---
if __name__=="__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    # ensure job_queue created
    job_queue = app.job_queue

    # register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("monitor_risk", monitor_risk))
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
