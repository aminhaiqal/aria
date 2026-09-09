import hashlib

from django.conf import settings
from django.core.cache import cache


def _login_key(remote_address: str, username: str) -> str:
    material = f"{remote_address.strip()}\0{username.strip().casefold()}".encode()
    return f"aria:login-failure:{hashlib.sha256(material).hexdigest()}"


def login_attempts(request) -> int:
    key = _login_key(request.META.get("REMOTE_ADDR", "unknown"), request.POST.get("username", ""))
    return int(cache.get(key, 0))


def login_is_blocked(request) -> bool:
    return login_attempts(request) >= settings.LOGIN_RATE_LIMIT_ATTEMPTS


def register_login_failure(request) -> int:
    key = _login_key(request.META.get("REMOTE_ADDR", "unknown"), request.POST.get("username", ""))
    if cache.add(key, 1, timeout=settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS):
        return 1
    try:
        return int(cache.incr(key))
    except ValueError:
        cache.set(key, 1, timeout=settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS)
        return 1


def clear_login_failures(request) -> None:
    key = _login_key(request.META.get("REMOTE_ADDR", "unknown"), request.POST.get("username", ""))
    cache.delete(key)
