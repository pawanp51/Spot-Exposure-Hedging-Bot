import logging
import time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from config import TELEGRAM_BOT_TOKEN
from deribit_client import DeribitClient, DeribitError
from risk import RiskCalculator
from options_hedger import OptionsHedger

logger = logging.getLogger(__name__)

# initialize Deribit client
client = DeribitClient()
# in-memory portfolio
portfolio = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! Commands:\n"
        "/monitor_risk <asset> <spot> <perp> <thr%>\n"
        "/hedge_protective_put <asset> <spot> <strike> <days> <vol>\n"
        "/risk_report <asset> <spot> <perp> <days> <conf>"
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
    try:
        asset, spot, strike, days, vol = context.args
        asset = asset.upper()
        spot = float(spot)
        strike = float(strike)
        days = int(days)
        vol = float(vol)

        # fetch current spot proxy price
        S = client.get_spot_price(asset)
        T = days / 365

        # find valid Deribit option instrument
        inst_name = client.find_option_instrument(asset, strike, days, option_type="put")

        # calculate hedge quantity based on put delta
        hedger = OptionsHedger(S, strike, T, 0.0, vol, spot)
        qty = hedger.hedge_qty()

        # fetch live option price
        price = client.get_ticker(inst_name)

        await update.message.reply_text(
            f"Protective Put Hedging:\n"
            f"Instrument: {inst_name}\n"
            f"Buy Quantity: {qty}\n"
            f"Price per Contract: {price:.2f}\n"
            f"Put Delta per Contract: {hedger.put_delta():.4f}\n"
            f"Total Delta Hedged: {qty * hedger.put_delta():.4f}\n"
        )
    except DeribitError as e:
        await update.message.reply_text(f"Instrument lookup error: {e}")
    except Exception:
        await update.message.reply_text("Usage: /hedge_protective_put ETH 2 3000 30 0.5")

        await update.message.reply_text("Usage: /hedge_protective_put ETH 2 1800 30 0.5")


async def risk_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates VaR, max drawdown, and correlation matrix over historical period."""
    try:
        asset, spot, perp, days, conf = context.args
        asset = asset.upper()
        spot = float(spot)
        perp = float(perp)
        days = int(days)
        conf = float(conf)

        end_ts = int(time.time() * 1000)
        start_ts = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)

        # Fetch historical series
        perp_series = client.get_historical_prices(f"{asset}-PERPETUAL", start_ts, end_ts)
        if len(perp_series) < 2:
            raise ValueError("Not enough perp data")

        # (Optional) Replace this with a true spot series when available
        spot_series = client.get_historical_prices(f"{asset}-PERPETUAL", start_ts, end_ts)

        rc = RiskCalculator(spot, perp)

        # VaR & max drawdown
        var = rc.var(perp_series, conf)
        pnl = [(p - perp_series[0]) * spot for p in perp_series]
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
        

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button callbacks for hedging actions."""
    query = update.callback_query
    await query.answer()
    try:
        rc = RiskCalculator(
            portfolio['spot'], portfolio['perp'], portfolio['threshold']
        )
        amount = rc.hedge_amount()
        portfolio['perp'] -= amount  # simulate hedge
        text = f"✅ Hedged {amount:.4f} of {portfolio['asset']}. New perp size: {portfolio['perp']:.4f}"
        await query.edit_message_text(text)
    except Exception as e:
        logger.exception("Error in button_handler")
        await query.edit_message_text(f"Error processing hedge: {e}")

if __name__=="__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("monitor_risk", monitor_risk))
    app.add_handler(CommandHandler("hedge_protective_put", hedge_protective_put))
    app.add_handler(CommandHandler("risk_report", risk_report))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Bot running...")
    app.run_polling()

