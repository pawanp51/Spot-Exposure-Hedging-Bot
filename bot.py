import logging
import time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from config import TELEGRAM_BOT_TOKEN
from deribit_client import DeribitClient, DeribitError
from risk import RiskCalculator
from options_hedger import OptionsHedger
from greeks import GreeksCalculator, OptionType

logger = logging.getLogger(__name__)

# initialize Deribit client
client = DeribitClient()

# in-memory portfolio with defaults
portfolio = {
    'asset': None,
    'spot': None,
    'perp': None,
    'threshold': 10.0,  # default threshold percent
    'freq': 60,         # default monitoring frequency in seconds
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! Commands:\n"
        "/monitor_risk <asset> <spot> <perp> <thr%>\n"
        "/hedge_protective_put <asset> <spot> <strike> <days> <vol>\n"
        "/risk_report <asset> <spot> <perp> <days> <conf>\n"
        "/start_monitoring [freq]\n"
        "/stop_monitoring\n"
        "/configure threshold=<%> freq=<seconds>"
    )

async def monitor_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /monitor_risk command to fetch prices, compute risk, and alert if needed."""
    try:
        asset, spot_str, perp_str, thresh_str = context.args
        asset = asset.upper()
        spot = float(spot_str)
        perp = float(perp_str)
        threshold = float(thresh_str)

        portfolio.update({'asset': asset, 'spot': spot, 'perp': perp, 'threshold': threshold})

        # fetch live prices
        spot_price = client.get_spot_price(asset)
        perp_price = client.get_perpetual_price(asset)

        # calculate risk
        rc = RiskCalculator(spot, perp, threshold)
        net_d = rc.net_delta()
        limit = rc.threshold_limit()

        msg = (
            f"Asset: {asset}\n"
            f"Spot: {spot} @ {spot_price:.2f}\n"
            f"Perpetual: {perp} @ {perp_price:.2f}\n"
            f"Net Delta: {net_d:.4f}\n"
            f"Threshold: ±{limit:.4f}\n"
        )
        if rc.needs_hedge():
            msg += "⚠️ Threshold exceeded."
            keyboard = [[InlineKeyboardButton("Hedge Now", callback_data="hedge_now")]]
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            msg += "✅ Within limits."
            await update.message.reply_text(msg)

    except (ValueError, IndexError):
        await update.message.reply_text("Usage: /monitor_risk BTC 1.0 -0.5 10")
    except DeribitError as e:
        logger.error(f"Deribit error: {e}")
        await update.message.reply_text(f"Error fetching price data: {e}")
    except Exception as e:
        logger.exception("Unexpected error in monitor_risk")
        await update.message.reply_text(f"Unexpected error: {e}")

async def hedge_protective_put(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /hedge_protective_put for buying protective puts."""
    try:
        asset, spot_str, strike_str, days_str, vol_str = context.args
        asset = asset.upper()
        spot = float(spot_str)
        strike = float(strike_str)
        days = int(days_str)
        vol = float(vol_str)

        # fetch current spot proxy price
        S = client.get_spot_price(asset)
        T = days / 365

        # find valid Deribit option instrument
        inst_name = client.find_option_instrument(asset, strike, days, option_type="put")

        # calculate hedge quantity based on put delta
        hedger = OptionsHedger(S, strike, T, 0.0, vol, spot)
        qty = hedger.hedge_qty()

        # compute Greeks
        gamma = GreeksCalculator.gamma(S, strike, T, 0.0, vol)
        theta = GreeksCalculator.theta(S, strike, T, 0.0, vol, OptionType.PUT)
        vega = GreeksCalculator.vega(S, strike, T, 0.0, vol)

        # fetch live option price
        price = client.get_ticker(inst_name)
        if price is None or price == 0.0:
            price = float('nan')  # fallback if zero

        await update.message.reply_text(
            f"Protective Put Hedging:\n"
            f"Instrument: {inst_name}\n"
            f"Buy Quantity: {qty}\n"
            f"Price per Contract: {price:.2f}\n"
            f"Put Delta per Contract: {hedger.put_delta():.4f}\n"
            f"Total Delta Hedged: {qty * hedger.put_delta():.4f}\n"
            f"Gamma: {gamma:.4f}\n"
            f"Theta: {theta:.6f}\n"
            f"Vega: {vega:.4f}"
        )
    except DeribitError as e:
        await update.message.reply_text(f"Instrument lookup error: {e}")
    except Exception:
        await update.message.reply_text("Usage: /hedge_protective_put ETH 2 3000 30 0.5")

async def risk_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates VaR, max drawdown, and correlation matrix over historical period."""
    try:
        asset, spot_str, perp_str, days_str, conf_str = context.args
        asset = asset.upper()
        spot = float(spot_str)
        perp = float(perp_str)
        days = int(days_str)
        conf = float(conf_str)

        end_ts = int(time.time() * 1000)
        start_ts = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)

        # Fetch historical perpetual series
        perp_series = client.get_historical_prices(
            f"{asset}-PERPETUAL", start_ts, end_ts
        )
        if len(perp_series) < 2:
            raise ValueError("Not enough perp data")

        # Use perpetual as spot proxy
        spot_series = perp_series.copy()

        rc = RiskCalculator(spot, perp)

        # VaR & max drawdown
        var = rc.var(perp_series, conf)
        pnl = [(p - perp_series[0]) * perp for p in perp_series]
        mdd = rc.max_drawdown(pnl)

        # Correlation matrix
        price_dict = {
            f"{asset}_spot": spot_series,
            f"{asset}_perp": perp_series
        }
        corr_mat = rc.correlation_matrix(price_dict)

        msg = (
            f"📊 Risk Report for {asset} over {days}d @ {conf*100:.1f}%\n\n"
            f"• VaR: {var:.2f}\n"
            f"• Max Drawdown: {mdd:.2f}\n\n"
            f"• Correlation Matrix (spot vs perp):\n"
            f"    [ {corr_mat[0,0]:.3f}   {corr_mat[0,1]:.3f} ]\n"
            f"    [ {corr_mat[1,0]:.3f}   {corr_mat[1,1]:.3f} ]"
        )
        await update.message.reply_text(msg)

    except Exception:
        await update.message.reply_text("Usage: /risk_report BTC 2 -1 7 0.95")

async def start_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Begin periodic risk checks."""
    freq = portfolio.get('freq', 60)
    # optional override: /start_monitoring 30
    if context.args:
        try:
            freq = int(context.args[0])
            portfolio['freq'] = freq
        except:
            pass
    context.job_queue.run_repeating(
        _monitor_job, interval=freq, first=0,
        data=update.effective_chat.id
    )
    await update.message.reply_text(f"✅ Auto‑monitoring every {freq}s started.")

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel all monitoring jobs."""
    for job in context.job_queue.jobs():
        job.schedule_removal()
    await update.message.reply_text("🛑 Auto‑monitoring stopped.")

async def configure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Configure threshold or frequency."""
    for kv in context.args:
        if '=' in kv:
            key, val = kv.split('=', 1)
            if key == 'threshold':
                try:
                    portfolio['threshold'] = float(val)
                except:
                    pass
            if key == 'freq':
                try:
                    portfolio['freq'] = int(val)
                except:
                    pass
    await update.message.reply_text(f"⚙️ Updated settings: {portfolio}")

async def _monitor_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    asset = portfolio.get('asset')
    spot = portfolio.get('spot')
    perp = portfolio.get('perp')
    thr = portfolio.get('threshold')

    if not asset or spot is None or perp is None:
        await context.bot.send_message(
            chat_id,
            "⚠️ Monitoring skipped: Missing asset/spot/perp.\n"
            "Please run /monitor_risk first to initialize the portfolio."
        )
        return

    try:
        # Proxy spot history with perp series
        spot_series = client.get_historical_prices(
            f"{asset}-PERPETUAL",
            int((datetime.utcnow() - timedelta(days=7)).timestamp() * 1000),
            int(time.time() * 1000)
        )
        perp_series = spot_series

        rc = RiskCalculator(spot, perp, thr)
        β = rc.beta(spot_series, perp_series)
        hedge_ratio = rc.perp_hedge_ratio(spot_series, perp_series)

        if rc.needs_hedge():
            msg = (
                f"🚨 Risk Alert: {asset}\n"
                f"• Net Δ: {rc.net_delta():.4f} (Thr ±{rc.threshold_limit():.4f})\n"
                f"• β: {β:.3f}\n"
                f"*Recommended perp hedge*: {hedge_ratio:.4f}\n"
            )
            keyboard = [
                [InlineKeyboardButton("Hedge Now", callback_data="hedge_now")],
                [InlineKeyboardButton("Adjust Thr%", callback_data="adjust_threshold")],
                [InlineKeyboardButton("View Analytics", callback_data="view_analytics")]
            ]
            await context.bot.send_message(chat_id, msg, reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        await context.bot.send_message(chat_id, f"⚠️ Error during monitoring: {e}")
        logger.exception("Monitoring job failed")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "adjust_threshold":
        await query.edit_message_text("Send new threshold % with /configure threshold=<value>")
    elif query.data == "view_analytics":
        await query.edit_message_text(f"Analytics: {portfolio}")
    else:  # "hedge_now"
        rc = RiskCalculator(
            portfolio['spot'], portfolio['perp'], portfolio['threshold']
        )
        amt = rc.hedge_amount()
        portfolio['perp'] -= amt
        await query.edit_message_text(f"✅ Hedged {amt:.4f}. New perp: {portfolio['perp']:.4f}")

async def _monitor_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    asset = portfolio.get('asset')
    spot = portfolio.get('spot')
    perp = portfolio.get('perp')
    thr = portfolio.get('threshold')

    if not asset or spot is None or perp is None:
        await context.bot.send_message(
            chat_id,
            "⚠️ Monitoring skipped: Missing asset/spot/perp.\n"
            "Please run /monitor_risk first to initialize the portfolio."
        )
        return

    try:
        # Proxy spot history with perp series
        spot_series = client.get_historical_prices(
            f"{asset}-PERPETUAL",
            int((datetime.utcnow() - timedelta(days=7)).timestamp() * 1000),
            int(time.time() * 1000)
        )
        perp_series = spot_series

        rc = RiskCalculator(spot, perp, thr)
        β = rc.beta(spot_series, perp_series)
        hedge_ratio = rc.perp_hedge_ratio(spot_series, perp_series)

        if rc.needs_hedge():
            msg = (
                f"🚨 Risk Alert: {asset}\n"
                f"• Net Δ: {rc.net_delta():.4f} (Thr ±{rc.threshold_limit():.4f})\n"
                f"• β: {β:.3f}\n"
                f"*Recommended perp hedge*: {hedge_ratio:.4f}\n"
            )
            keyboard = [
                [InlineKeyboardButton("Hedge Now", callback_data="hedge_now")],
                [InlineKeyboardButton("Adjust Thr%", callback_data="adjust_threshold")],
                [InlineKeyboardButton("View Analytics", callback_data="view_analytics")]
            ]
            await context.bot.send_message(chat_id, msg, reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        await context.bot.send_message(chat_id, f"⚠️ Error during monitoring: {e}")
        logger.exception("Monitoring job failed")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    job_queue = app.job_queue  # This ensures JobQueue is initialized

    # register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("monitor_risk", monitor_risk))
    app.add_handler(CommandHandler("hedge_protective_put", hedge_protective_put))
    app.add_handler(CommandHandler("risk_report", risk_report))
    app.add_handler(CommandHandler("start_monitoring", start_monitoring))
    app.add_handler(CommandHandler("stop_monitoring", stop_monitoring))
    app.add_handler(CommandHandler("configure", configure))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Bot running...")
    app.run_polling()
