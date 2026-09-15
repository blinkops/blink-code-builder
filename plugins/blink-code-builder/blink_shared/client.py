"""The HTTP client every Blink controller call goes through.

One place owns the base URL, the auth header, connect retries, and the translation of a
raw 401/403 into a message the caller can act on.
"""

import httpx

from .config import require_env

# httpx retries these on connect failures only — ConnectError and ConnectTimeout, which
# cover the occasional reset from the controller. Never on an HTTP status code.
CONNECT_RETRIES = 3


def _explain_authorization_failure(response):
    """Turn a raw 401/403 into an actionable message instead of a traceback.

    403 is almost always a roles issue. Blink permissions have two layers: a company role
    (Admin / Builder / Consumer) and a workspace role (Owner / Contributor / Viewer).
    Workflow endpoints need the workspace one, so a key from a company Builder with no
    workspace role 403s on every call.

    Registered as a response event hook, which also fires on streamed responses before
    the body is available — so the body is never read here. Raises RuntimeError rather
    than exiting, because this runs inside the MCP server process.
    """
    if response.status_code == 401:
        raise RuntimeError(
            "Blink API returned 401 Unauthorized — the API key is invalid or expired. "
            "Route the user through the setup-blink-plugin skill to update it."
        )
    if response.status_code == 403:
        raise RuntimeError(
            f"Blink API returned 403 Forbidden on {response.request.url.path} — the key is "
            "valid but its user lacks the workflow permission in this workspace. Note: the "
            "company-level 'Builder' role alone is NOT enough (it carries no workflow "
            "permissions). Access needs a workspace role in the target workspace — Viewer can "
            "read, but Contributor or above is required for this plugin's save/test/publish "
            "flow. (Company Admin also has access.) Ask a Blink admin to add them to the "
            "workspace, then retry."
        )


def build_client(timeout=60):
    """A configured httpx.Client: workspace base URL, auth header, retrying transport.

    Every request lands on /api/v1/workspace/<id>/..., so it always describes the
    configured workspace. The unscoped route is valid too, but the controller resolves it
    against whichever workspace the UI last selected.
    """
    controller, workspace, api_key = require_env()
    return httpx.Client(
        base_url=f"{controller}/workspace/{workspace}",
        headers={"BLINK-API-KEY": api_key},
        timeout=timeout,
        transport=httpx.HTTPTransport(retries=CONNECT_RETRIES),
        event_hooks={"response": [_explain_authorization_failure]},
    )


def raise_for_status(response):
    """Like `response.raise_for_status()`, but raises a RuntimeError carrying the response
    body — so a plain call surfaces the same readable error as the paths that hand-check
    specific status codes. Returns the response, for chaining.

    Callers include streamed responses (`client.stream(...)`), whose body isn't
    auto-read the way a plain `client.get()`/`client.post()` response's is — reading
    it here before touching `.text` avoids httpx's `ResponseNotRead` masking the real
    HTTP error.
    """
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        if not error.response.is_closed:
            error.response.read()
        raise RuntimeError(f"HTTP {error.response.status_code}: {error.response.text[:400]}") from error
    return response
