import time


class Watchdog:
    def __init__(self, timeout: int):
        self.timeout = timeout
        self.last_msg_time = time.time()

    def feed(self):
        self.last_msg_time = time.time()

    def is_timed_out(self) -> bool:
        return time.time() - self.last_msg_time > self.timeout

    def time_since_last_msg(self) -> float:
        return time.time() - self.last_msg_time
