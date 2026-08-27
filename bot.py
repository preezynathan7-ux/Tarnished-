# ============================================================
# NEXUS Trading Bot - Version Complète
# Bybit Demo | 30m | BSBUSDT
# Logique V4 + Score validé
# ============================================================

import time
import logging
import math
import os
from datetime import datetime
from pybit.unified_trading import HTTP
import requests

# ============================================================
#  CONFIGURATION
# ============================================================

API_KEY = "yhIWArGAp0JwDLDja2"
API_SECRET = "Xlg8fjG557YapL9B6EwHBCtotWkiadnENRtE"

TELEGRAM_TOKEN = "8878379567:AAECojwAmR2P10PXOJgQdJJtAbwXBPkwoaQ"
TELEGRAM_CHAT_ID = "7645348359"

SYMBOL = "BSBUSDT"
TIMEFRAME = "30"
LEVERAGE = 10
RISK_PER_TRADE = 0.02
MAX_DAILY_LOSS_PCT = 0.06

# Risk / Reward (V4)
SL_ATR_MULT = 2.7
RR_RATIO = 2.4

# Score
SCORE_MIN = 3.8
SCORE_MIN_CONTRE = 5.3

JOURNAL_FILE = "journal_nexus.txt"

# ============================================================
#  INIT
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")

session = HTTP(
    testnet=False,
    demo=True,
    api_key=API_KEY,
    api_secret=API_SECRET
)

capital = 0.0
daily_start_capital = 0.0
open_position = None
last_trade_time = 0
capital_peak = 0.0

# ============================================================
#  TELEGRAM + JOURNAL
# ============================================================

def tg(msg: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=10)
    except Exception as e:
        logging.error(f"Telegram error: {e}")

def log_trade(action, side, price, score, pnl=0, reason=""):
    try:
        with open(JOURNAL_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} | {action} | {side} | {price:.6f} | score:{score:.2f} | P&L:{pnl:.2f} | {reason}\n")
    except:
        pass

def send_stats():
    try:
        if not os.path.exists(JOURNAL_FILE):
            tg("📊 Aucun trade enregistré pour le moment.")
            return
        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        today = datetime.now().strftime("%Y-%m-%d")
        today_trades = [l for l in lines if today in l and "CLOSE" in l]
        total = len(today_trades)
        if total == 0:
            tg(f"📊 Aucun trade clôturé aujourd'hui ({today})")
            return
        winning = [l for l in today_trades if "P&L:" in l and float(l.split("P&L:")[1].split()[0]) > 0]
        win_rate = len(winning) / total * 100
        total_pnl = sum(float(l.split("P&L:")[1].split()[0]) for l in today_trades if "P&L:" in l)
        msg = (
            f"🔥 <b>NEXUS – RÉSUMÉ DU {today}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Trades : {total}\n"
            f"Gagnants : {len(winning)} | Perdants : {total - len(winning)}\n"
            f"Winrate : {win_rate:.1f}%\n"
            f"P&L total : <b>{total_pnl:+.2f} USDT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        tg(msg)
    except Exception as e:
        tg(f"❌ Erreur stats : {e}")

def check_telegram_commands():
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        resp = requests.get(url, timeout=5).json()
        if resp.get("ok") and resp.get("result"):
            for update in resp["result"]:
                if "message" in update and "text" in update["message"]:
                    text = update["message"]["text"]
                    chat_id = str(update["message"]["chat"]["id"])
                    if text == "/stats" and chat_id == str(TELEGRAM_CHAT_ID):
                        send_stats()
                        update_id = update["update_id"]
                        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={update_id+1}", timeout=5)
    except Exception as e:
        logging.error(f"Telegram commands error: {e}")

def msg_entry(side, entry, sl, tp, score, qty, details, proba, daily):
    emoji = "🟢" if side == "Buy" else "🔴"
    return (
        f"{emoji} <b>NEXUS — {side.upper()}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{SYMBOL}</b>\n"
        f"💰 Entrée : <code>{entry:.5f}</code>\n"
        f"🛑 SL : <code>{sl:.5f}</code>\n"
        f"🎯 TP : <code>{tp:.5f}</code>\n"
        f"📊 Score : <b>{score:.1f}</b>\n"
        f"🎲 Chances TP : <b>{proba:.0f}%</b>\n"
        f"📦 Quantité : {qty}\n"
        f"📈 {', '.join(details)}\n"
        f"🌐 Daily : {daily.upper()}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

def msg_exit(side, reason, pnl, capital_now):
    emoji = "✅" if pnl >= 0 else "❌"
    return (
        f"{emoji} <b>NEXUS — FERMETURE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 {SYMBOL} | {side.upper()}\n"
        f"📄 Raison : <b>{reason}</b>\n"
        f"💵 P&L : <b>{pnl:+.2f} USDT</b>\n"
        f"💼 Capital : {capital_now:.2f} USDT\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

# ============================================================
#  INDICATEURS COMPLETS
# ============================================================

def get_klines(interval, limit=200):
    try:
        res = session.get_kline(category="linear", symbol=SYMBOL, interval=interval, limit=limit)
        return list(reversed(res["result"]["list"]))
    except Exception as e:
        logging.error(f"Klines error: {e}")
        return []

def ema(values, period):
    if len(values) < period:
        return [None] * len(values)
    result = [None] * len(values)
    result[period-1] = sum(values[:period]) / period
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i-1]
    return result

def rsi(closes, period=14):
    r = [50.0] * len(closes)
    if len(closes) < period + 1:
        return r
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    r[period] = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i-1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i-1]) / period
        r[i] = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    return r

def macd(closes, fast=12, slow=26, signal=9):
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [None] * len(closes)
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]
    valid = [x for x in macd_line if x is not None]
    sig = ema(valid, signal)
    signal_line = [None] * len(closes)
    offset = len(closes) - len(valid)
    for i, v in enumerate(sig):
        if offset + i < len(closes):
            signal_line[offset + i] = v
    return macd_line, signal_line

def stoch_rsi(closes, period=14, smooth=3):
    r = rsi(closes, period)
    k = [50.0] * len(closes)
    for i in range(period, len(closes)):
        window = r[i-period+1:i+1]
        mini, maxi = min(window), max(window)
        k[i] = 50 if maxi == mini else ((r[i] - mini) / (maxi - mini)) * 100
    for i in range(len(closes)):
        if i >= smooth - 1:
            k[i] = sum(k[i-smooth+1:i+1]) / smooth
    return k, k

def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return [None] * len(closes)
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr_list = [None] * len(closes)
    atr_list[period] = sum(trs[:period]) / period
    for i in range(period+1, len(closes)):
        atr_list[i] = (atr_list[i-1] * (period-1) + trs[i-1]) / period
    return atr_list

def volume_ma(volumes, period=20):
    vma = [None] * len(volumes)
    for i in range(period-1, len(volumes)):
        vma[i] = sum(volumes[i-period+1:i+1]) / period
    return vma

def detect_bos(highs, lows, idx, lookback=5):
    if idx < lookback:
        return False, False
    return highs[idx] > max(highs[idx-lookback:idx]), lows[idx] < min(lows[idx-lookback:idx])

def detect_fvg(highs, lows, idx):
    if idx < 3:
        return False, False
    return lows[idx] > highs[idx-3], highs[idx] < lows[idx-3]

def detect_pullback(closes, idx, direction, threshold=0.30):
    if idx < 10:
        return False
    if direction == "bull":
        recent_high = max(closes[idx-10:idx-3])
        min_low = min(closes[idx-5:idx])
        if recent_high > 0:
            retrace = (recent_high - min_low) / recent_high
            return retrace <= threshold and closes[idx] > min_low
    else:
        recent_low = min(closes[idx-10:idx-3])
        max_high = max(closes[idx-5:idx])
        if recent_low > 0:
            retrace = (max_high - recent_low) / recent_low
            return retrace <= threshold and closes[idx] < max_high
    return False

def get_daily_trend():
    kl = get_klines("D", 60)
    if len(kl) < 30:
        return "neutral"
    closes = [float(x[4]) for x in kl]
    e20 = ema(closes, 20)[-1]
    e50 = ema(closes, 50)[-1]
    if e20 is None or e50 is None:
        return "neutral"
    if e20 > e50 * 1.008:
        return "bull"
    if e20 < e50 * 0.992:
        return "bear"
    return "neutral"

# ============================================================
#  SIGNAL COMPLET
# ============================================================

def get_signal():
    kl = get_klines(TIMEFRAME, 180)
    if len(kl) < 120:
        return None

    closes = [float(x[4]) for x in kl]
    highs  = [float(x[2]) for x in kl]
    lows   = [float(x[3]) for x in kl]
    volumes = [float(x[5]) for x in kl]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    rsi_list = rsi(closes)
    macd_line, macd_sig = macd(closes)
    stoch_k, stoch_d = stoch_rsi(closes)
    atr_list = atr(highs, lows, closes)
    vol_ma = volume_ma(volumes)

    i = -1
    if any(x is None for x in [ema20[i], ema50[i], ema200[i], atr_list[i]]):
        return None

    price = closes[i]
    current_atr = atr_list[i]
    current_rsi = rsi_list[i]
    current_vol = volumes[i]

    buy_score = sell_score = 0.0
    buy_details, sell_details = [], []

    # RSI ≤30 / ≥80 + bonus extrême
    if current_rsi <= 30:
        pts = 1.5 + max(0, (30 - current_rsi) / 30)
        buy_score += pts
        buy_details.append("RSI")
    elif current_rsi >= 80:
        pts = 1.5 + max(0, (current_rsi - 80) / 20)
        sell_score += pts
        sell_details.append("RSI")

    # EMA
    if price > ema20[i] > ema50[i]:
        buy_score += 2.1
        buy_details.append("EMA")
    elif price < ema20[i] < ema50[i]:
        sell_score += 2.1
        sell_details.append("EMA")

    # MACD
    if macd_line[i] is not None and macd_sig[i] is not None:
        if macd_line[i] > macd_sig[i] and macd_line[i] > 0:
            buy_score += 1.8
            buy_details.append("MACD")
        elif macd_line[i] < macd_sig[i] and macd_line[i] < 0:
            sell_score += 1.8
            sell_details.append("MACD")

    # StochRSI ≤10 / ≥90
    if stoch_k[i] <= 10 and stoch_d[i] <= 10:
        buy_score += 1.4
        buy_details.append("Stoch")
    elif stoch_k[i] >= 90 and stoch_d[i] >= 90:
        sell_score += 1.4
        sell_details.append("Stoch")

    # Volume
    if vol_ma[i] and current_vol > vol_ma[i] * 1.5:
        if buy_score > 0:
            buy_score += 0.8
            buy_details.append("Vol")
        if sell_score > 0:
            sell_score += 0.8
            sell_details.append("Vol")

    # BOS
    bos_h, bos_b = detect_bos(highs, lows, len(closes)-1)
    if bos_h:
        buy_score += 0.9
        buy_details.append("BOS")
    if bos_b:
        sell_score += 0.9
        sell_details.append("BOS")

    # FVG + Pullback (bonus)
    fvg_h, fvg_b = detect_fvg(highs, lows, len(closes)-1)
    if fvg_h:
        buy_score += 0.7
        buy_details.append("FVG")
    if fvg_b:
        sell_score += 0.7
        sell_details.append("FVG")

    if detect_pullback(closes, len(closes)-1, "bull"):
        buy_score += 0.8
        buy_details.append("Pullback")
    if detect_pullback(closes, len(closes)-1, "bear"):
        sell_score += 0.8
        sell_details.append("Pullback")

    # Filtre Daily
    daily = get_daily_trend()
    required = SCORE_MIN
    if daily == "bull" and sell_score > 0:
        required = SCORE_MIN_CONTRE
    elif daily == "bear" and buy_score > 0:
        required = SCORE_MIN_CONTRE

    side = None
    score = 0
    details = []
    if buy_score >= required:
        side = "Buy"
        score = buy_score
        details = buy_details
    elif sell_score >= required:
        side = "Sell"
        score = sell_score
        details = sell_details

    if side is None:
        return None

    stop_dist = current_atr * SL_ATR_MULT
    if side == "Buy":
        sl = price - stop_dist
        tp = price + stop_dist * RR_RATIO
    else:
        sl = price + stop_dist
        tp = price - stop_dist * RR_RATIO

    # Chances d'atteindre le TP
    dist_tp = abs(tp - price)
    proba = max(25, min(78, 58 - (dist_tp / current_atr - 2.4) * 7))

    return {
        "side": side,
        "entry": price,
        "sl": sl,
        "tp": tp,
        "score": score,
        "details": details,
        "daily": daily,
        "proba": proba
    }

# ============================================================
#  TRADING
# ============================================================

def get_balance():
    try:
        res = session.get_wallet_balance(accountType="UNIFIED")
        for c in res["result"]["list"][0]["coin"]:
            if c["coin"] == "USDT":
                return float(c["walletBalance"])
    except Exception as e:
        logging.error(f"Balance error: {e}")
    return 0.0

def place_order(signal):
    global capital, open_position, last_trade_time, capital_peak

    risk_amount = capital * RISK_PER_TRADE
    stop_dist = abs(signal["entry"] - signal["sl"])
    qty = risk_amount / stop_dist
    qty = math.floor(qty) if qty >= 1 else 0

    if qty <= 0:
        return False

    try:
        session.set_leverage(
            category="linear",
            symbol=SYMBOL,
            buyLeverage=str(LEVERAGE),
            sellLeverage=str(LEVERAGE)
        )

        session.place_order(
            category="linear",
            symbol=SYMBOL,
            side=signal["side"],
            orderType="Market",
            qty=str(qty),
            stopLoss=str(round(signal["sl"], 5)),
            takeProfit=str(round(signal["tp"], 5)),
            timeInForce="GTC"
        )

        open_position = {
            "side": signal["side"],
            "entry": signal["entry"],
            "qty": qty,
            "sl": signal["sl"],
            "tp": signal["tp"],
            "score": signal["score"]
        }
        last_trade_time = time.time()
        capital_peak = max(capital_peak, capital)

        tg(msg_entry(
            signal["side"], signal["entry"], signal["sl"], signal["tp"],
            signal["score"], qty, signal["details"], signal["proba"], signal["daily"]
        ))
        log_trade("OPEN", signal["side"], signal["entry"], signal["score"], 0, "NEW")
        logging.info(f"ENTRY {signal['side']} @ {signal['entry']:.5f} | Score {signal['score']:.1f}")
        return True

    except Exception as e:
        logging.error(f"Order error: {e}")
        tg(f"⚠️ Erreur ordre :\n<code>{e}</code>")
        return False

def check_position():
    global open_position, capital
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        has_position = any(float(p["size"]) > 0 for p in res["result"]["list"])

        if open_position and not has_position:
            capital = get_balance()
            # PnL approximatif (on pourra l'améliorer plus tard)
            tg(msg_exit(open_position["side"], "SL/TP", 0, capital))
            log_trade("CLOSE", open_position["side"], 0, open_position["score"], 0, "SL/TP")
            open_position = None
            logging.info("Position fermée")
    except Exception as e:
        logging.error(f"Check position error: {e}")

# ============================================================
#  MAIN
# ============================================================

def main():
    global capital, daily_start_capital, capital_peak

    tg(f"🚀 <b>NEXUS démarré</b>\nMode : <b>DÉMO BYBIT</b>\nSymbole : {SYMBOL}\nTimeframe : 30m\nScore min : {SCORE_MIN}")

    capital = get_balance()
    daily_start_capital = capital
    capital_peak = capital
    logging.info(f"Capital: {capital:.2f} USDT")

    while True:
        try:
            check_telegram_commands()

            capital = get_balance()
            daily_pnl = capital - daily_start_capital

            if daily_pnl <= -(daily_start_capital * MAX_DAILY_LOSS_PCT):
                tg(f"🛑 <b>MAX DAILY LOSS atteint</b>\nP&L jour : {daily_pnl:.2f} USDT\nBot en pause.")
                time.sleep(1800)
                continue

            check_position()

            if open_position is None and time.time() - last_trade_time > 18 * 60:
                signal = get_signal()
                if signal:
                    place_order(signal)

            time.sleep(40)

        except Exception as e:
            logging.error(f"Loop error: {e}")
            time.sleep(20)

if __name__ == "__main__":
    main()
