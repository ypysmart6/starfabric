const RAD = Math.PI / 180;

// USNO approximate apparent solar coordinates, evaluated at the MODEL timestamp.
// Equatorial-of-date is sufficient for this TEME visualization; not an ephemeris
// for orbit propagation, navigation, eclipse scheduling, or power calculations.
// https://aa.usno.navy.mil/faq/sun_approx
export function solarPosition(at) {
  const timestamp = Date.parse(at);
  if (!Number.isFinite(timestamp)) return null;
  const days = timestamp / 86400000 + 2440587.5 - 2451545;
  const anomaly = ((357.529 + .98560028 * days) % 360) * RAD;
  const longitude = ((280.459 + .98564736 * days) % 360 +
    1.915 * Math.sin(anomaly) + .020 * Math.sin(2 * anomaly)) * RAD;
  const obliquity = (23.439 - .00000036 * days) * RAD;
  const direction = [Math.cos(longitude), Math.cos(obliquity) * Math.sin(longitude),
    Math.sin(obliquity) * Math.sin(longitude)];
  return {
    direction,
    distanceAU: 1.00014 - .01671 * Math.cos(anomaly) - .00014 * Math.cos(2 * anomaly),
    rightAscension: Math.atan2(direction[1], direction[0]),
    declination: Math.asin(direction[2]),
  };
}
