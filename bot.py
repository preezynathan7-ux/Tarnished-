from pybit.unified_trading import HTTP
import time
import logging
import os
from datetime import datetime

# ============================================================
#  CONFIGURATION
# ============================================================

SYMBOLE = "BSBUSDT"
TIMEFRAME = "15"
LEVERAGE = 10
TAILLE_POSITION_BSB = 200

# --- SL/TP fixes ---
STOP_LOSS = 0.025
TAKE_PROFIT = 0.06

# --- Seuil ---
SCORE_SEUIL = 1.2

# --- Poids égaux ---
POIDS_MACD = 0.6
POIDS_EMA = 0.6
POIDS_RSI = 0.6
POIDS_STOCHRSI = 0.6
POIDS_BOS = 0.6
POIDS_VOLUME = 0.6
POIDS_PSAR = 0.6
POIDS_ATR = 0.6
POIDS_STOCH = 0.6

# --- RSI différencié ---
RSI6_OVERSOLD = 40
RSI24_OVERBOUGHT = 45

# --- Règle de retournement fort ---
RETOURNEMENT_RSI6 = 30
RETOURNEMENT_STOCH = 20
RETOURNEMENT_VOLUME_MA = 10

VOLUME_SPIKE = 1.3
ATR_THRESHOLD = 0.0008

API_KEY = os.getenv("API_KEY") or "yhIWArGAp0JwDLDja2"
API_SECRET = os.getenv("API_SECRET") or "Xlg8fjG557YapL9B6EwHBCtotWkiadnENRtE"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

session = HTTP(testnet=False, demo=True, api_key=API_KEY, api_secret=API_SECRET)

# ============================================================
#  INDICATEURS (inchangés)
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
#  JOURNAL QUOTIDIEN
# ============================================================

def print_daily_summary():
    try:
        with open("journal_trading.txt", "r") as f:
            lines = f.readlines()
        today = datetime.now().strftime("%Y-%m-%d")
        today_trades = [l for l in lines if today in l and "CLOSE" in l]
        total = len(today_trades)
        if total == 0:
            logging.info(f"📊 Aucun trade clôturé aujourd'hui ({today})")
            return
        winning = [l for l in today_trades if "P&L:" in l and float(l.split("P&L:")[1].split()[0]) > 0]
        win_rate = len(winning) / total * 100 if total > 0 else 0
        total_pnl = sum([float(l.split("P&L:")[1].split()[0]) for l in today_trades if "P&L:" in l])
        logging.info(f"📊 RÉSUMÉ DU {today}")
        logging.info(f"   Trades: {total} | Gagnants: {len(winning)} | Perdants: {total - len(winning)}")
        logging.info(f"   Win rate: {win_rate:.1f}% | P&L total: {total_pnl:.2f} USDT")
    except:
        pass

# ============================================================
#  BOUCLE PRINCIPALE
# ============================================================

def bot():
    logging.info("🤖 Bot 'Tarnished Equal' démarré (mode démo)")
    logging.info(f"📊 Symbole: {SYMBOLE} | Levier: {LEVERAGE}x | Position: {TAILLE_POSITION_BSB} BSB")
    logging.info(f"📈 Seuil: {SCORE_SEUIL} | Poids MACD: {POIDS_MACD} (égal aux autres)")
    logging.info(f"🔄 Règle de retournement fort: RSI6 < {RETOURNEMENT_RSI6} + StochRSI < {RETOURNEMENT_STOCH} + Volume > MA{RETOURNEMENT_VOLUME_MA}")

    position = None
    entry_price = 0
    entry_score = 0.0
    last_daily_log = datetime.now().date()

    side, qty, avg = get_position()
    if side is not None:
        position = side
        entry_price = avg
        logging.info(f"🔄 Position reprise: {side.upper()} {qty} BSB à {avg:.6f}")

    while True:
        try:
            today = datetime.now().date()
            if today != last_daily_log:
                print_daily_summary()
                last_daily_log = today

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

            # === RÈGLE DE RETOURNEMENT FORT ===
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

            # 1. MACD (poids égal)
            if macd is not None and signal is not None:
                if macd > signal:
                    buy_score += POIDS_MACD
                    buy_details.append("MACD")
                else:
                    sell_score += POIDS_MACD
                    sell_details.append("MACD")

            # 2. EMA
            if ema50 is not None:
                if price > ema50:
                    buy_score += POIDS_EMA
                    buy_details.append("EMA")
                else:
                    sell_score += POIDS_EMA
                    sell_details.append("EMA")

            # 3. RSI différencié
            if trend_bull and rsi6 < RSI6_OVERSOLD:
                buy_score += POIDS_RSI
                buy_details.append(f"RSI6({rsi6:.1f})")
            elif trend_bear and rsi24 < RSI24_OVERBOUGHT:
                sell_score += POIDS_RSI
                sell_details.append(f"RSI24({rsi24:.1f})")

            # 4. StochRSI
            if trend_bull and stoch_k < 20 and stoch_d < 20:
                buy_score += POIDS_STOCHRSI
                buy_details.append("StochRSI")
            elif trend_bear and stoch_k > 80 and stoch_d > 80:
                sell_score += POIDS_STOCHRSI
                sell_details.append("StochRSI")

            # 5. BOS
            if trend_bull and bos_h:
                buy_score += POIDS_BOS
                buy_details.append("BOS")
            elif trend_bear and bos_b:
                sell_score += POIDS_BOS
                sell_details.append("BOS")

            # 6. Volume
            if vol_ma is not None and current_volume > vol_ma * VOLUME_SPIKE:
                if buy_score > 0:
                    buy_score += POIDS_VOLUME
                    buy_details.append("Vol")
                elif sell_score > 0:
                    sell_score += POIDS_VOLUME
                    sell_details.append("Vol")

            # 7. PSAR
            if trend_bull and psar_bull:
                buy_score += POIDS_PSAR
                buy_details.append("PSAR")
            elif trend_bear and psar_bear:
                sell_score += POIDS_PSAR
                sell_details.append("PSAR")

            # 8. ATR
            if atr is not None and atr > ATR_THRESHOLD * 2:
                if buy_score > 0:
                    buy_score += POIDS_ATR
                    buy_details.append("ATR")
                elif sell_score > 0:
                    sell_score += POIDS_ATR
                    sell_details.append("ATR")

            # 9. Stoch
            if trend_bull and stoch_k2 < 20 and stoch_d2 < 20:
                buy_score += POIDS_STOCH
                buy_details.append("Stoch")
            elif trend_bear and stoch_k2 > 80 and stoch_d2 > 80:
                sell_score += POIDS_STOCH
                sell_details.append("Stoch")

            # === PAS DE PRIORITÉ MACD (plus de blocage) ===
            # MACD a le même poids que les autres, il ne bloque plus

            # === FILTRE DE TENDANCE (toujours actif) ===
            if trend_bull and sell_score > 0:
                sell_score = 0
                sell_details = []
            if trend_bear and buy_score > 0:
                buy_score = 0
                buy_details = []

            # === LOGS ===
            logging.info(f"📊 DEBUG - buy: {buy_score:.2f} | sell: {sell_score:.2f} | seuil: {SCORE_SEUIL:.2f} | RSI6: {rsi6:.1f} | RSI24: {rsi24:.1f}")

            if buy_score >= SCORE_SEUIL:
                logging.info(f"📊 SIGNAL BUY | score:{buy_score:.2f} | indicateurs: {', '.join(buy_details)}")
            if sell_score >= SCORE_SEUIL:
                logging.info(f"📊 SIGNAL SELL | score:{sell_score:.2f} | indicateurs: {', '.join(sell_details)}")

            # === GESTION POSITION ===
            if position is not None and entry_price > 0:
                pnl = (price - entry_price) / entry_price if position == 'buy' else (entry_price - price) / entry_price
                pnl *= LEVERAGE

                if pnl <= -STOP_LOSS:
                    logging.info(f"🔻 SL à {price:.4f} | P&L: {pnl*100:.2f}%")
                    close_position(position, qty)
                    position, entry_price, entry_score = None, 0, 0.0
                    continue
                elif pnl >= TAKE_PROFIT:
                    logging.info(f"🔺 TP à {price:.4f} | P&L: {pnl*100:.2f}%")
                    close_position(position, qty)
                    position, entry_price, entry_score = None, 0, 0.0
                    continue

                if position == 'buy' and sell_score > entry_score * 1.5:
                    logging.info(f"🔄 INVERSION: sell_score ({sell_score:.2f}) > {entry_score:.2f} * 1.5")
                    close_position(position, qty)
                    if create_order('sell', TAILLE_POSITION_BSB):
                        position, entry_price, entry_score = 'sell', price, sell_score
                    continue
                elif position == 'sell' and buy_score > entry_score * 1.5:
                    logging.info(f"🔄 INVERSION: buy_score ({buy_score:.2f}) > {entry_score:.2f} * 1.5")
                    close_position(position, qty)
                    if create_order('buy', TAILLE_POSITION_BSB):
                        position, entry_price, entry_score = 'buy', price, buy_score
                    continue

            # === NOUVELLE ENTRÉE ===
            if position is None:
                if buy_score >= SCORE_SEUIL:
                    if create_order('buy', TAILLE_POSITION_BSB):
                        position, entry_price, entry_score = 'buy', price, buy_score
                elif sell_score >= SCORE_SEUIL:
                    if create_order('sell', TAILLE_POSITION_BSB):
                        position, entry_price, entry_score = 'sell', price, sell_score

            time.sleep(30)

        except Exception as e:
            logging.error(f"❌ Erreur: {e}")
            time.sleep(60)

if __name__ == "__main__":
    bot()
