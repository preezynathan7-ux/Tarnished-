from pybit.unified_trading import HTTP
import os

API_KEY = os.getenv("yhIWArGAp0JwDLDja2")
API_SECRET = os.getenv("Xlg8fjG557YapL9B6EwHBCtotWkiadnENRtE")
print("=== TEST API ===")
print("API_KEY:", API_KEY[:5] + "..." if API_KEY else "MANQUANTE")
print("API_SECRET:", API_SECRET[:5] + "..." if API_SECRET else "MANQUANTE")

if API_KEY and API_SECRET:
    try:
        session = HTTP(testnet=False, demo=True, api_key=API_KEY, api_secret=API_SECRET)
        balance = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        print("✅ Connexion réussie !")
        print("💰 Solde USDT:", balance["result"]["list"][0]["coin"][0]["walletBalance"])
    except Exception as e:
        print("❌ Erreur:", e)
else:
    print("❌ Clés API manquantes !")
