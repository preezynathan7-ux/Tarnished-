# ============================================================
# NEXUS Trading Bot v2 - Corrections critiques
# Bybit Demo | 30m | BSBUSDT
# Corrections: PnL réel via API, trailing natif Bybit,
# reset quotidien, notifications fiables, heartbeat
# ============================================================

import time
import logging
import math
import os
from datetime import datetime, date
from pybit.unified_trading import HTTP
import requests

# ============================================================
#  CONFIGURATION
# ============================================================

API_KEY = os.environ.get("BYBIT_API_KEY", "yhIWArGAp0JwDLDja2")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "Xlg8fjG557YapL9B6EwHBCtotWkiadnENRtE")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8878379567:AAECojwAmR2P10PXOJgQdJJtAbwXBPkwoaQ")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "7645348359")

SYMBOL = "BSBUSDT"
TIMEFRAME = "30"
LEVERAGE = 10
RISK_PER_TRADE = 0.02
MAX_DAILY_LOSS_PCT = 0.06

SL_ATR_MULT = 2.7
RR_RATIO = 2.4
SCORE_MIN = 2.0
SCORE_MIN_CONTRE = 5.3

# Trailing stop natif Bybit (géré par la plateforme, pas par le bot en boucle)
TRAILING_ACTIVATE_PCT = 0.015   # active le trailing après ce % de profit
TRAILING_ATR_MULT = 1.8         # distance du trailing = X fois l'ATR

HEARTBEAT_INTERVAL_SEC = 4 * 3600   # message de vie toutes les 4h
COOLDOWN_AFTER_TRADE_SEC = 18 * 60
LOOP_SLEEP_SEC = 40

JOURNAL_FILE = "journal_nexus.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")

session = HTTP(testnet=False, demo=True, api_key=API_KEY, api_secret=API_SECRET)

# État global
daily_start_capital = 0.0
last_reset_date = date.today()
last_trade_time = 0
last_heartbeat = 0
tracked_position = None   # dict: side, entry, size, trailing_active

# ============================================================
#  TELEGRAM
# ============================================================

def tg(msg: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": msg,
            "parse_mode": "HTML", "disable_web_page_preview": True
        }, timeout=10)
        if r.status_code != 200:
            logging.error(f"Telegram HTTP {r.status_code}: {r.text}")
    except Exception as e:
        logging.error(f"Telegram error: {e}")

def log_trade(action, side, price, score=0, pnl=0, reason=""):
    try:
        with open(JOURNAL_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} | {action} | {side} | {price:.6f} | score:{score:.2f} | P&L:{pnl:.2f} | {reason}\n")
    except Exception as e:
        logging.error(f"Journal write error: {e}")

def send_stats():
    try:
        real_pos = get_real_position()
        status_line = ""
        if real_pos:
            status_line = (f"\n📍 <b>Position ouverte</b>: {real_pos['side']} "
                            f"{real_pos['size']} @ {real_pos['entry']:.5f} "
                            f"(PnL non réalisé: {real_pos['unrealised_pnl']:+.2f} USDT)\n")

        if not os.path.exists(JOURNAL_FILE):
            tg(f"📊 Aucun trade clôturé enregistré.{status_line}")
            return

        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

        today = datetime.now().strftime("%Y-%m-%d")
        today_closed = [l for l in lines if today in l and "CLOSE" in l]

        if not today_closed:
            tg(f"📊 Aucun trade clôturé aujourd'hui ({today}).{status_line}")
            return

        pnls = []
        for l in today_closed:
            try:
                pnls.append(float(l.split("P&L:")[1].split()[0]))
            except:
                pass

        total = len(pnls)
        winning = [p for p in pnls if p > 0]
        win_rate = (len(winning) / total * 100) if total else 0
        total_pnl = sum(pnls)

        msg = (
            f"🔥 <b>NEXUS – RÉSUMÉ DU {today}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Trades clôturés: {total}\n"
            f"Gagnants: {len(winning)} | Perdants: {total - len(winning)}\n"
            f"Winrate: {win_rate:.1f}%\n"
            f"P&L réalisé: <b>{total_pnl:+.2f} USDT</b>"
            f"{status_line}\n"
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

# ============================================================
#  FONCTIONS BYBIT
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

def get_real_position():
    try:
        res = session.get_positions(category="linear", symbol=SYMBOL)
        for p in res["result"]["list"]:
            size = float(p["size"])
            if size > 0:
                return {
                    "side": p["side"],
                    "size": size,
                    "entry": float(p["avgPrice"]),
                    "unrealised_pnl": float(p.get("unrealisedPnl", 0)),
                }
        return None
    except Exception as e:
        logging.error(f"Get position error: {e}")
        return None

def get_last_closed_pnl():
    """Récupère le PnL réel du dernier trade clôturé sur Bybit (pas d'estimation locale)."""
    try:
        res = session.get_closed_pnl(category="linear", symbol=SYMBOL, limit=1)
        items = res["result"]["list"]
        if items:
            item = items[0]
            return {
                "pnl": float(item["closedPnl"]),
                "avg_entry": float(item["avgEntryPrice"]),
                "avg_exit": float(item["avgExitPrice"]),
                "side": item["side"],
                "qty": float(item["qty"]),
            }
    except Exception as e:
        logging.error(f"get_closed_pnl error: {e}")
    return None

def set_native_trailing_stop(distance):
    """Active le trailing stop natif Bybit (géré côté serveur, plus fiable qu'une boucle Python)."""
    try:
        session.set_trading_stop(
            category="linear",
            symbol=SYMBOL,
            trailingStop=str(round(distance, 5)),
            positionIdx=0
        )
        logging.info(f"Trailing stop natif activé, distance={distance:.5f}")
        return True
    except Exception as e:
        logging.error(f"set_trading_stop error: {e}")
        return False

# ============================================================
#  INDICATEURS (identiques à la version précédente)
# ============================================================

def get_klines(interval, limit=200):
    try:
        res = session.get_kline(category="linear", symbol=SYMBOL, interval=interval, limit=limit)
        return list(reversed(res["result"]["list"]))
    except Exception as e:
        logging.error(f"get_klines error: {e}")
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

def get_signal():
    kl = get_klines(TIMEFRAME, 180)
    if len(kl) < 120:
        return None

    closes = [float(x[4]) for x in kl]
    highs = [float(x[2]) for x in kl]
    lows = [float(x[3]) for x in kl]
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

    if current_rsi <= 30:
        buy_score += 1.5 + max(0, (30 - current_rsi) / 30)
        buy_details.append("RSI")
    elif current_rsi >= 80:
        sell_score += 1.5 + max(0, (current_rsi - 80) / 20)
        sell_details.append("RSI")

    if price > ema20[i] > ema50[i]:
        buy_score += 2.1
        buy_details.append("EMA")
    elif price < ema20[i] < ema50[i]:
        sell_score += 2.1
        sell_details.append("EMA")

    if macd_line[i] is not None and macd_sig[i] is not None:
        if macd_line[i] > macd_sig[i] and macd_line[i] > 0:
            buy_score += 1.8
            buy_details.append("MACD")
        elif macd_line[i] < macd_sig[i] and macd_line[i] < 0:
            sell_score += 1.8
            sell_details.append("MACD")

    if stoch_k[i] <= 10 and stoch_d[i] <= 10:
        buy_score += 1.4
        buy_details.append("Stoch")
    elif stoch_k[i] >= 90 and stoch_d[i] >= 90:
        sell_score += 1.4
        sell_details.append("Stoch")

    if vol_ma[i] and current_vol > vol_ma[i] * 1.5:
        if buy_score > 0:
            buy_score += 0.8
            buy_details.append("Vol")
        if sell_score > 0:
            sell_score += 0.8
            sell_details.append("Vol")

    bos_h, bos_b = detect_bos(highs, lows, len(closes)-1)
    if bos_h:
        buy_score += 0.9
        buy_details.append("BOS")
    if bos_b:
        sell_score += 0.9
        sell_details.append("BOS")

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
        side, score, details = "Buy", buy_score, buy_details
    elif sell_score >= required:
        side, score, details = "Sell", sell_score, sell_details

    if side is None:
        return None

    stop_dist = current_atr * SL_ATR_MULT
    if side == "Buy":
        sl = price - stop_dist
        tp = price + stop_dist * RR_RATIO
    else:
        sl = price + stop_dist
        tp = price - stop_dist * RR_RATIO

    trailing_distance = current_atr * TRAILING_ATR_MULT

    return {
        "side": side, "entry": price, "sl": sl, "tp": tp,
        "score": score, "details": details, "daily": daily,
        "trailing_distance": trailing_distance,
    }

# ============================================================
#  TRADING
# ============================================================

def place_order(signal):
    global last_trade_time, tracked_position

    capital = get_balance()
    risk_amount = capital * RISK_PER_TRADE
    stop_dist = abs(signal["entry"] - signal["sl"])
    qty = risk_amount / stop_dist
    qty = math.floor(qty) if qty >= 1 else 0

    if qty <= 0:
        logging.warning("Qty calculée = 0, trade ignoré")
        return False

    try:
        session.set_leverage(category="linear", symbol=SYMBOL,
                              buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except Exception as e:
        logging.warning(f"set_leverage: {e}")

    try:
        session.place_order(
            category="linear", symbol=SYMBOL, side=signal["side"],
            orderType="Market", qty=str(qty),
            stopLoss=str(round(signal["sl"], 5)),
            takeProfit=str(round(signal["tp"], 5)),
            slTriggerBy="LastPrice",
            tpTriggerBy="LastPrice",
            tpslMode="Full",
            timeInForce="GTC",
        )
    except Exception as e:
        logging.error(f"Order error: {e}")
        tg(f"⚠️ <b>Erreur ouverture ordre</b>\n<code>{e}</code>")
        return False

    last_trade_time = time.time()
    tracked_position = {
        "side": signal["side"], "entry": signal["entry"],
        "trailing_active": False, "trailing_distance": signal["trailing_distance"],
    }

    msg = (
        f"{'🟢' if signal['side']=='Buy' else '🔴'} <b>NEXUS — OUVERTURE {signal['side'].upper()}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{SYMBOL}</b> | TF {TIMEFRAME}m\n"
        f"💰 Entrée : <code>{signal['entry']:.5f}</code>\n"
        f"🛑 SL : <code>{signal['sl']:.5f}</code>\n"
        f"🎯 TP : <code>{signal['tp']:.5f}</code>\n"
        f"📦 Quantité : {qty} ({risk_amount:.2f} USDT risqués)\n"
        f"📊 Score : <b>{signal['score']:.1f}</b>\n"
        f"📈 Confluences : {', '.join(signal['details'])}\n"
        f"🌐 Biais journalier : {signal['daily'].upper()}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    tg(msg)
    log_trade("OPEN", signal["side"], signal["entry"], signal["score"], 0, "NEW")
    logging.info(f"ENTRY {signal['side']} @ {signal['entry']:.5f}")
    return True

def manage_trailing(real_pos):
    """Active/ajuste le trailing stop natif une fois le seuil de profit atteint."""
    global tracked_position
    if tracked_position is None or real_pos is None:
        return

    entry = tracked_position["entry"]
    current_price = real_pos["entry"]  # avgPrice reste l'entrée; on utilise mark via unrealised_pnl à défaut
    unrealised = real_pos["unrealised_pnl"]
    size_value = real_pos["size"] * entry
    profit_pct_estimate = (unrealised / size_value) if size_value > 0 else 0

    if not tracked_position["trailing_active"] and profit_pct_estimate >= TRAILING_ACTIVATE_PCT:
        ok = set_native_trailing_stop(tracked_position["trailing_distance"])
        if ok:
            tracked_position["trailing_active"] = True
            tg(f"🔄 <b>Trailing stop activé</b>\nDistance: {tracked_position['trailing_distance']:.5f} "
               f"(profit actuel ≈ {profit_pct_estimate*100:.2f}%)")

def send_close_notification():
    """Va chercher le vrai PnL réalisé sur Bybit et notifie avec les vraies données."""
    closed = get_last_closed_pnl()
    if closed:
        emoji = "✅" if closed["pnl"] > 0 else "❌"
        msg = (
            f"{emoji} <b>NEXUS — POSITION CLÔTURÉE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {SYMBOL} | {closed['side']}\n"
            f"💰 Entrée : <code>{closed['avg_entry']:.5f}</code>\n"
            f"🚪 Sortie : <code>{closed['avg_exit']:.5f}</code>\n"
            f"📦 Quantité : {closed['qty']}\n"
            f"💵 <b>P&L réel : {closed['pnl']:+.2f} USDT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        tg(msg)
        log_trade("CLOSE", closed["side"], closed["avg_exit"], 0, closed["pnl"], "SL/TP/Trailing")
    else:
        tg("✅ <b>Position fermée</b> (détails PnL indisponibles via API)")
        log_trade("CLOSE", "Unknown", 0, 0, 0, "Fermeture détectée, PnL non récupéré")

# ============================================================
#  MAIN
# ============================================================

def main():
    global daily_start_capital, last_reset_date, last_trade_time, last_heartbeat, tracked_position

    tg(f"🚀 <b>NEXUS v2 démarré</b>\nMode : DÉMO BYBIT\nSymbole : {SYMBOL}\nTF : {TIMEFRAME}m")

    real_pos = get_real_position()
    if real_pos:
        tg(f"🔄 <b>Position reprise au démarrage</b>\n{real_pos['side']} {real_pos['size']} @ "
           f"{real_pos['entry']:.5f}\nPnL non réalisé : {real_pos['unrealised_pnl']:+.2f} USDT")
        tracked_position = {"side": real_pos["side"], "entry": real_pos["entry"],
                             "trailing_active": False, "trailing_distance": None}
        logging.info(f"Position reprise: {real_pos}")
    else:
        logging.info("Aucune position ouverte au démarrage")

    capital = get_balance()
    daily_start_capital = capital
    last_reset_date = date.today()
    last_heartbeat = time.time()
    logging.info(f"Capital: {capital:.2f} USDT")

    previous_had_position = real_pos is not None

    while True:
        try:
            check_telegram_commands()

            # Reset quotidien du capital de référence
            if date.today() != last_reset_date:
                daily_start_capital = get_balance()
                last_reset_date = date.today()
                logging.info(f"Reset capital journalier: {daily_start_capital:.2f} USDT")
                tg(f"🔁 Nouveau jour — capital de référence reset à {daily_start_capital:.2f} USDT")

            capital = get_balance()
            daily_pnl = capital - daily_start_capital

            if daily_pnl <= -(daily_start_capital * MAX_DAILY_LOSS_PCT):
                tg(f"🛑 <b>MAX DAILY LOSS atteint</b>\nP&L jour : {daily_pnl:.2f} USDT\nBot en pause 30 min.")
                time.sleep(1800)
                continue

            # Heartbeat périodique pour ne jamais rester dans le silence
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL_SEC:
                real_pos = get_real_position()
                if real_pos:
                    status = (f"📍 Position en cours: {real_pos['side']} @ {real_pos['entry']:.5f} "
                              f"(PnL: {real_pos['unrealised_pnl']:+.2f} USDT)")
                else:
                    status = "📭 Aucune position ouverte, en attente d'un signal."
                tg(f"💓 <b>NEXUS actif</b>\nCapital: {capital:.2f} USDT\n{status}")
                last_heartbeat = time.time()

            # Synchronisation position
            real_pos = get_real_position()
            has_position = real_pos is not None

            if previous_had_position and not has_position:
                send_close_notification()
                tracked_position = None

            if has_position:
                manage_trailing(real_pos)

            previous_had_position = has_position

            if not has_position and time.time() - last_trade_time > COOLDOWN_AFTER_TRADE_SEC:
                signal = get_signal()
                if signal:
                    place_order(signal)

            time.sleep(LOOP_SLEEP_SEC)

        except Exception as e:
            logging.error(f"Loop error: {e}")
            tg(f"⚠️ Erreur boucle principale:\n<code>{e}</code>")
            time.sleep(20)

if __name__ == "__main__":
    main()
