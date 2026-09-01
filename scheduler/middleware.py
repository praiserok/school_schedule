from django.shortcuts import redirect


class LoginRequiredMiddleware:
    """
    Неавторизованих користувачів:
    - /accounts/, /admin/, /static/, /public/ — пропускаємо без змін
    - /admin-* або ?next= — редирект на логін (адмін сценарій)
    - будь-який інший URL — редирект на /public/ (звичайний відвідувач)
    """

    PUBLIC_PREFIXES = ('/accounts/', '/admin/', '/static/', '/public/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            path = request.path
            if not any(path.startswith(p) for p in self.PUBLIC_PREFIXES):
                return redirect('/public/')
        return self.get_response(request)
