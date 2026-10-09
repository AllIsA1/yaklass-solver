import http.cookiejar as cj
import math

import pytest

from yaklass_bot.session import _expires, to_playwright_cookies


@pytest.mark.parametrize("raw,expected", [
    (None, -1), (0, -1), (-11644473600, -1), (-5, -1), (float("nan"), -1), ("abc", -1), (-1, -1),
    (1_800_000_000, 1_800_000_000),                 # секунды — без изменений
])
def test_expires_basic(raw, expected):
    assert _expires(raw) == expected


def test_expires_ms_and_us_scaled_to_seconds():
    assert _expires(1_800_000_000_000) == pytest.approx(1_800_000_000)
    assert _expires(1_800_000_000_000_000) == pytest.approx(1_800_000_000)


def _cookie(name, expires, domain=".yaklass.ru"):
    return cj.Cookie(0, name, "v", None, False, domain, True, True, "/", True, True, expires,
                     False, None, None, {})


def test_all_cookies_valid_for_playwright():
    jar = cj.CookieJar()
    for i, e in enumerate([None, 0, -11644473600, 1_800_000_000, 1_800_000_000_000]):
        jar.set_cookie(_cookie(f"c{i}", e))
    cookies = to_playwright_cookies(jar)
    assert len(cookies) == 5
    for c in cookies:                                # правило Playwright: -1 или положительное
        assert c["expires"] == -1 or (c["expires"] > 0 and not math.isnan(c["expires"]))
