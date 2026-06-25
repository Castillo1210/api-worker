import asyncio
from typing import Callable, TypeVar, Any
from functools import wraps
import structlog

logger = structlog.get_logger()

T = TypeVar("T")

async def async_retry(
    func: Callable[..., T],
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    exceptions: tuple = (Exception,),
    **kwargs
) -> T:
    """Retry async con backoff exponencial"""
    last_exception = None

    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except exceptions as e:
            last_exception = e
            if attempt == max_delay - 1:
                break

            delay = min(base_delay * (exponential_base ** attempt), max_delay)
            logger.warning(
                "Retry intento",
                attempt=attempt + 1,
                max_retries=max_retries,
                delay=delay,
                error=str(e)
            )
            await asyncio.sleep(delay)
        
    raise last_exception