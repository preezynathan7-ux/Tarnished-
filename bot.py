from pybit.unified_trading import HTTP
import time
import logging
import os 
print("=== DEBUG ===")
print("API_KEY:", os.getenv("API_KEY"))
print("API_SECRET:", os.getenv("API_SECRET")[:5] + "..." if os.getenv("API_SECRET") else "MANQUANTE")
print("==============")

# ============================================================
#  CONFIGURATION (variables d'environnement)
# ============================================================

SYMBOLE = "BSBUSDT"
TAILLE_POSITION_BSB = 200
STOP_LOSS = 0.025
TAKE_PROFIT = 0.08
LEVERAGE = 10

RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
STOCH_PERIOD = 14
STOCH_OVERSOLD = 20
STOCH_OVERBOUGHT = 80
SCORE_SEUIL = 3
EMA_FAST = 50
EMA_SLOW = 200

API_KEY = os.getenv("yhIWArGAp0JwDLDja2")
API_SECRET = os.getenv("Xlg8fjG557YapL9B6EwHBCtotWkiadnENRtE")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

session = HTTP(
    testnet=False,
    demo=True,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

# ============================================================
#  FONCTIONS
# ============================================================

def get_current_price():
    tickers = session.get_tickers(category="linear", symbol=SYMBOLE)
    return float(tickers["result"]["list"][0]["lastPrice"])

def get_ohlcv(limit=100):
    data = session.get_kline(category="linear", symbol=SYMBOLE, interval=15, limit=limit)
    return data["result"]["list"]

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
        logging.info(f"✅ {side.upper()} {qty} BSB")
        return True
    except Exception as e:
        logging.error(f"❌ Erreur ordre : {e}")
        return False

def close_position(side, qty):
    opp = "Buy" if side == "sell" else "Sell"
    return create_order(opp, qty)

# ============================================================
#  INDICATEURS
# ============================================================

def rsi(ohlcv, index, period=RSI_PERIOD):
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

def ema(ohlcv, index, period):
    if index < period:
        return None
    closes = [float(c[3]) for c in ohlcv[:index+1]]
    alpha = 2 / (period + 1)
    result = [closes[0]]
    for v in closes[1:]:
        result.append(alpha * v + (1 - alpha) * result[-1])
    return result[-1]

def macd(ohlcv, index):
    if index < MACD_SLOW + MACD_SIGNAL:
        return None, None
    e12 = ema(ohlcv, index, MACD_FAST)
    e26 = ema(ohlcv, index, MACD_SLOW)
    if e12 is None or e26 is None:
        return None, None
    macd_line = e12 - e26
    signal_line = e12 * 0.15 + e26 * 0.85
    return macd_line, signal_line

def stoch_rsi(ohlcv, index, period=STOCH_PERIOD):
    if index < period + 1:
        return 50, 50
    vals = []
    for i in range(index - period, index + 1):
        vals.append(rsi(ohlcv, i, period))
    if not vals:
        return 50, 50
    mn, mx = min(vals), max(vals)
    if mx == mn:
        return 50, 50
    stoch = (vals[-1] - mn) / (mx - mn)
    return stoch * 100, stoch * 100

def bos(ohlcv, index):
    if index < 5:
        return False, False
    highs = [float(c[1]) for c in ohlcv[index-5:index]]
    lows = [float(c[2]) for c in ohlcv[index-5:index]]
    return float(ohlcv[index][1]) > max(highs), float(ohlcv[index][2]) < min(lows)

def fvg(ohlcv, index):
    if index < 3:
        return False, False
    h2 = float(ohlcv[index-3][1])
    l2 = float(ohlcv[index-3][2])
    h0 = float(ohlcv[index][1])
    l0 = float(ohlcv[index][2])
    return l0 > h2, h0 < l2

def volume_ma(ohlcv, index, period=20):
    if index < period:
        return None
    volumes = [float(c[4]) for c in ohlcv[index-period:index]]
    return sum(volumes) / len(volumes)

# ============================================================
#  BOUCLE PRINCIPALE
# ============================================================

def bot():
    logging.info("🤖 Bot 'Tarnished Demo' démarré")
    logging.info(f"📊 Symbole: {SYMBOLE}")
    logging.info(f"📈 Levier: {LEVERAGE}x | Position: {TAILLE_POSITION_BSB} BSB")
    logging.info(f"🛑 SL: {STOP_LOSS*100}% | 🎯 TP: {TAKE_PROFIT*100}%")
    logging.info(f"📈 Seuil de score: {SCORE_SEUIL}")

    position = None
    entry_price = 0

    while True:
        try:
            ohlcv = get_ohlcv(100)
            if not ohlcv or len(ohlcv) < 30:
                time.sleep(30)
                continue

            price = get_current_price()
            side, qty, avg = get_position()
            if side is not None:
                position = side
                entry_price = avg

            i = len(ohlcv) - 1
            r = rsi(ohlcv, i)
            e50 = ema(ohlcv, i, EMA_FAST)
            e200 = ema(ohlcv, i, EMA_SLOW)
            mac, sig = macd(ohlcv, i)
            sk, sd = stoch_rsi(ohlcv, i)
            bos_h, bos_b = bos(ohlcv, i)
            fvg_h, fvg_b = fvg(ohlcv, i)
            vol_ma_val = volume_ma(ohlcv, i, 20)
            current_volume = float(ohlcv[i][4])
            volume_high = vol_ma_val is not None and current_volume > vol_ma_val * 1.5

            buy_score = 0
            sell_score = 0

            if e50 is not None and e200 is not None:
                trend_bull = e50 > e200
                trend_bear = e50 < e200
            else:
                trend_bull = False
                trend_bear = False

            if trend_bull and r < RSI_OVERSOLD:
                buy_score += 1
            elif trend_bear and r > RSI_OVERBOUGHT:
                sell_score += 1

            if mac is not None and sig is not None:
                if mac > sig:
                    buy_score += 1
                else:
                    sell_score += 1

            if trend_bull and sk < STOCH_OVERSOLD and sd < STOCH_OVERSOLD:
                buy_score += 1
            elif trend_bear and sk > STOCH_OVERBOUGHT and sd > STOCH_OVERBOUGHT:
                sell_score += 1

            if trend_bull and bos_h:
                buy_score += 1
            elif trend_bear and bos_b:
                sell_score += 1

            if trend_bull and fvg_h:
                buy_score += 1
            elif trend_bear and fvg_b:
                sell_score += 1

            if trend_bull and volume_high:
                buy_score += 1
            elif trend_bear and volume_high:
                sell_score += 1

            signal = None
            if trend_bull and buy_score >= SCORE_SEUIL:
                signal = 'buy'
            elif trend_bear and sell_score >= SCORE_SEUIL:
                signal = 'sell'

            if position is not None and entry_price > 0:
                pnl = (price - entry_price) / entry_price if position == 'buy' else (entry_price - price) / entry_price
                pnl *= LEVERAGE

                if pnl <= -STOP_LOSS:
                    logging.info(f"🔻 SL à {price:.4f} | P&L: {pnl*100:.2f}%")
                    close_position(position, qty)
                    position = None
                    entry_price = 0
                elif pnl >= TAKE_PROFIT:
                    logging.info(f"🔺 TP à {price:.4f} | P&L: {pnl*100:.2f}%")
                    close_position(position, qty)
                    position = None
                    entry_price = 0

            if position is None and signal is not None:
                logging.info(f"📊 Signal {signal.upper()} (score {buy_score if signal=='buy' else sell_score})")
                if create_order(signal, TAILLE_POSITION_BSB):
                    position = signal
                    entry_price = price

            time.sleep(30)

        except Exception as e:
            logging.error(f"Erreur : {e}")
            time.sleep(60)

if __name__ == "__main__":
    bot()
