"""App-wide surface contracts (main.py)."""

from app.main import app


def test_no_verbs_beyond_get_post_on_any_route():
    # D22: both CORS layers allow exactly GET/POST — one app-wide sweep instead
    # of a copy per router, so a new router can't ship an unchecked verb.
    # (Starlette auto-adds HEAD to GET routes; it is not a new verb.)
    methods = {m for route in app.routes for m in getattr(route, "methods", ())}
    assert methods <= {"GET", "HEAD", "POST"}
