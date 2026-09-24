//! Physics-layer checks for the CONNECT proxy (ADR-0005).
//! Resolve once, reject non-public IPs, honour HALT, host allow/deny.

use std::fs;
use std::net::IpAddr;
use std::path::Path;

use serde::Deserialize;

#[derive(Debug, Deserialize, Default)]
pub struct ProxyPolicy {
    #[serde(default)]
    pub deny_hosts: Vec<String>,
    #[serde(default)]
    pub allow_hosts: Vec<String>,
    #[serde(default = "default_ports")]
    pub allow_ports: Vec<u16>,
}

fn default_ports() -> Vec<u16> {
    vec![80, 443]
}

pub fn load_policy(path: &Path) -> Result<ProxyPolicy, String> {
    let raw = fs::read_to_string(path).map_err(|e| format!("policy: {e}"))?;
    toml::from_str(&raw).map_err(|e| format!("policy toml: {e}"))
}

pub fn halt_refuses_all(halt_path: &Path) -> bool {
    halt_path.exists()
}

pub fn is_public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v) => {
            !(v.is_loopback()
                || v.is_private()
                || v.is_link_local()
                || v.is_broadcast()
                || v.is_unspecified()
                || v.is_multicast()
                || v.octets()[0] == 169 && v.octets()[1] == 254)
        }
        IpAddr::V6(v) => !(v.is_loopback() || v.is_unique_local() || v.is_unicast_link_local() || v.is_multicast()),
    }
}

pub fn host_allowed(host: &str, port: u16, policy: &ProxyPolicy) -> Result<(), String> {
    let host = host.to_ascii_lowercase();
    if !policy.allow_ports.is_empty() && !policy.allow_ports.contains(&port) {
        return Err(format!("port {port} not allowed"));
    }
    if policy.deny_hosts.iter().any(|h| h.eq_ignore_ascii_case(&host)) {
        return Err(format!("host {host} denylisted"));
    }
    if !policy.allow_hosts.is_empty()
        && !policy.allow_hosts.iter().any(|h| h.eq_ignore_ascii_case(&host))
    {
        return Err(format!("host {host} not on allow_hosts"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::Ipv4Addr;

    #[test]
    fn rfc1918_and_metadata_are_not_public() {
        assert!(!is_public_ip(IpAddr::V4(Ipv4Addr::new(10, 0, 0, 1))));
        assert!(!is_public_ip(IpAddr::V4(Ipv4Addr::new(192, 168, 1, 1))));
        assert!(!is_public_ip(IpAddr::V4(Ipv4Addr::new(127, 0, 0, 1))));
        assert!(!is_public_ip(IpAddr::V4(Ipv4Addr::new(169, 254, 169, 254))));
        assert!(is_public_ip(IpAddr::V4(Ipv4Addr::new(1, 1, 1, 1))));
    }

    #[test]
    fn deny_hosts_win() {
        let p = ProxyPolicy {
            deny_hosts: vec!["evil.example".into()],
            allow_hosts: vec![],
            allow_ports: vec![443],
        };
        assert!(host_allowed("evil.example", 443, &p).is_err());
        assert!(host_allowed("example.org", 443, &p).is_ok());
        assert!(host_allowed("example.org", 8080, &p).is_err());
    }
}
