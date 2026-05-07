from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

import httpx


@dataclass
class ApiError(Exception):
    provider: str
    endpoint: str
    status_code: int | None
    message: str
    request_url: str | None = None

    def __str__(self) -> str:
        status = self.status_code if self.status_code is not None else "network"
        return f"{self.provider} {self.endpoint} failed ({status}): {self.message}"


class JsonHttpClient:
    def __init__(self, timeout: float, user_agent: str):
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )

    def get_json(
        self,
        provider: str,
        endpoint: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        max_retries: int = 2,
    ) -> Any:
        last_error: ApiError | None = None
        for attempt in range(max_retries + 1):
            try:
                response = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = ApiError(provider, endpoint, None, str(exc), url)
            else:
                if 200 <= response.status_code < 300:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise ApiError(
                            provider,
                            endpoint,
                            response.status_code,
                            f"invalid JSON: {exc}",
                            str(response.url),
                        ) from exc

                last_error = ApiError(
                    provider,
                    endpoint,
                    response.status_code,
                    response.text[:300],
                    str(response.url),
                )
                if response.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                    break

            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))

        assert last_error is not None
        raise last_error
