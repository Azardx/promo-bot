import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
# Adicionamos a captura do ID do Grupo:
GROUP_ID = os.getenv("GROUP_ID") 

SCRAPE_INTERVAL = 60
HTTP_TIMEOUT = 15

SCRAPE_INTERVAL = 60

HTTP_TIMEOUT = 8
MAX_CONNECTIONS = 100
BROADCAST_CONCURRENCY = 25
ADMIN_ID = 2037914903