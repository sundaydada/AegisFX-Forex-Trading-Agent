from brokers.oanda_broker import OandaBroker
import os

# Load .env file
with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip()

broker = OandaBroker(
    api_key=os.getenv("OANDA_DEMO_API_KEY"),
    account_id=os.getenv("OANDA_ACCOUNT_ID"),
    base_url="https://api-fxpractice.oanda.com"
)

print(broker.get_account_balance())
