from pybit.unified_trading import HTTP
import requests
import time
import logging
import os
import json
from datetime import datetime
import math

# ============================================================
#  CONFIGURATION
# ============================================================

SYMBOLE = "BSBUSDT"
TIMEFRAME = "15"
LEVERAGE = 10
TAILLE_POSITION_BSB = 200
STOP_LOSS = 0.025
TAKE_PROFIT = 0.06
SCORE_SEUIL_BASE = 1.5
INVERSION_FACTOR = 1.5
VOLATILITY_PERIOD = 20

API_KEY = os.getenv("API_KEY") or "yhIWArGAp0JwDLDja2"
API_SECRET = os.getenv("API_SECRET") or "Xlg8fjG557YapL9B6EwHBCtotWkiadnENRtE"

POIDS_FILE = "poids_config.json"
JOURNAL_FILE = "journal_trading.txt"

RSI_OVERBOUGHT = 72
RSI_OVERSOLD = 30
VOLUME_SPIKE = 1.3

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# === PROXY (Singapour) ===
proxies = {
    "http": "http://51.15.1.1:3128",   # Remplacer par une IP valide
    "https": "http://51.15.1.1:3128",
}

session = HTTP(
    testnet=False,
    demo=True,
    api_key=API_KEY,
    api_secret=API_SECRET,
    proxies=proxies,
)

# ============================================================
#  JOURNAL ET LOGS
# ============================================================

def log_trade(action, side, price, score, pnl=0, reason="", proba_tp=0):
    try:
        with open(JOURNAL_FILE, "a") as f:
            f.write(f"{datetime.now().isoformat()} | {action} | {side} | {price:.6f} | score:{score:.2f} | P&L:{pnl:.2f} | probaTP:{proba_tp:.1f}% | {reason}\n")
    except:
        pass

def log_signal(side, score, price, proba_tp, details=""):
    logging.info(f"📊 SIGNAL {side.upper()} | score:{score:.2f} | prix:{price:.4f} | probaTP:{proba_tp:.1f}% | {details}")

# ============================================================
#  PROBABILITÉ TP
# ============================================================

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

# ============================================================
#  MACRO
# ============================================================

def get_btc_trend():
    try:
        url = "https://api.bybit.com/v5/market/kline"
        params = {"category": "linear", "symbol": "BTCUSDT", "interval": "60", "limit": 24}
        resp = requests.get(url, params=params)
        data = resp.json()
        if data["retCode"] == 0:
            closes = [float(c[3]) for c in data["result"]["list"]]
            ema50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else closes[-1]
            ema200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else closes[-1]
            return {"bull": ema50 > ema200}
    except:
        pass
    return {"bull": True}

def get_fear_greed():
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1")
        data = resp.json()
        return int(data["data"][0]["value"])
    except:
        return 50

def get_market_cap(symbol="BSB"):
    try:
        if symbol == "BSB":
            return 150_000_000
        return 0
    except:
        return 0

# ============================================================
#  POIDS
# ============================================================

def load_poids():
    default = {
        "macd": 1.0,
        "ema": 1.0,
        "rsi": 0.5,
        "stoch": 0.5,
        "bos": 0.5,
        "volume": 0.5,
        "score_seuil": SCORE_SEUIL_BASE,
        "last_update": datetime.now().isoformat()
    }
    try:
        with open(POIDS_FILE, "r") as f:
            poids = json.load(f)
            if "score_seuil" not in poids:
                poids["score_seuil"] = SCORE_SEUIL_BASE
            return poids
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_poids(poids):
    try:
        with open(POIDS_FILE, "w") as f:
            json.dump(poids, f, indent=2)
    except:
        pass

def optimize_poids(btc_trend, fear_greed, market_cap):
    poids = load_poids()
    if btc_trend["bull"]:
        poids["macd"] = 1.2
        poids["ema"] = 1.2
        poids["rsi"] = 0.6
        poids["stoch"] = 0.4
    else:
        poids["macd"] = 1.2
        poids["ema"] = 1.2
        poids["rsi"] = 0.6
        poids["stoch"] = 0.4
    if fear_greed < 20 or fear_greed > 80:
        poids["rsi"] = 0.8
        poids["stoch"] = 0.8
    if market_cap < 100_000_000:
        poids["volume"] = 0.8
    poids["score_seuil"] = SCORE_SEUIL_BASE
    poids["last_update"] = datetime.now().isoformat()
    save_poids(poids)
    return poids

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

def detect_bos(ohlcv, index):
    if index < 5:
        return False, False
    highs = [float(c[1]) for c in ohlcv[index-5:index]]
    lows = [float(c[2]) for c in ohlcv[index-5:index]]
    return float(ohlcv[index][1]) > max(highs), float(ohlcv[index][2]) < min(lows)

def detect_choch(ohlcv, index):
    if index < 6:
        return False, False
    highs = [float(c[1]) for c in ohlcv[index-6:index]]
    lows = [float(c[2]) for c in ohlcv[index-6:index]]
    closes = [float(c[3]) for c in ohlcv[:index+1]]
    return closes[-1] > max(highs), closes[-1] < min(lows)

def get_volume_ma(ohlcv, index, period=20):
    if index < period:
        return None
    volumes = [float(c[4]) for c in ohlcv[index-period:index]]
    return sum(volumes) / len(volumes)

# ============================================================
#  ORDRES
# ============================================================

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
#  BOUCLE PRINCIPALE
# ============================================================

def bot():
    logging.info("🤖 Bot 'Tarnished V2' démarré (mode démo)")
    logging.info(f"📊 Symbole: {SYMBOLE} | Levier: {LEVERAGE}x | Position: {TAILLE_POSITION_BSB} BSB")
    logging.info(f"📈 Seuil de score: {SCORE_SEUIL_BASE}")

    poids = load_poids()
    last_optimization = datetime.now()

    position = None
    entry_price = 0
    entry_score = 0.0
    entry_proba_tp = 0.0

    side, qty, avg = get_position()
    if side is not None:
        position = side
        entry_price = avg
        logging.info(f"🔄 Position existante reprise: {side.upper()} {qty} BSB à {avg:.6f}")

    while True:
        try:
            now = datetime.now()

            if (now - last_optimization).seconds > 86400:
                btc_trend = get_btc_trend()
                fear_greed = get_fear_greed()
                market_cap = get_market_cap("BSB")
                poids = optimize_poids(btc_trend, fear_greed, market_cap)
                last_optimization = now

            ohlcv = get_ohlcv(100)
            if not ohlcv or len(ohlcv) < 30:
                time.sleep(30)
                continue

            price = get_current_price()
            side, qty, avg = get_position()
            if side is not None:
                if position is None:
                    position = side
                    entry_price = avg
                    logging.info(f"🔄 Position reprise: {side.upper()} {qty} BSB à {avg:.6f}")
            else:
                if position is not None:
                    logging.info("🔒 Position fermée")
                    position = None
                    entry_price = 0
                    entry_score = 0.0
                    entry_proba_tp = 0.0

            i = len(ohlcv) - 1
            rsi = get_rsi(ohlcv, i)
            ema50 = get_ema(ohlcv, i, 50)
            ema200 = get_ema(ohlcv, i, 200)
            macd, signal = get_macd(ohlcv, i)
            stoch_k, stoch_d = get_stoch_rsi(ohlcv, i)
            vol_ma = get_volume_ma(ohlcv, i, 20)
            bos_h, bos_b = detect_bos(ohlcv, i)
            choch_h, choch_b = detect_choch(ohlcv, i)
            current_volume = float(ohlcv[i][4])

            if ema50 is not None and ema200 is not None:
                trend_bull = ema50 > ema200
                trend_bear = ema50 < ema200
            else:
                trend_bull = False
                trend_bear = False

            buy_score = 0.0
            sell_score = 0.0
            buy_details = []
            sell_details = []

            if macd is not None and signal is not None:
                if macd > signal:
                    buy_score += poids["macd"]
                    buy_details.append("MACD")
                else:
                    sell_score += poids["macd"]
                    sell_details.append("MACD")

            if ema50 is not None:
                if price > ema50:
                    buy_score += poids["ema"]
                    buy_details.append("EMA")
                else:
                    sell_score += poids["ema"]
                    sell_details.append("EMA")

            if rsi < RSI_OVERSOLD:
                buy_score += poids["rsi"]
                buy_details.append("RSI")
            elif rsi > RSI_OVERBOUGHT:
                sell_score += poids["rsi"]
                sell_details.append("RSI")

            if stoch_k < 20 and stoch_d < 20:
                buy_score += poids["stoch"]
                buy_details.append("Stoch")
            elif stoch_k > 80 and stoch_d > 80:
                sell_score += poids["stoch"]
                sell_details.append("Stoch")

            if bos_h or choch_h:
                buy_score += poids["bos"]
                buy_details.append("BOS")
            if bos_b or choch_b:
                sell_score += poids["bos"]
                sell_details.append("BOS")

            if vol_ma is not None and current_volume > vol_ma * VOLUME_SPIKE:
                if buy_score > 0:
                    buy_score += poids["volume"]
                    buy_details.append("Vol")
                elif sell_score > 0:
                    sell_score += poids["volume"]
                    sell_details.append("Vol")

            if macd is not None and signal is not None:
                if macd > signal and sell_score > 0:
                    sell_score = 0
                    sell_details = []
                if macd < signal and buy_score > 0:
                    buy_score = 0
                    buy_details = []

            if trend_bull and sell_score > 0:
                sell_score = 0
                sell_details = []
            if trend_bear and buy_score > 0:
                buy_score = 0
                buy_details = []

            # === LOGS DE DEBUG ===
            logging.info(f"📊 DEBUG - buy_score: {buy_score:.2f} | sell_score: {sell_score:.2f} | seuil: {poids['score_seuil']:.2f} | prix: {price:.4f} | RSI: {rsi:.2f}")

            buy_proba = 50.0
            sell_proba = 50.0
            sl_price_buy = price * (1 - STOP_LOSS)
            tp_price_buy = price * (1 + TAKE_PROFIT)
            sl_price_sell = price * (1 + STOP_LOSS)
            tp_price_sell = price * (1 - TAKE_PROFIT)

            if buy_score >= poids["score_seuil"]:
                buy_proba = calculate_tp_probability(ohlcv, price, sl_price_buy, tp_price_buy)
            if sell_score >= poids["score_seuil"]:
                sell_proba = calculate_tp_probability(ohlcv, price, sl_price_sell, tp_price_sell)

            if buy_score >= poids["score_seuil"]:
                log_signal("BUY", buy_score, price, buy_proba, f"indicateurs: {', '.join(buy_details)}")
            if sell_score >= poids["score_seuil"]:
                log_signal("SELL", sell_score, price, sell_proba, f"indicateurs: {', '.join(sell_details)}")

            if position is not None and entry_price > 0:
                pnl = (price - entry_price) / entry_price if position == 'buy' else (entry_price - price) / entry_price
                pnl *= LEVERAGE

                if pnl <= -STOP_LOSS:
                    logging.info(f"🔻 SL à {price:.4f} | P&L: {pnl*100:.2f}%")
                    close_position(position, qty)
                    log_trade("CLOSE", position, price, entry_score, pnl, "SL", entry_proba_tp)
                    position, entry_price, entry_score, entry_proba_tp = None, 0, 0.0, 0.0
                    continue
                elif pnl >= TAKE_PROFIT:
                    logging.info(f"🔺 TP à {price:.4f} | P&L: {pnl*100:.2f}%")
                    close_position(position, qty)
                    log_trade("CLOSE", position, price, entry_score, pnl, "TP", entry_proba_tp)
                    position, entry_price, entry_score, entry_proba_tp = None, 0, 0.0, 0.0
                    continue

                if position == 'buy' and sell_score > entry_score * INVERSION_FACTOR:
                    logging.info(f"🔄 INVERSION DÉTECTÉE: sell_score ({sell_score:.2f}) > {entry_score:.2f} * {INVERSION_FACTOR}")
                    close_position(position, qty)
                    log_trade("CLOSE", position, price, entry_score, pnl, "INVERSION_CLOSE", entry_proba_tp)
                    if create_order('sell', TAILLE_POSITION_BSB):
                        position, entry_price, entry_score, entry_proba_tp = 'sell', price, sell_score, sell_proba
                        log_trade("OPEN", 'sell', price, sell_score, 0, "INVERSION_OPEN", sell_proba)
                    continue
                elif position == 'sell' and buy_score > entry_score * INVERSION_FACTOR:
                    logging.info(f"🔄 INVERSION DÉTECTÉE: buy_score ({buy_score:.2f}) > {entry_score:.2f} * {INVERSION_FACTOR}")
                    close_position(position, qty)
                    log_trade("CLOSE", position, price, entry_score, pnl, "INVERSION_CLOSE", entry_proba_tp)
                    if create_order('buy', TAILLE_POSITION_BSB):
                        position, entry_price, entry_score, entry_proba_tp = 'buy', price, buy_score, buy_proba
                        log_trade("OPEN", 'buy', price, buy_score, 0, "INVERSION_OPEN", buy_proba)
                    continue

            if position is None:
                if buy_score >= poids["score_seuil"]:
                    if create_order('buy', TAILLE_POSITION_BSB):
                        position, entry_price, entry_score, entry_proba_tp = 'buy', price, buy_score, buy_proba
                        log_trade("OPEN", 'buy', price, buy_score, 0, "NEW", buy_proba)
                elif sell_score >= poids["score_seuil"]:
                    if create_order('sell', TAILLE_POSITION_BSB):
                        position, entry_price, entry_score, entry_proba_tp = 'sell', price, sell_score, sell_proba
                        log_trade("OPEN", 'sell', price, sell_score, 0, "NEW", sell_proba)

            time.sleep(30)

        except Exception as e:
            logging.error(f"❌ Erreur: {e}")
            time.sleep(60)

if __name__ == "__main__":
    bot()
