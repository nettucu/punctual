from punctual.geofence import Site, distance_m, is_inside

SITE: Site = Site(name="test", lat=44.4268, lon=26.1025, radius_m=100.0)


def test_distance_zero() -> None:
    assert distance_m(SITE.lat, SITE.lon, SITE.lat, SITE.lon) == 0.0


def test_inside_and_outside() -> None:
    assert is_inside(SITE, 44.4270, 26.1026)
    assert not is_inside(SITE, 44.4400, 26.1025)


def test_accuracy_slack_does_not_rescue_far_fix() -> None:
    assert not is_inside(SITE, 44.4400, 26.1025, accuracy_m=50.0)
