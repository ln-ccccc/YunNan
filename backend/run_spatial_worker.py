#!/usr/bin/env python3
import logging
import signal
import threading

from applications.project_hub.spatial_worker import SpatialWorker
from applications.project_hub.worker_app import create_spatial_worker_app


def main():
    app = create_spatial_worker_app()
    stop_event = threading.Event()

    def request_stop(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    with app.app_context():
        SpatialWorker().run_forever(stop_event.is_set)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
