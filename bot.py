from pybit.unified_trading import HTTP
import time
import logging
import os
import math
import requests
from datetime import datetime

# ============================================================
#  CONFIGURATION
# ============================================================

SYMBOLE = "BSBUSDT"
TIMEFRAME = "15"
LEVERAGE = 10
TAILLE_POSITION_BSB = 200
STOP_LOSS = 0.025
TAKE_PROFIT = 0.075
SCORE_SEUIL = 1.2

API_KEY = os.getenv("API_KEY") or "yhIWArGAp0JwDLDja2"
API_SECRET = os.getenv("API_SECRET") or "Xlg8fjG557YapL9B6EwHBCtotWkiadnENRtE"

# --- Telegram ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or "8878379567:AAECojwAmR2P10PXOJgQdJJtAbwXBPkwoaQ"
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or "7645348359"

RSI6_OVERSOLD = 30
RSI24_OVERBOUGHT = 62

RETOURNEMENT_RSI6 = 30
RETOURNEMENT_STOCH = 20
RETOURNEMENT_VOLUME_MA = 10

ATR_THRESHOLD = 0.00072
VOLATILITY_PERIOD = 20

POIDS_MACD = 0.6
POIDS_EMA = 0.6
POIDS_RSI = 0.6
POIDS_STOCHRSI = 0.6
POIDS_BOS = 0.6
POIDS_VOLUME = 0.6
POIDS_PSAR = 0.6
POIDS_ATR = 0.6
POIDS_STOCH = 0.6

VOLUME_SPIKE = 1.3

JOURNAL_FILE = "journal_trading.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

session = HTTP(testnet=False, demo=True, api_key=API_KEY, api_secret=API_SECRET)

# ============================================================
#  TELEGRAM
# ============================================================

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logging.error(f"❌ Erreur envoi Telegram: {e}")
        
def send_stats():
    try:
        with open(JOURNAL_FILE, "r") as f:
            lines = f.readlines()
        today = datetime.now().strftime("%Y-%m-%d")
        today_trades = [l for l in lines if today in l and "CLOSE" in l]
        total = len(today_trades)
        if total == 0:
            send_telegram(f"📊 Aucun trade clôturé aujourd'hui ({today})")
            return
        winning = [l for l in today_trades if "P&L:" in l and float(l.split("P&L:")[1].split()[0]) > 0]
        win_rate = len(winning) / total * 100 if total > 0 else 0
        total_pnl = sum([float(l.split("P&L:")[1].split()[0]) for l in today_trades if "P&L:" in l])
        msg = (f"📊 RÉSUMÉ DU {today}\n"
               f"   Trades: {total} | Gagnants: {len(winning)} | Perdants: {total - len(winning)}\n"
               f"   Win rate: {win_rate:.1f}% | P&L total: {total_pnl:.2f} USDT")
        send_telegram(msg)
    except:
        send_telegram("❌ Erreur lors de la lecture du journal.")
    
# ============================================================
#  JOURNAL ET PROBABILITÉ TP
# ============================================================

def log_trade(action, side, price, score, pnl=0, reason="", proba_tp=0):
    try:
        with open(JOURNAL_FILE, "a") as f:
            f.write(f"{datetime.now().isoformat()} | {action} | {side} | {price:.6f} | score:{score:.2f} | P&L:{pnl:.2f} | probaTP:{proba_tp:.1f}% | {reason}\n")
    except:
        pass

def calculate_tp_probability(ohlcv, entry_price, sl_price, tp_price):
    if len(ohlcv) < VOLATILITY_PERIOD:
        return 50.0
    closes = [float(c[3]) for c in ohlcv[-VOLATILITY_PERIOD:]]
    if len(closes) < 2:
        return 50.0
    returns = []
    for i in range(1, len(closes)):
        returns.append((closes[i] - closes[i-1]) / closes[i-1])
    if not returns:
        return 50.0
    std_dev = math.sqrt(sum([r**2 for r in returns]) / len(returns))
    daily_volatility = std_dev * 100
    tp_distance = abs(tp_price - entry_price) / entry_price * 100
    sl_distance = abs(sl_price - entry_price) / entry_price * 100
    if sl_distance == 0:
        return 50.0
    ratio = tp_distance / sl_distance
    if daily_volatility > tp_distance * 0.8:
        prob = 70.0
    elif daily_volatility > tp_distance * 0.5:
        prob = 55.0
    else:
        prob = 40.0
    if ratio > 2.5:
        prob = min(prob * 0.8, 60)
    elif ratio < 1.5:
        prob = min(prob * 1.2, 85)
    return max(5.0, min(95.0, prob))

def print_daily_summary():
    try:
        with open(JOURNAL_FILE, "r") as f:
            lines = f.readlines()
        today = datetime.now().strftime("%Y-%m-%d")
        today_trades = [l for l in lines if today in l and "CLOSE" in l]
        total = len(today_trades)
        if total == 0:
            msg = f"📊 Aucun trade clôturé aujourd'hui ({today})"
            logging.info(msg)
            send_telegram(msg)
            return
        winning = [l for l in today_trades if "P&L:" in l and float(l.split("P&L:")[1].split()[0]) > 0]
        win_rate = len(winning) / total * 100 if total > 0 else 0
        total_pnl = sum([float(l.split("P&L:")[1].split()[0]) for l in today_trades if "P&L:" in l])
        msg = (f"📊 RÉSUMÉ DU {today}\n"
               f"   Trades: {total} | Gagnants: {len(winning)} | Perdants: {total - len(winning)}\n"
               f"   Win rate: {win_rate:.1f}% | P&L total: {total_pnl:.2f} USDT")
        logging.info(msg)
        send_telegram(msg)
    except:
        pass

def collect_and_log_market_info(ohlcv):
    if not ohlcv or len(ohlcv) < 50:
        return
    last = float(ohlcv[-1][3])
    ema50 = get_ema(ohlcv, len(ohlcv)-1, 50)
    ema200 = get_ema(ohlcv, len(ohlcv)-1, 200)
    rsi6 = get_rsi(ohlcv, len(ohlcv)-1, 6)
    rsi24 = get_rsi(ohlcv, len(ohlcv)-1, 24)
    atr = get_atr(ohlcv, len(ohlcv)-1, 14)
    vol_ma = get_volume_ma(ohlcv, len(ohlcv)-1, 20)
    current_volume = float(ohlcv[-1][4])
    vol_spike = current_volume / vol_ma if vol_ma and vol_ma > 0 else 1.0

    if ema50 is not None and ema200 is not None:
        trend = "HAUSSIÈRE" if ema50 > ema200 else "BAISSIÈRE"
    else:
        trend = "INCONNUE"

    logging.info("="*50)
    logging.info("📊 ANALYSE MARCHÉ (collecte horaire)")
    logging.info(f"💰 Prix: {last:.6f}")
    logging.info(f"📈 Tendance: {trend}")
    if ema50 is not None:
        logging.info(f"   EMA50: {ema50:.6f}")
    else:
        logging.info("   EMA50: indisponible")
    if ema200 is not None:
        logging.info(f"   EMA200: {ema200:.6f}")
    else:
        logging.info("   EMA200: indisponible")
    logging.info(f"📉 RSI6: {rsi6:.1f} | RSI24: {rsi24:.1f}")
    logging.info(f"📊 ATR: {atr:.6f}" if atr is not None else "📊 ATR: indisponible")
    logging.info(f"📊 Volume spike: {vol_spike:.2f}x")
    logging.info("="*50)

# ============================================================
#  INDICATEURS
# ============================================================

def get_current_price():
    tickers = session.get_tickers(category="linear", symbol=SYMBOLE)
    return float(tickers["result"]["list"][0]["lastPrice"])

def get_ohlcv(limit=100):
    data = session.get_kline(category="linear", symbol=SYMBOLE, interval=15, limit=limit)
    return data["result"]["list"]

def get_rsi(ohlcv, index, period=14):
    if index < period + 1:
        return 50
    closes = [float(c[3]) for c in ohlcv[:index+1]]
    gains, losses = 0, 0
    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += abs(diff)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))

def get_ema(ohlcv, index, period):
    if index < period:
        return None
    closes = [float(c[3]) for c in ohlcv[:index+1]]
    alpha = 2 / (period + 1)
    result = [closes[0]]
    for v in closes[1:]:
        result.append(alpha * v + (1 - alpha) * result[-1])
    return result[-1]

def get_macd(ohlcv, index):
    if index < 26:
        return None, None
    e12 = get_ema(ohlcv, index, 12)
    e26 = get_ema(ohlcv, index, 26)
    if e12 is None or e26 is None:
        return None, None
    macd = e12 - e26
    signal = e12 * 0.15 + e26 * 0.85
    return macd, signal

def get_stoch_rsi(ohlcv, index, period=14):
    if index < period + 1:
        return 50, 50
    rsi_values = []
    for i in range(index - period, index + 1):
        rsi_values.append(get_rsi(ohlcv, i, period))
    if not rsi_values:
        return 50, 50
    mn, mx = min(rsi_values), max(rsi_values)
    if mx == mn:
        return 50, 50
    stoch = (rsi_values[-1] - mn) / (mx - mn)
    return stoch * 100, stoch * 100

def get_stoch(ohlcv, index, k_period=14, d_period=3):
    if index < k_period + d_period:
        return 50, 50
    closes = [float(c[3]) for c in ohlcv[index-k_period+1:index+1]]
    lows = [float(c[2]) for c in ohlcv[index-k_period+1:index+1]]
    highs = [float(c[1]) for c in ohlcv[index-k_period+1:index+1]]
    lowest = min(lows)
    highest = max(highs)
    if highest == lowest:
        return 50, 50
    k = 100 * (closes[-1] - lowest) / (highest - lowest)
    d = k
    return k, d

def get_psar(ohlcv, index, step=0.02, max_step=0.2):
    if index < 2:
        return None
    high = float(ohlcv[index][1])
    low = float(ohlcv[index][2])
    prev_high = float(ohlcv[index-1][1])
    prev_low = float(ohlcv[index-1][2])
    if high > prev_high:
        return prev_low * (1 - step)
    else:
        return prev_high * (1 + step)

def get_atr(ohlcv, index, period=14):
    if index < period:
        return None
    true_ranges = []
    for i in range(index - period + 1, index + 1):
        high = float(ohlcv[i][1])
        low = float(ohlcv[i][2])
        prev_close = float(ohlcv[i-1][3]) if i > 0 else low
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    return sum(true_ranges) / len(true_ranges)

def detect_bos(ohlcv, index):
    if index < 5:
        return False, False
    highs = [float(c[1]) for c in ohlcv[index-5:index]]
    lows = [float(c[2]) for c in ohlcv[index-5:index]]
    return float(ohlcv[index][1]) > max(highs), float(ohlcv[index][2]) < min(lows)

def get_volume_ma(ohlcv, index, period=20):
    if index < period:
        return None
    volumes = [float(c[4]) for c in ohlcv[index-period:index]]
    return sum(volumes) / len(volumes)

def get_position():
    pos = session.get_positions(category="linear", symbol=SYMBOLE)
    if pos["result"]["list"]:
        p = pos["result"]["list"][0]
        if float(p["size"]) != 0:
            return p["side"].lower(), float(p["size"]), float(p["avgPrice"])
    return None, 0, 0

def create_order(side, qty):
    try:
        session.place_order(
            category="linear",
            symbol=SYMBOLE,
            side=side.capitalize(),
            orderType="Market",
            qty=str(qty),
            timeInForce="GTC"
        )
        return True
    except Exception as e:
        logging.error(f"❌ Erreur ordre : {e}")
        return False

def close_position(side, qty):
    opp = "Buy" if side == "sell" else "Sell"
    return create_order(opp, qty)

# ============================================================
#  BOUCLE PRINCIPALE – GAMBIT
# ============================================================

def bot():
    logging.info("🤖 Bot 'Gambit' démarré (mode démo)")
    logging.info(f"📊 Symbole: {SYMBOLE} | Levier: {LEVERAGE}x | Position: {TAILLE_POSITION_BSB} BSB")
    logging.info(f"📈 Seuil: {SCORE_SEUIL} | Poids MACD: {POIDS_MACD} (égal aux autres)")
    logging.info(f"🔄 Règle de retournement fort: RSI6 < {RETOURNEMENT_RSI6} + StochRSI < {RETOURNEMENT_STOCH} + Volume > MA{RETOURNEMENT_VOLUME_MA}")
    logging.info(f"⏳ Filtre ATR: seuil = {ATR_THRESHOLD} (bloque les marchés calmes)")

    position = None
    entry_price = 0
    entry_score = 0.0
    entry_proba_tp = 0.0
    last_daily_log = datetime.now().date()
    last_hourly_collect = datetime.now().hour

    # --- REPRISE DES POSITIONS AU DÉMARRAGE ---
    side, qty, avg = get_position()
    if side is not None:
        position = side
        entry_price = avg
        logging.info(f"🔄 Position reprise: {side.upper()} {qty} BSB à {avg:.6f}")
        send_telegram(f"🔄 Position reprise: {side.upper()} {qty} BSB à {avg:.6f}")

    while True:
        try:
            today = datetime.now().date()
            if today != last_daily_log:
                print_daily_summary()
                last_daily_log = today

            current_hour = datetime.now().hour
            if current_hour != last_hourly_collect:
                ohlcv_temp = get_ohlcv(100)
                if ohlcv_temp and len(ohlcv_temp) >= 30:
                    collect_and_log_market_info(ohlcv_temp)
                last_hourly_collect = current_hour

            ohlcv = get_ohlcv(100)
            if not ohlcv or len(ohlcv) < 30:
                time.sleep(30)
                continue

            price = get_current_price()

            # Vérifier la position en temps réel
            side, qty, avg = get_position()
            if side is not None:
                if position is None:
                    position = side
                    entry_price = avg
                    logging.info(f"🔄 Position reprise: {side.upper()} {qty} BSB à {avg:.6f}")
                    send_telegram(f"🔄 Position reprise: {side.upper()} {qty} BSB à {avg:.6f}")
            else:
                if position is not None:
                    logging.info("🔒 Position fermée")
                    position = None
                    entry_price = 0
                    entry_score = 0.0
                    entry_proba_tp = 0.0

            i = len(ohlcv) - 1

            rsi6 = get_rsi(ohlcv, i, 6)
            rsi24 = get_rsi(ohlcv, i, 24)

            ema50 = get_ema(ohlcv, i, 50)
            ema200 = get_ema(ohlcv, i, 200)
            macd, signal = get_macd(ohlcv, i)
            stoch_k, stoch_d = get_stoch_rsi(ohlcv, i)
            stoch_k2, stoch_d2 = get_stoch(ohlcv, i)
            psar = get_psar(ohlcv, i)
            atr = get_atr(ohlcv, i)
            vol_ma = get_volume_ma(ohlcv, i, 20)
            bos_h, bos_b = detect_bos(ohlcv, i)
            current_volume = float(ohlcv[i][4])

            if atr is not None and atr < ATR_THRESHOLD:
                logging.info(f"⏳ Marché en range (ATR={atr:.6f} < {ATR_THRESHOLD}) → pas de trade")
                time.sleep(30)
                continue

            if ema50 is not None and ema200 is not None:
                trend_bull = ema50 > ema200
                trend_bear = ema50 < ema200
            else:
                trend_bull = False
                trend_bear = False

            psar_bull = price > psar if psar is not None else False
            psar_bear = price < psar if psar is not None else False

            buy_score = 0.0
            sell_score = 0.0
            buy_details = []
            sell_details = []

            retournement_bull = (
                rsi6 < RETOURNEMENT_RSI6 and
                stoch_k < RETOURNEMENT_STOCH and
                stoch_d < RETOURNEMENT_STOCH and
                current_volume > vol_ma * (RETOURNEMENT_VOLUME_MA / 10) if vol_ma is not None else False
            )
            retournement_bear = (
                rsi24 > 60 and
                stoch_k > 80 and
                stoch_d > 80 and
                current_volume > vol_ma * (RETOURNEMENT_VOLUME_MA / 10) if vol_ma is not None else False
            )

            if retournement_bull:
                buy_score += 1.0
                buy_details.append("RETOURNEMENT")
            if retournement_bear:
                sell_score += 1.0
                sell_details.append("RETOURNEMENT")

            if macd is not None and signal is not None:
                if macd > signal:
                    buy_score += POIDS_MACD
                    buy_details.append("MACD")
                else:
                    sell_score += POIDS_MACD
                    sell_details.append("MACD")

            if ema50 is not None:
                if price > ema50:
                    buy_score += POIDS_EMA
                    buy_details.append("EMA")
                else:
                    sell_score += POIDS_EMA
                    sell_details.append("EMA")

            if trend_bull and rsi6 < RSI6_OVERSOLD:
                buy_score += POIDS_RSI
                buy_details.append(f"RSI6({rsi6:.1f})")
            elif trend_bear and rsi24 < RSI24_OVERBOUGHT:
                sell_score += POIDS_RSI
                sell_details.append(f"RSI24({rsi24:.1f})")

            if trend_bull and stoch_k < 10 and stoch_d < 10:
                buy_score += POIDS_STOCHRSI
                buy_details.append("StochRSI")
            elif trend_bear and stoch_k > 90 and stoch_d > 90:
                sell_score += POIDS_STOCHRSI
                sell_details.append("StochRSI")

            if trend_bull and bos_h:
                buy_score += POIDS_BOS
                buy_details.append("BOS")
            elif trend_bear and bos_b:
                sell_score += POIDS_BOS
                sell_details.append("BOS")

            if vol_ma is not None and current_volume > vol_ma * VOLUME_SPIKE:
                if buy_score > 0:
                    buy_score += POIDS_VOLUME
                    buy_details.append("Vol")
                elif sell_score > 0:
                    sell_score += POIDS_VOLUME
                    sell_details.append("Vol")

            if trend_bull and psar_bull:
                buy_score += POIDS_PSAR
                buy_details.append("PSAR")
            elif trend_bear and psar_bear:
                sell_score += POIDS_PSAR
                sell_details.append("PSAR")

            if atr is not None and atr > ATR_THRESHOLD * 2:
                if buy_score > 0:
                    buy_score += POIDS_ATR
                    buy_details.append("ATR")
                elif sell_score > 0:
                    sell_score += POIDS_ATR
                    sell_details.append("ATR")

            if trend_bull and stoch_k2 < 20 and stoch_d2 < 20:
                buy_score += POIDS_STOCH
                buy_details.append("Stoch")
            elif trend_bear and stoch_k2 > 80 and stoch_d2 > 80:
                sell_score += POIDS_STOCH
                sell_details.append("Stoch")

            if trend_bull and sell_score > 0:
                sell_score = 0
                sell_details = []
            if trend_bear and buy_score > 0:
                buy_score = 0
                buy_details = []

            buy_proba = 50.0
            sell_proba = 50.0
            sl_price_buy = price * (1 - STOP_LOSS)
            tp_price_buy = price * (1 + TAKE_PROFIT)
            sl_price_sell = price * (1 + STOP_LOSS)
            tp_price_sell = price * (1 - TAKE_PROFIT)

            if buy_score >= SCORE_SEUIL:
                buy_proba = calculate_tp_probability(ohlcv, price, sl_price_buy, tp_price_buy)
            if sell_score >= SCORE_SEUIL:
                sell_proba = calculate_tp_probability(ohlcv, price, sl_price_sell, tp_price_sell)

            logging.info(f"📊 DEBUG - buy: {buy_score:.2f} | sell: {sell_score:.2f} | seuil: {SCORE_SEUIL:.2f} | RSI6: {rsi6:.1f} | RSI24: {rsi24:.1f}")

            if buy_score >= SCORE_SEUIL:
                logging.info(f"📊 SIGNAL BUY | score:{buy_score:.2f} | probaTP:{buy_proba:.1f}% | indicateurs: {', '.join(buy_details)}")
            if sell_score >= SCORE_SEUIL:
                logging.info(f"📊 SIGNAL SELL | score:{sell_score:.2f} | probaTP:{sell_proba:.1f}% | indicateurs: {', '.join(sell_details)}") 
                
                        # === GESTION POSITION ===
            if position is not None and entry_price > 0:
                pnl = (price - entry_price) / entry_price if position == 'buy' else (entry_price - price) / entry_price
                pnl *= LEVERAGE

                if pnl <= -STOP_LOSS:
                    msg = f"🔻 SL à {price:.6f} | P&L: {pnl*100:.2f}%"
                    logging.info(msg)
                    send_telegram(msg)
                    close_position(position, qty)
                    log_trade("CLOSE", position, price, entry_score, pnl, "SL", entry_proba_tp)
                    position = None
                    entry_price = 0
                    entry_score = 0.0
                    entry_proba_tp = 0.0
                    continue
                elif pnl >= TAKE_PROFIT:
                    msg = f"🔺 TP à {price:.6f} | P&L: {pnl*100:.2f}%"
                    logging.info(msg)
                    send_telegram(msg)
                    close_position(position, qty)
                    log_trade("CLOSE", position, price, entry_score, pnl, "TP", entry_proba_tp)
                    position = None
                    entry_price = 0
                    entry_score = 0.0
                    entry_proba_tp = 0.0
                    continue

                if position == 'buy' and sell_score > entry_score * 1.5:
                    msg = f"🔄 INVERSION: sell_score ({sell_score:.2f}) > {entry_score:.2f} * 1.5"
                    logging.info(msg)
                    send_telegram(msg)
                    close_position(position, qty)
                    log_trade("CLOSE", position, price, entry_score, pnl, "INVERSION_CLOSE", entry_proba_tp)
                    if create_order('sell', TAILLE_POSITION_BSB):
                        position = 'sell'
                        entry_price = price
                        entry_score = sell_score
                        entry_proba_tp = sell_proba
                        log_trade("OPEN", 'sell', price, sell_score, 0, "INVERSION_OPEN", sell_proba)
                        send_telegram(f"🟢 SELL ouvert à {price:.6f} | Score: {entry_score:.2f} | TP: {tp_price_sell:.6f} | SL: {sl_price_sell:.6f}")
                    continue
                elif position == 'sell' and buy_score > entry_score * 1.5:
                    msg = f"🔄 INVERSION: buy_score ({buy_score:.2f}) > {entry_score:.2f} * 1.5"
                    logging.info(msg)
                    send_telegram(msg)
                    close_position(position, qty)
                    log_trade("CLOSE", position, price, entry_score, pnl, "INVERSION_CLOSE", entry_proba_tp)
                    if create_order('buy', TAILLE_POSITION_BSB):
                        position = 'buy'
                        entry_price = price
                        entry_score = buy_score
                        entry_proba_tp = buy_proba
                        log_trade("OPEN", 'buy', price, buy_score, 0, "INVERSION_OPEN", buy_proba)
                        send_telegram(f"🟢 BUY ouvert à {price:.6f} | Score: {entry_score:.2f} | TP: {tp_price_buy:.6f} | SL: {sl_price_buy:.6f}")
                    continue

                        # === NOUVELLE ENTRÉE ===
            if position is None:
                if buy_score >= SCORE_SEUIL:
                    if create_order('buy', TAILLE_POSITION_BSB):
                        position = 'buy'
                        entry_price = price
                        entry_score = buy_score
                        entry_proba_tp = buy_proba
                        log_trade("OPEN", 'buy', price, buy_score, 0, "NEW", buy_proba)
                        send_telegram(f"🟢 BUY ouvert à {price:.6f} | Score: {entry_score:.2f} | TP: {tp_price_buy:.6f} | SL: {sl_price_buy:.6f}")
                elif sell_score >= SCORE_SEUIL:
                    if create_order('sell', TAILLE_POSITION_BSB):
                        position = 'sell'
                        entry_price = price
                        entry_score = sell_score
                        entry_proba_tp = sell_proba
                        log_trade("OPEN", 'sell', price, sell_score, 0, "NEW", sell_proba)
                        send_telegram(f"🟢 SELL ouvert à {price:.6f} | Score: {entry_score:.2f} | TP: {tp_price_sell:.6f} | SL: {sl_price_sell:.6f}")

            # === VÉRIFICATION DES COMMANDES TELEGRAM ===
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
                resp = requests.get(url, timeout=5).json()
                if resp.get("ok") and resp.get("result"):
                    for update in resp["result"]:
                        if "message" in update and "text" in update["message"]:
                            text = update["message"]["text"]
                            chat_id = update["message"]["chat"]["id"]
                            if text == "/stats" and str(chat_id) == str(TELEGRAM_CHAT_ID):
                                send_stats()
                                update_id = update["update_id"]
                                requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={update_id+1}", timeout=5)
            except:
                pass

            time.sleep(30)

        except Exception as e:
            error_msg = f"❌ Erreur critique: {e}"
            logging.error(error_msg)
            send_telegram(error_msg)
            time.sleep(60)

if __name__ == "__main__":
    bot()
