"""
AskMyFile custom middleware.
DevCsrfMiddleware: in DEBUG mode only, bypasses Django's Origin check
when the browser sends 'Origin: null' (happens in IDE embedded previews,
some Chrome security policies on 127.0.0.1, etc.).
In production (DEBUG=False) this middleware is completely transparent.
"""
from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware


class DevCsrfMiddleware(CsrfViewMiddleware):
    """
    Drop-in replacement for CsrfViewMiddleware.
    When DEBUG is True and the Origin header is exactly 'null',
    skip the origin check so the CSRF token check still runs
    (token must still be present and valid) but the browser's
    null-origin doesn't cause an automatic 403.
    """

    def process_view(self, request, callback, callback_args, callback_kwargs):
        if settings.DEBUG:
            origin = request.META.get("HTTP_ORIGIN", "")
            if origin == "null":
                # Remove the header so Django's parent implementation
                # falls through to the Referer / token check only.
                request.META.pop("HTTP_ORIGIN", None)
        return super().process_view(request, callback, callback_args, callback_kwargs)
