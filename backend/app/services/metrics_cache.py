from collections.abc import Callable
from threading import RLock
from time import monotonic

from cachetools import TTLCache
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


class MetricsCache:
    max_age = 300

    def __init__(self):
        self._cache = TTLCache(maxsize=8 * 1024 * 1024, ttl=self.max_age, getsizeof=lambda value: len(value[1]))
        self._lock = RLock()

    def read(self, key: tuple, build: Callable[[], dict], refresh: bool = False) -> tuple[bytes, str]:
        # 同时到达的统计请求共用一次计算，缓存只保存不可变的响应正文。
        with self._lock:
            entry = self._cache.get(key)
            if not refresh and entry is not None and monotonic() - entry[0] < self.max_age:
                return entry[1], "hit"
            if entry is not None:
                del self._cache[key]
            started = monotonic()
            body = JSONResponse(jsonable_encoder(build())).body
            if len(body) > self._cache.maxsize:
                return body, "bypass"
            if key not in self._cache and len(self._cache) >= 32:
                self._cache.popitem()
            self._cache[key] = (started, body)
            return body, "refresh" if refresh else "miss"
