import uvicorn

from web.scheduler import start_scheduler, stop_scheduler

if __name__ == "__main__":
    start_scheduler()
    try:
        uvicorn.run("web.app:app", host="0.0.0.0", port=8000)
    finally:
        stop_scheduler()
