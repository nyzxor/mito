//! Tiny CONNECT proxy: internal clients only, no TLS interception.
//! `MITO_EGRESS_BIND` (default 127.0.0.1:18790), `MITO_EGRESS_POLICY`, `MITO_HALT`.

use std::env;
use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;
use std::time::Duration;

use mito_egress_proxy::{halt_refuses_all, host_allowed, is_public_ip, load_policy};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::time::timeout;

#[tokio::main]
async fn main() {
    let bind = env::var("MITO_EGRESS_BIND").unwrap_or_else(|_| "127.0.0.1:18790".into());
    let policy_path = PathBuf::from(
        env::var("MITO_EGRESS_POLICY").unwrap_or_else(|_| "control/egress-proxy.toml".into()),
    );
    let halt_path = PathBuf::from(env::var("MITO_HALT").unwrap_or_else(|_| "control/HALT".into()));
    let listener = TcpListener::bind(&bind)
        .await
        .unwrap_or_else(|e| panic!("bind {bind}: {e}"));
    eprintln!("mito-egress-proxy on {bind}");
    loop {
        let Ok((mut inbound, _)) = listener.accept().await else {
            continue;
        };
        let policy_path = policy_path.clone();
        let halt_path = halt_path.clone();
        tokio::spawn(async move {
            if let Err(e) = handle(&mut inbound, &policy_path, &halt_path).await {
                let _ = inbound
                    .write_all(format!("HTTP/1.1 403 Forbidden\r\n\r\n{e}\n").as_bytes())
                    .await;
            }
        });
    }
}

async fn handle(
    inbound: &mut TcpStream,
    policy_path: &PathBuf,
    halt_path: &PathBuf,
) -> Result<(), String> {
    if halt_refuses_all(halt_path) {
        return Err("HALT".into());
    }
    let policy = load_policy(policy_path)?;
    let mut buf = vec![0u8; 1024];
    let n = timeout(Duration::from_secs(5), inbound.read(&mut buf))
        .await
        .map_err(|_| "read timeout".to_string())?
        .map_err(|e| e.to_string())?;
    let head = std::str::from_utf8(&buf[..n]).map_err(|_| "not utf8")?;
    let line = head.lines().next().unwrap_or("");
    let mut parts = line.split_whitespace();
    let method = parts.next().unwrap_or("");
    let target = parts.next().unwrap_or("");
    if method != "CONNECT" {
        return Err("only CONNECT".into());
    }
    let (host, port) = parse_host_port(target)?;
    host_allowed(&host, port, &policy)?;
    let ips = tokio::net::lookup_host((host.as_str(), port))
        .await
        .map_err(|e| format!("dns: {e}"))?;
    let mut chosen: Option<SocketAddr> = None;
    for addr in ips {
        if is_public_ip(addr.ip()) {
            chosen = Some(addr);
            break;
        } else {
            return Err(format!("SSRF: {}", addr.ip()));
        }
    }
    let dest = chosen.ok_or_else(|| "no public A/AAAA".to_string())?;
    let mut outbound = timeout(Duration::from_secs(10), TcpStream::connect(dest))
        .await
        .map_err(|_| "connect timeout".to_string())?
        .map_err(|e| e.to_string())?;
    inbound
        .write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        .await
        .map_err(|e| e.to_string())?;
    let _ = tokio::io::copy_bidirectional(inbound, &mut outbound).await;
    let _ = dest.ip();
    Ok(())
}

fn parse_host_port(target: &str) -> Result<(String, u16), String> {
    let (h, p) = target.rsplit_once(':').ok_or("host:port required")?;
    let port: u16 = p.parse().map_err(|_| "bad port")?;
    let host = h.trim_matches(|c| c == '[' || c == ']').to_string();
    let _ip: Option<IpAddr> = host.parse().ok();
    Ok((host, port))
}
