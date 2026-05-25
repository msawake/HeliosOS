// SPDX-License-Identifier: BUSL-1.1
//! `forgeos deploy <file>` — POST the manifest to the server.
//!
//! We send the raw YAML/JSON body so the server's `AgentManifest.from_dict`
//! does the heavy parsing. This keeps the Rust side schema-light: any
//! manifest the Python platform accepts works through the Rust client.

use anyhow::{bail, Context, Result};
use clap::Args as ClapArgs;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

use crate::api::{self, Endpoint};
use crate::ui;

#[derive(ClapArgs)]
pub struct Args {
    /// Path to agent.yaml or agent.json
    pub file: PathBuf,
}

#[derive(Serialize)]
struct DeployRequest {
    manifest_yaml: String,
}

#[derive(Deserialize)]
struct DeployResponse {
    agent_id: String,
}

pub fn run(args: Args, ep: &Endpoint) -> Result<i32> {
    let raw = std::fs::read_to_string(&args.file)
        .with_context(|| format!("read {}", args.file.display()))?;

    // Refuse Team manifests for now — the server endpoint isn't exposed.
    if raw.contains("\nkind: Team") || raw.starts_with("kind: Team") {
        bail!(
            "team manifests are not yet supported by the Rust CLI; split into individual agents"
        );
    }

    let resp: DeployResponse = api::post_json(
        ep,
        "/api/platform/agents",
        &DeployRequest { manifest_yaml: raw },
    )?;
    ui::ok(&format!("Deployed agent: {}", resp.agent_id));
    Ok(0)
}
