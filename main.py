"""Single entry point: WebApp + Telegram /start bot + background order worker.

    python main.py
"""
import uvicorn
from activon.config import config

if __name__ == "__main__":
    uvicorn.run("activon.web:app", host=config.host, port=config.port, log_level="info", workers=1)
