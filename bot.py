from pybit.unified_trading import HTTP
import requests
import time
import logging
import os
from datetime import datetime

# ============================================================
#  CONFIGURATION – EMBER (VERSION FINALE)
# ============================================================

SYMBOLE = "BSBUSDT"
TIMEFRAME = "30"
LIMIT = 100

CAPITAL_INITIAL = 7.0
RISQUE_PAR_TRADE = 0.05
LEVERAGE = 15
STOP_LOSS = 0.035
TAKE_PROFIT = 0.063
COMMISSION = 0.0006

# === INDICATEURS ===
RSI_OVERBOUGHT = 72
RSI_OVERSOLD = 30
VOLUME_SPIKE = 1.3
SCORE_SEUIL = 3
STOCH_OVERSOLD = 10
STOCH_OVERBOUGHT = 90

# === FILTRES ===
ATR_THRESHOLD = 0.0006
FRAIS_BUFFER = 0.002
TRAILING_ACTIVATE_PNL = 0.03 + FRAIS_BUFFER
TRAILING_STEP = 0.4
MAX_LOSS_USDT = 0.35

# === OPTIMISATIONS RETENUES ===
USE_TIME_FILTER = True
USE_DRAWDOWN_PROTECTION = True
USE_PYRAMID = True

# === API BYBIT (DÉMO) ===
API_KEY = "yhIWArGAp0JwDLDja2"       # ← REMPLACE ICI
API_SECRET = "Xlg8fjG557YapL9B6EwHBCtotWkiadnENRtE"    # ← REMPLACE ICI

# === TELEGRAM ===
TELEGRAM_TOKEN = "8878379567:AAECojwAmR2P10PXOJgQdJJtAbwXBPkwoaQ"      # ← REMPLACE ICI
TELEGRAM_CHAT_ID = "7645348359"  # ← REMPLACE ICI

JOURNAL_FILE = "journal_ember.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# === INITIALISATION BYBIT ===
try:
    session = HTTP(testnet=False, demo=True, api_key=API_KEY, api_secret=API_SECRET)
    logging.info("✅ Connexion Bybit démo réussie")
except Exception as e:
    logging.error(f"❌ Échec de connexion Bybit: {e}")
    exit(1)

# ============================================================
#  FONCTIONS
# ============================================================

def get_balance():
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        if resp["result"]["list"]:
            for coin in resp["result"]["list"][0]["coin"]:
                if coin["coin"] == "USDT":
                    return float(coin["walletBalance"])
        return 0.0
    except Exception as e:
        logging.error(f"❌ Erreur solde: {e}")
        return 0.0

def get_current_price():
    tickers = session.get_tickers(category="linear", symbol=SYMBOLE)
    return float(tickers["result"]["list"][0]["lastPrice"])

def get_ohlcv(limit=100):
    data = session.get_kline(category="linear", symbol=SYMBOLE, interval=15, limit=limit)
    return data["result"]["list"]

def get_position():
    try:
        pos = session.get_positions(category="linear", symbol=SYMBOLE)
        if pos["result"]["list"]:
            p = pos["result"]["list"][0]
            if float(p["size"]) != 0:
                return p["side"].lower(), float(p["size"]), float(p["avgPrice"])
        return None, 0, 0
    except Exception as e:
        logging.error(f"❌ Erreur get_position: {e}")
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

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logging.error(f"❌ Erreur envoi Telegram: {e}")

def log_trade(action, side, price, score, pnl=0, reason=""):
    try:
        with open(JOURNAL_FILE, "a") as f:
            f.write(f"{datetime.now().isoformat()} | {action} | {side} | {price:.6f} | score:{score:.2f} | P&L:{pnl:.2f} | {reason}\n")
    except:
        pass

def send_stats():
    """Envoie les statistiques du jour sur Telegram"""
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
        msg = (f"🔥 EMBER – RÉSUMÉ DU {today}\n"
               f"   Trades: {total} | Gagnants: {len(winning)} | Perdants: {total - len(winning)}\n"
               f"   Win rate: {win_rate:.1f}% | P&L total: {total_pnl:.2f} USDT")
        send_telegram(msg)
    except Exception as e:
        send_telegram(f"❌ Erreur lors de la lecture du journal: {e}")

def check_telegram_commands():
    """Vérifie les commandes Telegram (/stats)"""
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
                        # Marquer l'update comme lu
                        update_id = update["update_id"]
                        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={update_id+1}", timeout=5)
    except Exception as e:
        logging.error(f"❌ Erreur check_telegram_commands: {e}")

# === INDICATEURS ===
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
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

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
    ema12 = get_ema(ohlcv, index, 12)
    ema26 = get_ema(ohlcv, index, 26)
    if ema12 is None or ema26 is None:
        return None, None
    macd = ema12 - ema26
    signal = ema12 * 0.15 + ema26 * 0.85
    return macd, signal

def get_stoch_rsi(ohlcv, index, period=14):
    if index < period + 1:
        return 50, 50
    rsi_values = []
    for i in range(index - period, index + 1):
        rsi_values.append(get_rsi(ohlcv, i, period))
    if not rsi_values:
        return 50, 50
    min_rsi = min(rsi_values)
    max_rsi = max(rsi_values)
    if max_rsi == min_rsi:
        return 50, 50
    stoch = (rsi_values[-1] - min_rsi) / (max_rsi - min_rsi)
    return stoch * 100, stoch * 100

def detect_bos(ohlcv, index):
    if index < 5:
        return False, False
    highs = [float(c[1]) for c in ohlcv[index-5:index]]
    lows = [float(c[2]) for c in ohlcv[index-5:index]]
    current_high = float(ohlcv[index][1])
    current_low = float(ohlcv[index][2])
    return current_high > max(highs), current_low < min(lows)

def detect_choch(ohlcv, index):
    if index < 6:
        return False, False
    highs = [float(c[1]) for c in ohlcv[index-6:index]]
    lows = [float(c[2]) for c in ohlcv[index-6:index]]
    closes = [float(c[3]) for c in ohlcv[:index+1]]
    current_close = closes[-1]
    return current_close > max(highs), current_close < min(lows)

def detect_fvg(ohlcv, index):
    if index < 3:
        return False, False
    high_2 = float(ohlcv[index-3][1])
    low_2 = float(ohlcv[index-3][2])
    high_0 = float(ohlcv[index][1])
    low_0 = float(ohlcv[index][2])
    return low_0 > high_2, high_0 < low_2

def get_volume_ma(ohlcv, index, period=20):
    if index < period:
        return None
    volumes = [float(c[4]) for c in ohlcv[index-period:index]]
    return sum(volumes) / len(volumes)

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

def time_filter(timestamp):
    dt = datetime.fromtimestamp(int(timestamp)/1000)
    hour = dt.hour + 1
    if hour >= 24:
        hour -= 24
    return 8 <= hour <= 22

# ============================================================
#  BOUCLE PRINCIPALE – EMBER (LIVE)
# ============================================================

def bot():
    logging.info("🔥 Bot 'EMBER' – LIVE (démo) – Version finale (trailing % fixe)")
    send_telegram("🔥 Bot 'EMBER' – LIVE (démo) – Version finale")

    position = None
    entry_price = 0
    entry_score = 0.0
    trailing_sl_price = 0
    qty_position = 0
    capital_peak = 0.0
    pyramide_active = False

    side, qty, avg = get_position()
    if side is not None:
        position = side
        entry_price = avg
        qty_position = qty
        capital_peak = get_balance()
        logging.info(f"🔄 Position reprise: {side.upper()} {qty} BSB à {avg:.6f}")
        send_telegram(f"🔄 Position reprise: {side.upper()} {qty} BSB à {avg:.6f}")

    while True:
        try:
            # Vérification des commandes Telegram
            check_telegram_commands()

            capital = get_balance()
            if capital <= 0:
                logging.warning("⚠️ Solde à zéro, on attend...")
                time.sleep(30)
                continue

            if capital_peak == 0:
                capital_peak = capital

            drawdown_protection = False
            if USE_DRAWDOWN_PROTECTION and capital < capital_peak * 0.9:
                drawdown_protection = True
                logging.info(f"🛡️ Drawdown > 10% – réduction de taille active")
                send_telegram(f"🛡️ Drawdown > 10% – réduction de taille active")

            ohlcv = get_ohlcv(100)
            if not ohlcv or len(ohlcv) < 30:
                time.sleep(30)
                continue

            price = get_current_price()
            current_time = int(ohlcv[-1][0])

            if USE_TIME_FILTER and not time_filter(current_time):
                time.sleep(30)
                continue

            side, qty, avg = get_position()
            if side is not None:
                if position is None:
                    position = side
                    entry_price = avg
                    qty_position = qty
                    logging.info(f"🔄 Position reprise: {side.upper()} {qty} BSB à {avg:.6f}")
                    send_telegram(f"🔄 Position reprise: {side.upper()} {qty} BSB à {avg:.6f}")
            else:
                if position is not None:
                    logging.info("🔒 Position fermée")
                    position = None
                    entry_price = 0
                    entry_score = 0.0
                    trailing_sl_price = 0
                    qty_position = 0
                    pyramide_active = False

            i = len(ohlcv) - 1

            rsi = get_rsi(ohlcv, i)
            ema50 = get_ema(ohlcv, i, 50)
            ema200 = get_ema(ohlcv, i, 200)
            macd, signal = get_macd(ohlcv, i)
            stoch_k, stoch_d = get_stoch_rsi(ohlcv, i)
            vol_ma = get_volume_ma(ohlcv, i, 20)
            bos_h, bos_b = detect_bos(ohlcv, i)
            choch_h, choch_b = detect_choch(ohlcv, i)
            fvg_h, fvg_b = detect_fvg(ohlcv, i)
            atr = get_atr(ohlcv, i)

            # --- GESTION POSITION ---
            if position is not None and entry_price > 0:
                pnl = (price - entry_price) / entry_price if position == 'buy' else (entry_price - price) / entry_price
                pnl *= LEVERAGE

                sl_price = entry_price * (1 - STOP_LOSS) if position == 'buy' else entry_price * (1 + STOP_LOSS)
                tp_price = entry_price * (1 + TAKE_PROFIT) if position == 'buy' else entry_price * (1 - TAKE_PROFIT)

                # Trailing % fixe
                if trailing_sl_price == 0 and pnl >= TRAILING_ACTIVATE_PNL:
                    if position == 'buy':
                        trailing_sl_price = entry_price * (1 + FRAIS_BUFFER)
                    else:
                        trailing_sl_price = entry_price * (1 - FRAIS_BUFFER)
                    logging.info(f"🔒 Trailing % activé à {trailing_sl_price:.4f}")
                    send_telegram(f"🔒 Trailing % activé à {trailing_sl_price:.4f}")

                if trailing_sl_price != 0:
                    if position == 'buy' and price > entry_price:
                        new_sl = entry_price + (price - entry_price) * TRAILING_STEP
                        if new_sl > trailing_sl_price:
                            trailing_sl_price = new_sl
                    elif position == 'sell' and price < entry_price:
                        new_sl = entry_price - (entry_price - price) * TRAILING_STEP
                        if new_sl < trailing_sl_price:
                            trailing_sl_price = new_sl

                reason = None
                if position == 'buy':
                    if price <= sl_price:
                        reason = 'SL'
                    elif price >= tp_price:
                        reason = 'TP'
                    elif trailing_sl_price != 0 and price <= trailing_sl_price:
                        reason = 'TRAILING'
                else:
                    if price >= sl_price:
                        reason = 'SL'
                    elif price <= tp_price:
                        reason = 'TP'
                    elif trailing_sl_price != 0 and price >= trailing_sl_price:
                        reason = 'TRAILING'

                if reason:
                    valeur_position = qty_position * entry_price / LEVERAGE
                    pnl_usdt = pnl * valeur_position
                    pnl_usdt_net = pnl_usdt - COMMISSION * valeur_position

                    if pnl_usdt_net < -MAX_LOSS_USDT:
                        pnl_usdt_net = -MAX_LOSS_USDT
                        reason = f"{reason} (limité)"

                    msg = f"🔻 {reason} à {price:.6f} | P&L: {pnl_usdt_net:.2f} USDT ({pnl*100:.2f}%)"
                    logging.info(msg)
                    send_telegram(msg)
                    close_position(position, qty_position)
                    log_trade("CLOSE", position, price, entry_score, pnl_usdt_net, reason)
                    position = None
                    entry_price = 0
                    entry_score = 0.0
                    trailing_sl_price = 0
                    qty_position = 0
                    pyramide_active = False
                    continue

            # --- FILTRE ATR ---
            if atr is not None and atr < ATR_THRESHOLD:
                time.sleep(30)
                continue

            # --- TENDANCE ---
            if ema50 is not None and ema200 is not None:
                trend_bull = ema50 > ema200
                trend_bear = ema50 < ema200
            else:
                trend_bull = False
                trend_bear = False

            # --- SCORE ---
            buy_score = 0.0
            sell_score = 0.0
            buy_details = []
            sell_details = []

            if rsi < RSI_OVERSOLD:
                buy_score += 1.0
                buy_details.append("RSI")
            elif rsi > RSI_OVERBOUGHT:
                sell_score += 1.0
                sell_details.append("RSI")

            if ema50 is not None:
                if price > ema50:
                    buy_score += 1.0
                    buy_details.append("EMA")
                else:
                    sell_score += 1.0
                    sell_details.append("EMA")

            if macd is not None and signal is not None:
                if macd > signal:
                    buy_score += 1.0
                    buy_details.append("MACD")
                else:
                    sell_score += 1.0
                    sell_details.append("MACD")

            if stoch_k < STOCH_OVERSOLD and stoch_d < STOCH_OVERSOLD:
                buy_score += 1.0
                buy_details.append("StochRSI")
            elif stoch_k > STOCH_OVERBOUGHT and stoch_d > STOCH_OVERBOUGHT:
                sell_score += 1.0
                sell_details.append("StochRSI")

            if vol_ma is not None and float(ohlcv[i][4]) > vol_ma * VOLUME_SPIKE:
                if buy_score > 0:
                    buy_score += 1.0
                    buy_details.append("Vol")
                elif sell_score > 0:
                    sell_score += 1.0
                    sell_details.append("Vol")

            if bos_h or choch_h:
                buy_score += 1.0
                buy_details.append("BOS/CHoCH")
            if bos_b or choch_b:
                sell_score += 1.0
                sell_details.append("BOS/CHoCH")

            if fvg_h:
                buy_score += 1.0
                buy_details.append("FVG")
            if fvg_b:
                sell_score += 1.0
                sell_details.append("FVG")

            if trend_bull and sell_score > 0:
                sell_score = 0
                sell_details = []
            if trend_bear and buy_score > 0:
                buy_score = 0
                buy_details = []

            signal_side = None
            if buy_score >= SCORE_SEUIL:
                signal_side = 'buy'
                signal_score = buy_score
                signal_details = buy_details
            elif sell_score >= SCORE_SEUIL:
                signal_side = 'sell'
                signal_score = sell_score
                signal_details = sell_details

            # ============================================================
#  NOUVELLE ENTRÉE (POSITION IS NONE) + BOUCLE PRINCIPALE
# ============================================================

            # --- PYRAMIDE (si position déjà ouverte) ---
            if USE_PYRAMID and position is not None and not pyramide_active:
                pnl_actuel = (price - entry_price) / entry_price if position == 'buy' else (entry_price - price) / entry_price
                if pnl_actuel * 100 >= 3:
                    risque = RISQUE_PAR_TRADE * 0.5 if drawdown_protection else RISQUE_PAR_TRADE
                    risque_usdt = capital * risque
                    distance_sl = price * STOP_LOSS
                    qty_add = risque_usdt / distance_sl
                    qty_add = qty_add / LEVERAGE
                    qty_add = max(1, int(qty_add * 1.1) // 2)
                    if qty_add > 0:
                        if create_order(position, qty_add):
                            qty_position += qty_add
                            pyramide_active = True
                            logging.info(f"🔺 Pyramide ajoutée: +{qty_add} BSB à {price:.4f}")
                            send_telegram(f"🔺 Pyramide ajoutée: +{qty_add} BSB à {price:.4f}")

            # --- NOUVELLE ENTRÉE (position is None) ---
            if position is None and signal_side is not None:
                risque = RISQUE_PAR_TRADE * 0.5 if drawdown_protection else RISQUE_PAR_TRADE
                risque_usdt = capital * risque
                distance_sl = price * STOP_LOSS
                qty = risque_usdt / distance_sl
                qty = qty / LEVERAGE
                qty = max(1, int(qty * 1.1))

                if create_order(signal_side, qty):
                    position = signal_side
                    entry_price = price
                    entry_score = signal_score
                    qty_position = qty
                    trailing_sl_price = 0
                    pyramide_active = False
                    log_trade("OPEN", signal_side, price, signal_score, 0, "NEW")
                    msg = f"🔥 {signal_side.upper()} EMBER – ouvert à {price:.6f} | Score: {entry_score:.2f} | Qty: {qty} BSB | Capital: {capital:.2f} USDT | indicateurs: {', '.join(signal_details)}"
                    logging.info(msg)
                    send_telegram(msg)

            time.sleep(30)

        except Exception as e:
            error_msg = f"❌ Erreur critique (EMBER): {e}"
            logging.error(error_msg)
            send_telegram(error_msg)
            time.sleep(60)

if __name__ == "__main__":
    bot()
        
