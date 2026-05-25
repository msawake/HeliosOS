// SPDX-License-Identifier: BUSL-1.1
//! HTTP client and endpoint discovery.

use anyhow::{anyhow, bail, Context, Result};
use reqwest::blocking::{Client, Response};
use serde::de::DeserializeOwned;
use serde::Serialize;
use serde_json::Value;
use std::time::Duration;

use crate::config;

pub struct Endpoint {
    /// Lazily resolved on first use so subcommands that don't need the
    /// server (like `validate`) can run without a lockfile.
    remote_override: Option<String>,
    token_override: Option<String>,
}

impl Endpoint {
    pub fn resolve(remote: Option<String>, token: Option<String>) -> Self {
        Self {
            remote_override: remote,
            token_override: token,
        }
    }

    fn resolved(&self) -> Result<(String, String)> {
        let lock = config::read_server_lock().ok();
        let base = match (&self.remote_override, &lock) {
            (Some(url), _) => url.trim_end_matches('/').to_string(),
            (None, Some(l)) => format!("http://{}:{}", l.host, l.port),
            (None, None) => bail!(
                "no --remote URL given and no server lockfile at {}",
                config::lock_path().display()
            ),
        };
        let token = match (&self.token_override, &lock) {
            (Some(t), _) => t.clone(),
            (None, Some(l)) => l.token.clone(),
            (None, None) => bail!("no --token given and no server lockfile to read it from"),
        };
        Ok((base, token))
    }
}

fn client() -> Client {
    Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
        .expect("build reqwest client")
}

fn check(resp: Response, method: &str, path: &str) -> Result<Response> {
    if resp.status().is_success() {
        return Ok(resp);
    }
    let status = resp.status();
    let body = resp.text().unwrap_or_default();
    let detail: Option<String> = serde_json::from_str::<Value>(&body)
        .ok()
        .and_then(|v| v.get("detail").and_then(|d| d.as_str().map(String::from)));
    Err(anyhow!(
        "{} {} failed ({}): {}",
        method,
        path,
        status,
        detail.unwrap_or(body)
    ))
}

pub fn get<T: DeserializeOwned>(ep: &Endpoint, path: &str) -> Result<T> {
    let (base, token) = ep.resolved()?;
    let url = format!("{base}{path}");
    let resp = client()
        .get(&url)
        .bearer_auth(&token)
        .send()
        .with_context(|| format!("GET {url}"))?;
    let resp = check(resp, "GET", path)?;
    resp.json::<T>().with_context(|| format!("decode {url}"))
}

pub fn post_json<B: Serialize, T: DeserializeOwned>(
    ep: &Endpoint,
    path: &str,
    body: &B,
) -> Result<T> {
    let (base, token) = ep.resolved()?;
    let url = format!("{base}{path}");
    let resp = client()
        .post(&url)
        .bearer_auth(&token)
        .json(body)
        .send()
        .with_context(|| format!("POST {url}"))?;
    let resp = check(resp, "POST", path)?;
    resp.json::<T>().with_context(|| format!("decode {url}"))
}

pub fn delete<T: DeserializeOwned>(ep: &Endpoint, path: &str) -> Result<T> {
    let (base, token) = ep.resolved()?;
    let url = format!("{base}{path}");
    let resp = client()
        .delete(&url)
        .bearer_auth(&token)
        .send()
        .with_context(|| format!("DELETE {url}"))?;
    let resp = check(resp, "DELETE", path)?;
    resp.json::<T>().with_context(|| format!("decode {url}"))
}

/// `/api/health` is unauthenticated.
pub fn health(ep: &Endpoint) -> Result<Value> {
    let (base, _token) = ep.resolved()?;
    let url = format!("{base}/api/health");
    let resp = client()
        .get(&url)
        .send()
        .with_context(|| format!("GET {url}"))?;
    let resp = check(resp, "GET", "/api/health")?;
    resp.json::<Value>().with_context(|| format!("decode {url}"))
}
