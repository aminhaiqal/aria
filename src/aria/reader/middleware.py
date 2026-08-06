class ReaderSecurityHeadersMiddleware:
    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith(("/reader/", "/api/reader/")):
            response["Content-Security-Policy"] = (
                "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
                "img-src 'self' data:; object-src 'none'; script-src 'self'; style-src 'self'"
            )
            response["Referrer-Policy"] = "same-origin"
            response["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
            response["X-Robots-Tag"] = "noindex, nofollow"
            if not request.path.startswith("/reader/assets/"):
                response["Cache-Control"] = "private, no-store"
        return response
