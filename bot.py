# ============================================================
# NEXUS Trading Bot v3.2
# Bybit Demo | BSBUSDT
# Fixes vs v3 :
#  - ATR filter réellement appliqué (relatif au prix, pas absolu)
#  - Bougie en cours de formation exclue du calcul (backtest ne voit
#    jamais de bougie non fermée, le live devait faire pareil)
#  - Tendance calculée en local 30m (EMA20/50/200) au lieu du biais D1
#    -> aligne exactement la logique live sur celle du backtest
#  - Trailing stop : plus de crash silencieux au redémarrage
#  - Volume : ne boost plus les deux côtés en même temois
#  - Validation qty minimum avant envoi d'ordre
#  - Commandes Telegram /pause /resume /pos /help
#  - get_closed_pnl vérifié + retry (fix du bug de duplication)
# ============================================================

import time
import logging
import math
import os
from datetime import datetime, date
from pybit.unified_trading import HTTP
import requests

# ============================================================
#  ZONE DE CONFIGURATION — MODIFIE TOUT ICI
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

# --- Score de décision ---
SCORE_MIN = 3.8
SCORE_MIN_CONTRE = 5.3

# --- RSI ---
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 80
RSI_POINTS = 1.5

# --- MACD ---
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MACD_POINTS = 1.8

# --- EMA (structure de tendance, identique au backtest) ---
EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 200
EMA_POINTS = 2.1

# --- Stochastic RSI ---
STOCH_PERIOD = 14
STOCH_SMOOTH = 3
STOCH_OVERSOLD = 10
STOCH_OVERBOUGHT = 90
STOCH_POINTS = 1.4

# --- Volume ---
VOLUME_MA_PERIOD = 20
VOLUME_SPIKE_MULT = 1.5
VOLUME_POINTS = 0.8

# --- BOS Court Terme / Long Terme ---
ST_BOS_LOOKBACK = 5
LT_BOS_LOOKBACK = 25
ST_BOS_POINTS = 0.6
LT_BOS_POINTS = 1.2

# --- Pullback ---
PULLBACK_LOOKBACK = 10
PULLBACK_THRESHOLD = 0.30
PULLBACK_POINTS = 0.8

# --- Stop Loss / Take Profit ---
SL_ATR_MULT = 2.7
RR_RATIO_DEFAULT = 2.0
RR_RATIO_ST = 1.6
RR_RATIO_LT = 3.0

# --- Trailing stop natif Bybit ---
TRAILING_ACTIVATE_PCT = 0.030   # valeur retenue après le backtest comparatif
TRAILING_ATR_MULT = 2.8

# --- Filtre volatilité (ATR relatif au prix, marche sur toute paire) ---
ATR_PCT_MIN = 0.0008
ATR_PCT_MAX = 0.02

# --- Taille minimale d'ordre (à vérifier sur Bybit pour BSBUSDT) ---
MIN_QTY = 1
QTY_STEP = 1

# --- Rythme du bot ---
HEARTBEAT_INTERVAL_SEC = 4 * 3600
COOLDOWN_AFTER_TRADE_SEC = 18 * 60
LOOP_SLEEP_SEC = 40

# --- Analyse multi-timeframe (1H / 4H) ---
HTF_TIMEFRAMES = [("60", "1H"), ("240", "4H")]
HTF_LOOKBACK = 30          # bougies pour définir la zone support/résistance
HTF_TOL_ATR_MULT = 1.2     # tolérance de proximité = 1.2x l'ATR de la TF

# --- Confirmation sur bougie suivante (désactivé par défaut, à valider en backtest) ---
CONFIRM_NEXT_CANDLE = False

JOURNAL_FILE = "journal_nexus.txt"

# ============================================================
#  INIT
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")
session = HTTP(testnet=False, demo=True, api_key=API_KEY, api_secret=API_SECRET)

daily_start_capital = 0.0
last_reset_date = date.today()
last_trade_time = 0
last_heartbeat = 0
tracked_position = None
bot_paused = False
pending_confirmation = None   # {"side":..., "candle_time":...} si CONFIRM_NEXT_CANDLE actif

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
                            f"(PnL: {real_pos['unrealised_pnl']:+.2f} USDT)\n")

        pause_line = "\n⏸️ Bot en pause (aucune nouvelle entrée)" if bot_paused else ""

        if not os.path.exists(JOURNAL_FILE):
            tg(f"📊 Aucun trade clôturé enregistré.{status_line}{pause_line}")
            return

        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

        today = datetime.now().strftime("%Y-%m-%d")
        today_closed = [l for l in lines if today in l and "CLOSE" in l]

        if not today_closed:
            tg(f"📊 Aucun trade clôturé aujourd'hui ({today}).{status_line}{pause_line}")
            return

        pnls = []
        for l in today_closed:
            try:
                pnls.append(float(l.split("P&L:")[1].split()[0]))
            except Exception:
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
            f"{status_line}{pause_line}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        tg(msg)
    except Exception as e:
        tg(f"❌ Erreur stats : {e}")

def flush_pending_updates():
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset=-1"
        resp = requests.get(url, timeout=10).json()
        if resp.get("ok") and resp.get("result"):
            last_id = resp["result"][-1]["update_id"]
            requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_id + 1}",
                timeout=10
            )
            logging.info(f"Backlog Telegram vidé (jusqu'à update_id {last_id})")
        else:
            logging.info("Aucun backlog Telegram à vider")
    except Exception as e:
        logging.error(f"flush_pending_updates error: {e}")

def check_telegram_commands():
    global bot_paused
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        resp = requests.get(url, timeout=5).json()
        if not resp.get("ok"):
            logging.error(f"Telegram getUpdates KO: {resp.get('description', resp)}")
            return
        if not resp.get("result"):
            return

        max_update_id = None
        for update in resp["result"]:
            max_update_id = update["update_id"]
            if "message" in update and "text" in update["message"]:
                text = update["message"]["text"].strip()
                chat_id = str(update["message"]["chat"]["id"])
                if chat_id != str(TELEGRAM_CHAT_ID):
                    continue
                if text == "/stats" or text == "/pos":
                    send_stats()
                elif text == "/pause":
                    bot_paused = True
                    tg("⏸️ <b>Bot mis en pause.</b> Aucune nouvelle entrée ne sera prise "
                       "(les positions déjà ouvertes continuent d'être gérées).\nTape /resume pour reprendre.")
                elif text == "/resume":
                    bot_paused = False
                    tg("▶️ <b>Bot repris.</b> En attente de signal...")
                elif text == "/help":
                    tg("📋 <b>Commandes disponibles :</b>\n"
                       "/stats — Résumé du jour\n"
                       "/pos — Position en cours\n"
                       "/pause — Suspendre les nouvelles entrées\n"
                       "/resume — Reprendre\n"
                       "/help — Cette aide")

        if max_update_id is not None:
            requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={max_update_id + 1}",
                timeout=5
            )
    except Exception as e:
        logging.error(f"Telegram commands error: {e}")

# ============================================================
#  BYBIT
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
                    "side": p["side"], "size": size,
                    "entry": float(p["avgPrice"]),
                    "unrealised_pnl": float(p.get("unrealisedPnl", 0)),
                }
        return None
    except Exception as e:
        logging.error(f"Get position error: {e}")
        return None

def get_last_closed_pnl(expected_entry=None, max_retries=4, delay=3):
    """Vérifie que le trade récupéré correspond bien à l'entrée attendue,
    avec retry — évite le bug où Bybit renvoie un ancien trade par délai d'indexation."""
    for attempt in range(max_retries):
        try:
            res = session.get_closed_pnl(category="linear", symbol=SYMBOL, limit=1)
            items = res["result"]["list"]
            if items:
                item = items[0]
                avg_entry = float(item["avgEntryPrice"])
                if expected_entry is None or abs(avg_entry - expected_entry) / expected_entry < 0.001:
                    return {
                        "pnl": float(item["closedPnl"]),
                        "avg_entry": avg_entry,
                        "avg_exit": float(item["avgExitPrice"]),
                        "side": item["side"],
                        "qty": float(item["qty"]),
                    }
                else:
                    logging.warning(f"closed_pnl périmé (attendu≈{expected_entry:.5f}, reçu {avg_entry:.5f}) "
                                     f"— retry {attempt+1}/{max_retries}")
        except Exception as e:
            logging.error(f"get_closed_pnl error: {e}")
        time.sleep(delay)
    return None

def set_native_trailing_stop(distance):
    if distance is None or distance <= 0:
        logging.warning("Trailing distance invalide, activation ignorée")
        return False
    try:
        session.set_trading_stop(
            category="linear", symbol=SYMBOL,
            trailingStop=str(round(distance, 5)), positionIdx=0
        )
        logging.info(f"Trailing stop natif activé, distance={distance:.5f}")
        return True
    except Exception as e:
        logging.error(f"set_trading_stop error: {e}")
        return False

def get_klines_closed(interval, limit=220):
    """Récupère les klines et retire la dernière si elle est encore en formation
    (le backtest ne travaille jamais sur une bougie non fermée)."""
    try:
        res = session.get_kline(category="linear", symbol=SYMBOL, interval=interval, limit=limit)
        kl = list(reversed(res["result"]["list"]))
        if not kl:
            return kl
        interval_sec = int(interval) * 60 if interval.isdigit() else 86400
        last_open_time_sec = int(kl[-1][0]) / 1000
        if last_open_time_sec + interval_sec > time.time():
            kl = kl[:-1]
        return kl
    except Exception as e:
        logging.error(f"get_klines error: {e}")
        return []

# ============================================================
#  INDICATEURS (identiques au backtest)
# ============================================================

def ema(values, period):
    if len(values) < period:
        return [None] * len(values)
    result = [None] * len(values)
    result[period-1] = sum(values[:period]) / period
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i-1]
    return result

def rsi(closes, period=RSI_PERIOD):
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

def macd(closes, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
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

def stoch_rsi(closes, period=STOCH_PERIOD, smooth=STOCH_SMOOTH):
    r = rsi(closes, period)
    k = [50.0] * len(closes)
    for i in range(period, len(closes)):
        window = r[i-period+1:i+1]
        mini, maxi = min(window), max(window)
        k[i] = 50 if maxi == mini else ((r[i] - mini) / (maxi - mini)) * 100
    for i in range(len(closes)):
        if i >= smooth - 1:
            k[i] = sum(k[i-smooth+1:i+1]) / smooth
    return k

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

def volume_ma(volumes, period=VOLUME_MA_PERIOD):
    vma = [None] * len(volumes)
    for i in range(period-1, len(volumes)):
        vma[i] = sum(volumes[i-period+1:i+1]) / period
    return vma

def detect_bos(highs, lows, idx, lookback):
    if idx < lookback:
        return False, False
    return highs[idx] > max(highs[idx-lookback:idx]), lows[idx] < min(lows[idx-lookback:idx])

def detect_pullback(closes, idx, direction, threshold=PULLBACK_THRESHOLD, lookback=PULLBACK_LOOKBACK):
    if idx < lookback:
        return False
    if direction == "bull":
        recent_high = max(closes[idx-lookback:idx-3])
        min_low = min(closes[idx-5:idx])
        if recent_high > 0:
            return (recent_high - min_low) / recent_high <= threshold and closes[idx] > min_low
    else:
        recent_low = min(closes[idx-lookback:idx-3])
        max_high = max(closes[idx-5:idx])
        if recent_low > 0:
            return (max_high - recent_low) / recent_low <= threshold and closes[idx] < max_high
    return False

# ============================================================
#  CALCUL DU SCORE — aligné exactement sur le backtest
# ============================================================

def compute_scores():
    kl = get_klines_closed(TIMEFRAME, 220)
    if len(kl) < 205:
        return None

    closes = [float(x[4]) for x in kl]
    highs = [float(x[2]) for x in kl]
    lows = [float(x[3]) for x in kl]
    volumes = [float(x[5]) for x in kl]

    ema_fast_l = ema(closes, EMA_FAST)
    ema_mid_l = ema(closes, EMA_MID)
    ema_slow_l = ema(closes, EMA_SLOW)
    rsi_list = rsi(closes)
    macd_line, macd_sig = macd(closes)
    stoch_k = stoch_rsi(closes)
    atr_list = atr(highs, lows, closes)
    vma = volume_ma(volumes)

    i = -1
    if any(x is None for x in [ema_fast_l[i], ema_mid_l[i], ema_slow_l[i], atr_list[i]]):
        return None

    candle_time = int(kl[-1][0])
    price = closes[i]
    current_atr = atr_list[i]
    atr_pct = current_atr / price if price > 0 else None
    current_rsi = rsi_list[i]
    current_vol = volumes[i]

    # Filtre de volatilité — appliqué ici, pour de vrai cette fois
    if atr_pct is None or atr_pct < ATR_PCT_MIN or atr_pct > ATR_PCT_MAX:
        return {"filtered_out": True, "atr_pct": atr_pct, "candle_time": candle_time}

    # Tendance calculée en local 30m — identique au backtest (plus de biais D1 séparé)
    e_f, e_m, e_s = ema_fast_l[i], ema_mid_l[i], ema_slow_l[i]
    trend_bull = e_f > e_m > e_s
    trend_bear = e_f < e_m < e_s
    trend_label = "BULL" if trend_bull else ("BEAR" if trend_bear else "NEUTRE")

    buy_score = sell_score = 0.0
    buy_details, sell_details = [], []

    if current_rsi <= RSI_OVERSOLD:
        pts = RSI_POINTS + max(0, (RSI_OVERSOLD - current_rsi) / RSI_OVERSOLD)
        buy_score += pts; buy_details.append("RSI")
    elif current_rsi >= RSI_OVERBOUGHT:
        pts = RSI_POINTS + max(0, (current_rsi - RSI_OVERBOUGHT) / 20)
        sell_score += pts; sell_details.append("RSI")

    if price > e_f > e_m:
        buy_score += EMA_POINTS; buy_details.append("EMA")
    elif price < e_f < e_m:
        sell_score += EMA_POINTS; sell_details.append("EMA")

    if macd_line[i] is not None and macd_sig[i] is not None:
        if macd_line[i] > macd_sig[i] and macd_line[i] > 0:
            buy_score += MACD_POINTS; buy_details.append("MACD")
        elif macd_line[i] < macd_sig[i] and macd_line[i] < 0:
            sell_score += MACD_POINTS; sell_details.append("MACD")

    if stoch_k[i] <= STOCH_OVERSOLD:
        buy_score += STOCH_POINTS; buy_details.append("Stoch")
    elif stoch_k[i] >= STOCH_OVERBOUGHT:
        sell_score += STOCH_POINTS; sell_details.append("Stoch")

    # Fix double-scoring : le volume ne renforce que le côté actuellement dominant
    if vma[i] and current_vol > vma[i] * VOLUME_SPIKE_MULT:
        if buy_score > sell_score and buy_score > 0:
            buy_score += VOLUME_POINTS; buy_details.append("Vol")
        elif sell_score > buy_score and sell_score > 0:
            sell_score += VOLUME_POINTS; sell_details.append("Vol")

    st_bos_h, st_bos_b = detect_bos(highs, lows, len(closes)-1, ST_BOS_LOOKBACK)
    lt_bos_h, lt_bos_b = detect_bos(highs, lows, len(closes)-1, LT_BOS_LOOKBACK)

    if st_bos_h: buy_score += ST_BOS_POINTS; buy_details.append("ST-BOS")
    if lt_bos_h: buy_score += LT_BOS_POINTS; buy_details.append("LT-BOS")
    if st_bos_b: sell_score += ST_BOS_POINTS; sell_details.append("ST-BOS")
    if lt_bos_b: sell_score += LT_BOS_POINTS; sell_details.append("LT-BOS")

    if detect_pullback(closes, len(closes)-1, "bull"):
        buy_score += PULLBACK_POINTS; buy_details.append("Pullback")
    if detect_pullback(closes, len(closes)-1, "bear"):
        sell_score += PULLBACK_POINTS; sell_details.append("Pullback")

    return {
        "filtered_out": False,
        "price": price, "atr": current_atr, "atr_pct": atr_pct,
        "trend": trend_label, "candle_time": candle_time,
        "buy_score": buy_score, "buy_details": buy_details,
        "sell_score": sell_score, "sell_details": sell_details,
        "buy_lt_bos": lt_bos_h, "buy_st_bos": st_bos_h,
        "sell_lt_bos": lt_bos_b, "sell_st_bos": st_bos_b,
    }

def get_swing_zone(highs, lows, lookback=HTF_LOOKBACK):
    if len(highs) < lookback:
        return None, None
    return min(lows[-lookback:]), max(highs[-lookback:])

def compute_htf_context():
    """Regarde 1H et 4H pour savoir si le prix actuel est collé à une zone
    de support/résistance sur une timeframe plus large que le 30m."""
    result = {"near_support": False, "near_support_tf": None,
              "near_resistance": False, "near_resistance_tf": None}
    for tf, label in HTF_TIMEFRAMES:
        kl = get_klines_closed(tf, 80)
        if len(kl) < HTF_LOOKBACK + 10:
            continue
        highs = [float(x[2]) for x in kl]
        lows = [float(x[3]) for x in kl]
        closes = [float(x[4]) for x in kl]
        atr_list = atr(highs, lows, closes)
        current_atr = atr_list[-1]
        if current_atr is None:
            continue
        support, resistance = get_swing_zone(highs, lows)
        price = closes[-1]
        tol = current_atr * HTF_TOL_ATR_MULT
        if support is not None and abs(price - support) <= tol:
            result["near_support"] = True
            result["near_support_tf"] = label
        if resistance is not None and abs(price - resistance) <= tol:
            result["near_resistance"] = True
            result["near_resistance_tf"] = label
    return result

def format_side_summary(score, details, label):
    if score <= 0:
        return f"{label} : +0"
    tag = ", ".join(details) if details else "?"
    return f"{label} : +{score:.1f} ({tag})"

def get_signal():
    data = compute_scores()
    if data is None or data.get("filtered_out"):
        return None, data

    required_buy = SCORE_MIN_CONTRE if data["trend"] == "BEAR" else SCORE_MIN
    required_sell = SCORE_MIN_CONTRE if data["trend"] == "BULL" else SCORE_MIN

    side, score, details, lt_bos, st_bos = None, 0, [], False, False
    if data["buy_score"] >= required_buy:
        side, score, details = "Buy", data["buy_score"], data["buy_details"]
        lt_bos, st_bos = data["buy_lt_bos"], data["buy_st_bos"]
    elif data["sell_score"] >= required_sell:
        side, score, details = "Sell", data["sell_score"], data["sell_details"]
        lt_bos, st_bos = data["sell_lt_bos"], data["sell_st_bos"]

    if side is None:
        return None, data

    # Filtre multi-timeframe : un Short près d'un support 1H/4H ou un Long
    # près d'une résistance 1H/4H est risqué -> on exige un score plus strict.
    # À l'inverse, si la HTF confirme la même zone, on l'ajoute comme confluence.
    htf = compute_htf_context()
    if side == "Sell":
        if htf["near_support"]:
            if score < SCORE_MIN_CONTRE:
                logging.info(f"Signal Sell annulé — proche support {htf['near_support_tf']} "
                             f"(score {score:.1f} < {SCORE_MIN_CONTRE})")
                return None, data
            details.append(f"⚠️Support-{htf['near_support_tf']}")
        if htf["near_resistance"]:
            details.append(f"✅{htf['near_resistance_tf']}-Résistance")
    else:  # Buy
        if htf["near_resistance"]:
            if score < SCORE_MIN_CONTRE:
                logging.info(f"Signal Buy annulé — proche résistance {htf['near_resistance_tf']} "
                             f"(score {score:.1f} < {SCORE_MIN_CONTRE})")
                return None, data
            details.append(f"⚠️Résistance-{htf['near_resistance_tf']}")
        if htf["near_support"]:
            details.append(f"✅{htf['near_support_tf']}-Support")

    if lt_bos:
        rr_used = RR_RATIO_LT
    elif st_bos:
        rr_used = RR_RATIO_ST
    else:
        rr_used = RR_RATIO_DEFAULT

    stop_dist = data["atr"] * SL_ATR_MULT
    price = data["price"]
    if side == "Buy":
        sl = price - stop_dist
        tp = price + stop_dist * rr_used
    else:
        sl = price + stop_dist
        tp = price - stop_dist * rr_used

    trailing_distance = data["atr"] * TRAILING_ATR_MULT

    signal = {
        "side": side, "entry": price, "sl": sl, "tp": tp,
        "score": score, "details": details, "trend": data["trend"],
        "rr_used": rr_used, "lt_bos": lt_bos, "st_bos": st_bos,
        "trailing_distance": trailing_distance,
    }
    return signal, data

# ============================================================
#  TRADING
# ============================================================

def place_order(signal, opposite_summary):
    global last_trade_time, tracked_position

    capital = get_balance()
    risk_amount = capital * RISK_PER_TRADE
    stop_dist = abs(signal["entry"] - signal["sl"])
    if stop_dist <= 0:
        return False

    qty = risk_amount / stop_dist
    qty = math.floor(qty / QTY_STEP) * QTY_STEP

    if qty < MIN_QTY:
        logging.warning(f"Qty calculée ({qty}) sous le minimum ({MIN_QTY}), trade ignoré")
        tg(f"⚠️ Signal {signal['side']} détecté mais quantité ({qty}) sous le minimum requis "
           f"({MIN_QTY}) — trade non exécuté. Capital ou risque à revoir.")
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
            slTriggerBy="LastPrice", tpTriggerBy="LastPrice",
            tpslMode="Full", timeInForce="GTC",
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

    bos_tag = "LT-BOS" if signal["lt_bos"] else ("ST-BOS" if signal["st_bos"] else "aucun BOS")

    msg = (
        f"{'🟢' if signal['side']=='Buy' else '🔴'} <b>NEXUS — OUVERTURE {signal['side'].upper()}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{SYMBOL}</b> | TF {TIMEFRAME}m\n"
        f"💰 Entrée : <code>{signal['entry']:.5f}</code>\n"
        f"🛑 SL : <code>{signal['sl']:.5f}</code>\n"
        f"🎯 TP : <code>{signal['tp']:.5f}</code> (RR {signal['rr_used']:.1f} — {bos_tag})\n"
        f"📦 Quantité : {qty} ({risk_amount:.2f} USDT risqués)\n"
        f"📊 Score : <b>{signal['score']:.1f}</b>\n"
        f"📈 Confluences : {', '.join(signal['details'])}\n"
        f"🌐 Tendance 30m : {signal['trend']}\n"
        f"🔭 Contexte 1H/4H inclus dans les confluences ci-dessus (⚠️/✅)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔁 <i>Signal opposé au même instant :</i>\n{opposite_summary}"
    )
    tg(msg)
    log_trade("OPEN", signal["side"], signal["entry"], signal["score"], 0, "NEW")
    logging.info(f"ENTRY {signal['side']} @ {signal['entry']:.5f}")
    return True

def recompute_trailing_distance_from_market():
    """Utilisé au redémarrage si une position existe déjà — recalcule
    une vraie distance ATR au lieu de laisser trailing_distance à None."""
    kl = get_klines_closed(TIMEFRAME, 30)
    if len(kl) < 16:
        return None
    h = [float(x[2]) for x in kl]
    l = [float(x[3]) for x in kl]
    c = [float(x[4]) for x in kl]
    atr_vals = atr(h, l, c)
    last_atr = atr_vals[-1]
    return last_atr * TRAILING_ATR_MULT if last_atr else None

def manage_trailing(real_pos):
    global tracked_position
    if tracked_position is None or real_pos is None:
        return
    if tracked_position.get("trailing_distance") is None:
        return  # garde-fou : jamais d'appel avec une distance invalide

    entry = tracked_position["entry"]
    unrealised = real_pos["unrealised_pnl"]
    size_value = real_pos["size"] * entry
    profit_pct_estimate = (unrealised / size_value) if size_value > 0 else 0

    if not tracked_position["trailing_active"] and profit_pct_estimate >= TRAILING_ACTIVATE_PCT:
        ok = set_native_trailing_stop(tracked_position["trailing_distance"])
        if ok:
            tracked_position["trailing_active"] = True
            tg(f"🔄 <b>Trailing stop activé</b>\nDistance: {tracked_position['trailing_distance']:.5f} "
               f"(profit actuel ≈ {profit_pct_estimate*100:.2f}%)")

def send_close_notification(expected_entry=None):
    closed = get_last_closed_pnl(expected_entry=expected_entry)
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
        tg("✅ <b>Position fermée</b> (détails PnL indisponibles/non vérifiés via API)")
        log_trade("CLOSE", "Unknown", 0, 0, 0, "Fermeture détectée, PnL non récupéré")

# ============================================================
#  MAIN
# ============================================================

def main():
    global daily_start_capital, last_reset_date, last_trade_time, last_heartbeat, tracked_position, pending_confirmation

    flush_pending_updates()
    tg(f"🚀 <b>NEXUS v3.2 démarré</b>\nMode : DÉMO BYBIT\nSymbole : {SYMBOL}\nTF : {TIMEFRAME}m")

    real_pos = get_real_position()
    if real_pos:
        recomputed_distance = recompute_trailing_distance_from_market()
        tg(f"🔄 <b>Position reprise au démarrage</b>\n{real_pos['side']} {real_pos['size']} @ "
           f"{real_pos['entry']:.5f}\nPnL non réalisé : {real_pos['unrealised_pnl']:+.2f} USDT")
        tracked_position = {"side": real_pos["side"], "entry": real_pos["entry"],
                             "trailing_active": False, "trailing_distance": recomputed_distance}
    else:
        logging.info("Aucune position ouverte au démarrage")

    capital = get_balance()
    daily_start_capital = capital
    last_reset_date = date.today()
    last_heartbeat = time.time()

    previous_had_position = real_pos is not None

    while True:
        try:
            check_telegram_commands()

            if date.today() != last_reset_date:
                daily_start_capital = get_balance()
                last_reset_date = date.today()
                tg(f"🔁 Nouveau jour — capital de référence reset à {daily_start_capital:.2f} USDT")

            capital = get_balance()
            daily_pnl = capital - daily_start_capital

            if daily_pnl <= -(daily_start_capital * MAX_DAILY_LOSS_PCT):
                tg(f"🛑 <b>MAX DAILY LOSS atteint</b>\nP&L jour : {daily_pnl:.2f} USDT\nBot en pause 30 min.")
                time.sleep(1800)
                continue

            real_pos = get_real_position()
            has_position = real_pos is not None

            if previous_had_position and not has_position:
                expected_entry = tracked_position["entry"] if tracked_position else None
                send_close_notification(expected_entry=expected_entry)
                tracked_position = None

            if has_position:
                manage_trailing(real_pos)

            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL_SEC:
                pause_tag = " ⏸️" if bot_paused else ""
                if has_position:
                    status = (f"📍 Position en cours: {real_pos['side']} @ {real_pos['entry']:.5f} "
                              f"(PnL: {real_pos['unrealised_pnl']:+.2f} USDT)")
                    tg(f"💓 <b>NEXUS actif{pause_tag}</b>\nCapital: {capital:.2f} USDT\n{status}")
                else:
                    data = compute_scores()
                    if data and not data.get("filtered_out"):
                        long_line = format_side_summary(data["buy_score"], data["buy_details"], "Long")
                        short_line = format_side_summary(data["sell_score"], data["sell_details"], "Short")
                        tg(f"💓 <b>NEXUS actif{pause_tag}</b>\nCapital: {capital:.2f} USDT\n"
                           f"📭 Aucune position ouverte, en attente d'un signal.\n"
                           f"📊 {long_line}\n📊 {short_line}\n🌐 Tendance 30m: {data['trend']}")
                    elif data and data.get("filtered_out"):
                        tg(f"💓 <b>NEXUS actif{pause_tag}</b>\nCapital: {capital:.2f} USDT\n"
                           f"📭 Volatilité hors zone de trading (ATR {data['atr_pct']*100:.3f}% du prix), en attente.")
                    else:
                        tg(f"💓 <b>NEXUS actif{pause_tag}</b>\nCapital: {capital:.2f} USDT\n📭 En attente de données suffisantes.")
                last_heartbeat = time.time()

            previous_had_position = has_position

            if not has_position and not bot_paused and time.time() - last_trade_time > COOLDOWN_AFTER_TRADE_SEC:
                signal, data = get_signal()

                if not CONFIRM_NEXT_CANDLE:
                    if signal and data:
                        opposite_summary = (format_side_summary(data["sell_score"], data["sell_details"], "Short")
                                             if signal["side"] == "Buy" else
                                             format_side_summary(data["buy_score"], data["buy_details"], "Long"))
                        place_order(signal, opposite_summary)
                else:
                    current_candle_time = data.get("candle_time") if data else None
                    if signal and current_candle_time is not None:
                        if (pending_confirmation
                                and pending_confirmation["side"] == signal["side"]
                                and pending_confirmation["candle_time"] != current_candle_time):
                            # Même signal confirmé sur une nouvelle bougie -> on exécute
                            opposite_summary = (format_side_summary(data["sell_score"], data["sell_details"], "Short")
                                                 if signal["side"] == "Buy" else
                                                 format_side_summary(data["buy_score"], data["buy_details"], "Long"))
                            place_order(signal, opposite_summary)
                            pending_confirmation = None
                        elif not pending_confirmation or pending_confirmation["candle_time"] != current_candle_time:
                            pending_confirmation = {"side": signal["side"], "candle_time": current_candle_time}
                            logging.info(f"Signal {signal['side']} en attente de confirmation sur la prochaine bougie")
                    elif pending_confirmation and current_candle_time is not None and pending_confirmation["candle_time"] != current_candle_time:
                        # Nouvelle bougie mais plus de signal -> le setup est mort
                        pending_confirmation = None

            time.sleep(LOOP_SLEEP_SEC)

        except Exception as e:
            logging.error(f"Loop error: {e}")
            tg(f"⚠️ Erreur boucle principale:\n<code>{e}</code>")
            time.sleep(20)

if __name__ == "__main__":
    main()
