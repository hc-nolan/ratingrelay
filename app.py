import uvicorn
from ratingrelay.settings import LOG_CONFIG

if __name__ == "__main__":
    uvicorn.run(
        "ratingrelay.main:app",
        host="0.0.0.0",
        reload=True,
        log_config=LOG_CONFIG,
    )
