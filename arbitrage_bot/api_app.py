from fastapi import FastAPI

from arbitrage_bot.api import internal

app = FastAPI(title="Arbitrage Alert Bot API")


@app.get("/")
async def root():
    return {
        "message": "Arbitrage Alert Bot API is running",
        "docs": "/docs",
        "health": "/api/health",
        "status": "/api/status",
    }


app.include_router(internal.router, prefix="/api")