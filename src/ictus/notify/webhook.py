"""Posting one JSON body to one URL.

``urllib.request`` rather than a client library: this sends a small JSON POST to
a URL from the environment, and nothing about that needs connection pooling,
retries or a session. The standing rule is to justify each dependency, and this
one could not be.

**The URL is the credential.** An incoming-webhook URL is the whole
authorisation to post as whatever it points at — there is no second secret — so
nothing here puts one in a message, a log line or an exception. ``urllib``
cheerfully names the URL in ``HTTPError``/``URLError``, so failures are
rewritten rather than passed through; see ``DeliveryError``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from ictus.errors import IctusError

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["TIMEOUT_SECONDS", "DeliveryError", "post"]

#: A report is not worth holding a watcher open for.
TIMEOUT_SECONDS = 10.0


class DeliveryError(IctusError):
    """A report could not be delivered.

    Carries the notifier's name and what went wrong, never the endpoint. A
    failure that printed the URL would put a live credential into whatever
    reads the watcher's output, which is usually a terminal and sometimes a log.
    """


def post(
    url: str,
    body: Mapping[str, object],
    *,
    name: str,
    timeout: float = TIMEOUT_SECONDS,
) -> None:
    """POST ``body`` as JSON. Raises ``DeliveryError``, which names no URL."""
    payload = json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if request.type not in ("http", "https"):
        raise DeliveryError(f"notifier {name!r} is not an http(s) endpoint, so nothing was sent")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 400:
                raise DeliveryError(f"notifier {name!r} answered {response.status}")
    except urllib.error.HTTPError as exc:
        # `exc` stringifies with the URL in it. Only the status is reported.
        raise DeliveryError(f"notifier {name!r} answered {exc.code}") from None
    except urllib.error.URLError as exc:
        raise DeliveryError(f"notifier {name!r} is unreachable: {exc.reason}") from None
    except TimeoutError:
        raise DeliveryError(f"notifier {name!r} did not answer in {timeout:g}s") from None
