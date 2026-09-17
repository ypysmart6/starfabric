use serde::{Deserialize, Serialize};
use std::io;
use std::process::Command;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct FallbackRoute {
    pub prefix: String,
    pub via: String,
    pub interface: String,
    #[serde(default = "default_metric")]
    pub metric: u32,
}

/// Resolve the egress gateway through the node's current IGP/kernel routes.
/// This continues to work when the ground controller is unavailable.
#[derive(Clone, Debug, Deserialize)]
pub struct FallbackTarget {
    pub prefix: String,
    pub gateway_loopback: String,
    #[serde(default = "default_metric")]
    pub metric: u32,
}

pub fn validate_target(target: &FallbackTarget) -> io::Result<()> {
    validate(&FallbackRoute {
        prefix: target.prefix.clone(), via: target.gateway_loopback.clone(),
        interface: "lo".into(), metric: target.metric,
    })
}

pub fn resolve_reply(target: &FallbackTarget, bytes: &[u8]) -> io::Result<Option<FallbackRoute>> {
    let records: serde_json::Value = serde_json::from_slice(bytes)?;
    let entry = records.as_array().and_then(|v| v.first())
        .ok_or_else(|| io::Error::other("empty gateway route lookup"))?;
    if matches!(entry["type"].as_str(), Some("blackhole" | "unreachable" | "prohibit")) {
        return Ok(None);
    }
    let (Some(via), Some(interface)) = (entry["gateway"].as_str(), entry["dev"].as_str()) else {
        return Ok(None);
    };
    let route = FallbackRoute { prefix: target.prefix.clone(), via: via.into(),
        interface: interface.into(), metric: target.metric };
    validate(&route)?;
    Ok(Some(route))
}

pub fn resolve(targets: &[FallbackTarget]) -> io::Result<Vec<FallbackRoute>> {
    let mut routes = Vec::new();
    for target in targets {
        validate_target(target)?;
        let family = if target.gateway_loopback.contains(':') { "-6" } else { "-4" };
        let result = Command::new("ip")
            .args([family, "-j", "route", "get", &target.gateway_loopback]).output()?;
        if !result.status.success() {
            // An unreachable egress must withdraw its old fallback, not retain
            // a next hop from an earlier orbital contact.
            continue;
        }
        if let Some(route) = resolve_reply(target, &result.stdout)? { routes.push(route); }
    }
    Ok(routes)
}

/// Update changed routes, then remove only keys absent from the new set.
/// On error, restore the previous set before reporting the failed update.
pub fn transition(previous: &[FallbackRoute], desired: &[FallbackRoute], dry_run: bool) -> io::Result<()> {
    for route in desired { validate(route)?; }
    let same_key = |a: &FallbackRoute, b: &FallbackRoute| a.prefix == b.prefix && a.metric == b.metric;
    let changed: Vec<_> = desired.iter().filter(|r| !previous.contains(r)).cloned().collect();
    let obsolete: Vec<_> = previous.iter().filter(|r| !desired.iter().any(|n| same_key(r, n))).cloned().collect();
    let result = apply(&changed, dry_run).and_then(|_| remove(&obsolete, dry_run));
    if let Err(error) = result {
        let added: Vec<_> = desired.iter().filter(|r| !previous.iter().any(|p| same_key(r, p))).cloned().collect();
        let restored = apply(previous, dry_run);
        let removed = remove(&added, dry_run);
        return Err(io::Error::other(format!("{error}; restore={restored:?}; remove new={removed:?}")));
    }
    Ok(())
}

fn default_metric() -> u32 {
    42760
}

pub fn validate(route: &FallbackRoute) -> io::Result<()> {
    let (address, length) = route
        .prefix
        .split_once('/')
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "prefix must be CIDR"))?;
    let network = address
        .parse::<std::net::IpAddr>()
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid CIDR address"))?;
    let prefix_length = length
        .parse::<u8>()
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid CIDR prefix length"))?;
    let maximum = if network.is_ipv6() { 128 } else { 32 };
    if prefix_length > maximum {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "CIDR prefix length is out of range",
        ));
    }
    let next_hop = route
        .via
        .parse::<std::net::IpAddr>()
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid next-hop"))?;
    if network.is_ipv6() != next_hop.is_ipv6() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "CIDR and next-hop address families differ",
        ));
    }
    if route.interface.is_empty()
        || route.interface.len() > 15
        || !route
            .interface
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.'))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid interface",
        ));
    }
    if route.metric == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "route metric must be positive",
        ));
    }
    Ok(())
}

// remove withdraws only the exact fallback routes owned by this runtime.
pub fn remove(routes: &[FallbackRoute], dry_run: bool) -> io::Result<()> {
    for route in routes {
        validate(route)?;
        if dry_run {
            continue;
        }
        let family = if route.via.contains(':') { "-6" } else { "-4" };
        let status = Command::new("ip")
            .args([
                family,
                "route",
                "del",
                &route.prefix,
                "via",
                &route.via,
                "dev",
                &route.interface,
                "metric",
                &route.metric.to_string(),
            ])
            .status()?;
        if !status.success() {
            return Err(io::Error::other(format!(
                "ip route delete failed for {}",
                route.prefix
            )));
        }
    }
    Ok(())
}

// apply uses argv execution, never a shell. `ip route replace` is deliberately
// idempotent and is only activated after the controller hold timer expires.
pub fn apply(routes: &[FallbackRoute], dry_run: bool) -> io::Result<()> {
    for route in routes {
        validate(route)?;
        if dry_run {
            continue;
        }
        let family = if route.via.contains(':') { "-6" } else { "-4" };
        let status = Command::new("ip")
            .args([
                family,
                "route",
                "replace",
                &route.prefix,
                "via",
                &route.via,
                "dev",
                &route.interface,
                "metric",
                &route.metric.to_string(),
            ])
            .status()?;
        if !status.success() {
            return Err(io::Error::other(format!(
                "ip route failed for {}",
                route.prefix
            )));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn dynamic_fallback_tracks_igp_and_withdraws_unreachable_gateway() {
        let target = FallbackTarget { prefix: "172.22.0.8/32".into(), gateway_loopback: "10.255.0.2".into(), metric: 42760 };
        let a = resolve_reply(&target, br#"[{"gateway":"10.128.0.6","dev":"sf1"}]"#).unwrap().unwrap();
        let b = resolve_reply(&target, br#"[{"gateway":"10.128.0.10","dev":"sf2"}]"#).unwrap().unwrap();
        assert_ne!(a, b);
        assert_eq!(b.prefix, target.prefix);
        assert!(resolve_reply(&target, br#"[{"type":"unreachable"}]"#).unwrap().is_none());
        assert!(resolve_reply(&target, br#"[{"gateway":"bad","dev":"sf2"}]"#).is_err());
        transition(&[a], &[b], true).unwrap();
    }
    #[test]
    fn rejects_interface_injection() {
        let route = FallbackRoute {
            prefix: "2001:db8::/64".into(),
            via: "fe80::1".into(),
            interface: "eth0;reboot".into(),
            metric: 1,
        };
        assert!(validate(&route).is_err());
    }

    #[test]
    fn rejects_invalid_cidr_and_family() {
        let mut route = FallbackRoute {
            prefix: "not-an-ip/64".into(),
            via: "fe80::1".into(),
            interface: "eth0".into(),
            metric: 1,
        };
        assert!(validate(&route).is_err());
        route.prefix = "192.0.2.0/33".into();
        route.via = "192.0.2.1".into();
        assert!(validate(&route).is_err());
        route.prefix = "192.0.2.0/24".into();
        route.via = "fe80::1".into();
        assert!(validate(&route).is_err());
    }
}
