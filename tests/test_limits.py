from rssac002_dashboard.limits import RateLimiter


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_budget_is_spent_and_refills():
    clock = Clock()
    limiter = RateLimiter(60, clock=clock)  # one request a second, sixty in hand
    assert all(limiter.wait_for("a") == 0 for _ in range(60))
    assert limiter.wait_for("a") == 1.0  # empty: one more is a second away
    assert limiter.wait_for("b") == 0  # another client is unaffected
    clock.now += 2
    assert limiter.wait_for("a") == 0 and limiter.wait_for("a") == 0
    assert limiter.wait_for("a") > 0


def test_expensive_requests_cost_more():
    limiter = RateLimiter(100, clock=Clock())
    assert limiter.wait_for("a", cost=40) == 0 and limiter.wait_for("a", cost=40) == 0
    assert limiter.wait_for("a", cost=40) > 0
    assert limiter.wait_for("a") == 0  # twenty left for ordinary requests


def test_zero_turns_the_limit_off():
    limiter = RateLimiter(0, clock=Clock())
    assert all(limiter.wait_for("a", cost=40) == 0 for _ in range(1000))
