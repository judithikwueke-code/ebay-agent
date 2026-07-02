import requests

TOKEN = "8583121219:AAFbpza_GbFcfzjp8_mDZAGgWbZ5sAS9Z14"
CHAT_ID = "7681216735"

message = (
    "First eBay Sale - Action Needed!\n\n"
    "Item: 2-in-1 Spray Bottle Detangling Hair Brush\n"
    "Buyer: Sharni Burness\n"
    "Address: 4 Fenwick Street, Boldon Colliery, NE35 9HU\n"
    "Sale: 10.99 | Cost: 4.99 | Profit: ~6\n\n"
    "STEP 1 - Order on CJ now (5 min)\n"
    "1. Login: cjdropshipping.com\n"
    "2. Search: spray bottle detangling hair brush\n"
    "3. Ship to Sharni at address above\n"
    "4. Top up CJ wallet if needed (~5)\n"
    "5. Save tracking number from CJ confirmation email\n\n"
    "STEP 2 - Upload tracking when you have it\n"
    "SSH into VPS then run:\n"
    "cd ~/ebay-agent\n"
    "python upload_tracking.py 10-14788-84978 YOUR_TRACKING_NUMBER\n\n"
    "eBay dispatch deadline: 25 June\n\n"
    "Bugs fixed today:\n"
    "- Order address parsing fixed\n"
    "- Listing ID field corrected\n"
    "- CJ error logging improved\n\n"
    "CJ auto-ordering blocked (code 16900205) - likely empty CJ wallet. "
    "Top it up and future orders will place automatically."
)

resp = requests.post(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    json={"chat_id": CHAT_ID, "text": message},
    timeout=10,
)
print(f"Status: {resp.status_code}")
data = resp.json()
if data.get("ok"):
    print("Message sent successfully")
else:
    print("Error:", data.get("description", "unknown"))
