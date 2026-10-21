"""Server-side geofence validation (never trust a client-side pass/fail)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

logger: logging.Logger = logging.getLogger(__name__)

EARTH_RADIUS_M: float = 6_371_000.0
MAX_ACCURACY_M: float = 100.0  # a GPS fix less accurate than this is flagged for review (reports, corrections, punch)


@dataclass(frozen=True)
class Site:
    """A workplace with a circular geofence.

    Attributes:
        name: Human-readable site name.
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        radius_m: Allowed distance from the center, in meters.
    """

    name: str
    lat: float
    lon: float
    radius_m: float


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the haversine distance between two points in meters.

    Args:
        lat1: First point latitude.
        lon1: First point longitude.
        lat2: Second point latitude.
        lon2: Second point longitude.

    Returns:
        Distance in meters.
    """
    p1: float = radians(lat1)
    p2: float = radians(lat2)
    dlat: float = p2 - p1
    dlon: float = radians(lon2 - lon1)
    a: float = sin(dlat / 2) ** 2 + cos(p1) * cos(p2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def is_inside(site: Site, lat: float, lon: float, accuracy_m: float = 0.0) -> bool:
    """Check whether a reported position lies inside a site geofence.

    The reported GPS accuracy is subtracted from the distance so a noisy fix
    is not rejected unfairly, but a very inaccurate fix cannot pass on its own.

    Args:
        site: Site to test against.
        lat: Reported latitude.
        lon: Reported longitude.
        accuracy_m: Reported GPS accuracy radius in meters.

    Returns:
        True when the position is within the site radius.
    """
    d: float = distance_m(site.lat, site.lon, lat, lon)
    logger.debug(
        f"geofence site={site.name} distance={d:.1f}m accuracy={accuracy_m:.1f}m radius={site.radius_m}m"
    )
    return max(d - accuracy_m, 0.0) <= site.radius_m
