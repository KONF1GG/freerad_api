import time
import uuid
from collections import deque
from typing import Dict, Deque

class RequestTracker:
    """Трекинг запросов в очереди и их времени ожидания."""
    
    def __init__(self):
        self.queue: Deque[str] = deque()
        self.start_times: Dict[str, float] = {}
        self.max_queue_size = 0

    def add_request(self, request_id: str) -> None:
        """Добавляет запрос в очередь."""
        self.start_times[request_id] = time.time()
        self.queue.append(request_id)
        self.max_queue_size = max(self.max_queue_size, len(self.queue))

    def remove_request(self, request_id: str) -> None:
        """Удаляет запрос из очереди."""
        self.start_times.pop(request_id, None)
        try:
            self.queue.remove(request_id)
        except ValueError:
            pass

    def get_wait_time(self, request_id: str) -> float:
        """Возвращает время ожидания запроса."""
        return time.time() - self.start_times.get(request_id, time.time())

# Глобальный экземпляр для трекинга
request_tracker = RequestTracker()