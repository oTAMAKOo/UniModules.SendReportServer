import time
from collections import defaultdict
from fastapi import Request, HTTPException


class RateLimiter:
    """シンプルなインメモリレート制限。IPアドレスごとに制御する。"""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _cleanup(self, ip: str, now: float):
        cutoff = now - self.window_seconds
        self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]

    def check(self, request: Request):
        """レート制限チェック。超過時は429エラーを送出する。"""
        ip = self._get_client_ip(request)
        now = time.time()
        self._cleanup(ip, now)

        if len(self._requests[ip]) >= self.max_requests:
            raise HTTPException(
                status_code=429,
                detail="リクエスト数が上限に達しました。しばらくしてから再試行してください。",
            )

        self._requests[ip].append(now)


# レポート受信API用: 1分間に60リクエストまで
report_limiter = RateLimiter(max_requests=60, window_seconds=60)

# ログイン用: 1分間に10回まで（ブルートフォース対策）
login_limiter = RateLimiter(max_requests=10, window_seconds=60)
