import uvicorn
from ratingrelay.settings import LOG_CONFIG, get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "ratingrelay.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        log_config=LOG_CONFIG,
    )
