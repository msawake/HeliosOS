// SPDX-License-Identifier: BUSL-1.1
use anyhow::Result;
use clap::Args as ClapArgs;
use serde_json::Value;

use crate::api::{self, Endpoint};
use crate::ui;

#[derive(ClapArgs)]
pub struct Args {
    pub agent_id: String,
}

pub fn run(args: Args, ep: &Endpoint) -> Result<i32> {
    let _resp: Value = api::delete(ep, &format!("/api/platform/agents/{}", args.agent_id))?;
    ui::ok(&format!("Undeployed {}", args.agent_id));
    Ok(0)
}
