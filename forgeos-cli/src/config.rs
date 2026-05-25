// SPDX-License-Identifier: BUSL-1.1
//! ~/.forgeos/ config + credentials store (kubectl-style).
//!
//! Mirrors the Python `src/forgeos_sdk/config_store.py` so the Rust CLI
//! and any remaining Python tooling share the same files. Credentials
//! live in `~/.forgeos/credentials` at mode 0600; refusing to read the
//! file if it's group/other-readable matches the Python behaviour.

use anyhow::{anyhow, bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

pub fn config_dir() -> PathBuf {
    if let Ok(override_dir) = std::env::var("FORGEOS_CONFIG_DIR") {
        return PathBuf::from(override_dir);
    }
    dirs::home_dir().expect("no home directory").join(".forgeos")
}

pub fn config_path() -> PathBuf {
    config_dir().join("config.yaml")
}

pub fn credentials_path() -> PathBuf {
    config_dir().join("credentials")
}

pub fn lock_path() -> PathBuf {
    config_dir().join("server.lock")
}

fn ensure_dir() -> Result<()> {
    let d = config_dir();
    fs::create_dir_all(&d).with_context(|| format!("create {}", d.display()))?;
    #[cfg(unix)]
    {
        let _ = fs::set_permissions(&d, fs::Permissions::from_mode(0o700));
    }
    Ok(())
}

// ---- config.yaml ----------------------------------------------------------

pub type Config = BTreeMap<String, serde_yaml::Value>;

pub fn load_config() -> Result<Config> {
    let path = config_path();
    if !path.exists() {
        return Ok(BTreeMap::new());
    }
    let raw = fs::read_to_string(&path).with_context(|| format!("read {}", path.display()))?;
    if raw.trim().is_empty() {
        return Ok(BTreeMap::new());
    }
    let parsed: Config = serde_yaml::from_str(&raw)
        .with_context(|| format!("parse YAML in {}", path.display()))?;
    Ok(parsed)
}

pub fn save_config(data: &Config) -> Result<()> {
    ensure_dir()?;
    let yaml = serde_yaml::to_string(data)?;
    let path = config_path();
    fs::write(&path, yaml).with_context(|| format!("write {}", path.display()))?;
    Ok(())
}

pub fn current_profile() -> Result<String> {
    let cfg = load_config()?;
    match cfg.get("current_profile") {
        Some(serde_yaml::Value::String(s)) => Ok(s.clone()),
        _ => Ok("default".to_string()),
    }
}

pub fn set_current_profile(name: &str) -> Result<()> {
    let mut cfg = load_config()?;
    cfg.insert(
        "current_profile".to_string(),
        serde_yaml::Value::String(name.to_string()),
    );
    save_config(&cfg)
}

// ---- credentials ----------------------------------------------------------

pub type Credentials = BTreeMap<String, BTreeMap<String, String>>;

fn check_credentials_permissions() -> Result<()> {
    let path = credentials_path();
    if !path.exists() {
        return Ok(());
    }
    #[cfg(unix)]
    {
        let mode = fs::metadata(&path)?.permissions().mode() & 0o777;
        let bad = mode & 0o077;
        if bad != 0 {
            bail!(
                "{} has insecure permissions {:#o}. Run: chmod 600 {}",
                path.display(),
                mode,
                path.display()
            );
        }
    }
    Ok(())
}

pub fn load_credentials() -> Result<Credentials> {
    check_credentials_permissions()?;
    let path = credentials_path();
    if !path.exists() {
        return Ok(BTreeMap::new());
    }
    let raw = fs::read_to_string(&path).with_context(|| format!("read {}", path.display()))?;
    if raw.trim().is_empty() {
        return Ok(BTreeMap::new());
    }
    let parsed: Credentials = serde_yaml::from_str(&raw)
        .with_context(|| format!("parse YAML in {}", path.display()))?;
    Ok(parsed)
}

pub fn save_credentials(data: &Credentials) -> Result<()> {
    ensure_dir()?;
    let yaml = serde_yaml::to_string(data)?;
    let path = credentials_path();
    fs::write(&path, yaml).with_context(|| format!("write {}", path.display()))?;
    #[cfg(unix)]
    {
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

pub fn get_credential(name: &str, profile: Option<&str>) -> Result<Option<String>> {
    let profile_name = match profile {
        Some(p) => p.to_string(),
        None => current_profile()?,
    };
    let creds = load_credentials()?;
    Ok(creds
        .get(&profile_name)
        .and_then(|bucket| bucket.get(name))
        .cloned())
}

pub fn set_credential(name: &str, value: &str, profile: Option<&str>) -> Result<()> {
    let profile_name = match profile {
        Some(p) => p.to_string(),
        None => current_profile()?,
    };
    let mut creds = load_credentials()?;
    creds
        .entry(profile_name)
        .or_default()
        .insert(name.to_string(), value.to_string());
    save_credentials(&creds)
}

pub fn delete_credential(name: &str, profile: Option<&str>) -> Result<bool> {
    let profile_name = match profile {
        Some(p) => p.to_string(),
        None => current_profile()?,
    };
    let mut creds = load_credentials()?;
    let removed = creds
        .get_mut(&profile_name)
        .and_then(|bucket| bucket.remove(name))
        .is_some();
    if removed {
        save_credentials(&creds)?;
    }
    Ok(removed)
}

pub fn list_credentials(profile: Option<&str>) -> Result<Vec<String>> {
    let profile_name = match profile {
        Some(p) => p.to_string(),
        None => current_profile()?,
    };
    let creds = load_credentials()?;
    Ok(match creds.get(&profile_name) {
        Some(bucket) => {
            let mut names: Vec<_> = bucket.keys().cloned().collect();
            names.sort();
            names
        }
        None => Vec::new(),
    })
}

// ---- server.lock ----------------------------------------------------------

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct ServerLock {
    pub host: String,
    pub port: u16,
    pub token: String,
    #[serde(default)]
    pub pid: Option<u32>,
}

pub fn read_server_lock() -> Result<ServerLock> {
    let path = lock_path();
    let raw = fs::read_to_string(&path).with_context(|| {
        format!(
            "no server lock at {} — is `forgeos-server` running?",
            path.display()
        )
    })?;
    let lock: ServerLock = serde_json::from_str(&raw)
        .map_err(|e| anyhow!("malformed {}: {}", path.display(), e))?;
    Ok(lock)
}
