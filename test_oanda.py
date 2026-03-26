from brokers.oanda_broker import OandaBroker
import os

broker = OandaBroker(
    api_key=os.getenv("OANDA_API_KEY"),
    account_id=os.getenv("OANDA_ACCOUNT_ID"),
    base_url="https://api-fxpractice.oanda.com"
)

print(broker.get_account_balance())