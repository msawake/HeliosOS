// SPDX-License-Identifier: BUSL-1.1
use anyhow::Result;
use clap::Args as ClapArgs;
use serde::Serialize;
use serde_json::Value;

use crate::api::{self, Endpoint};
use crate::ui;

#[derive(ClapArgs)]
pub struct Args {
    pub agent_id: String,
    pub prompt: String,
}

#[derive(Serialize)]
struct InvokeRequest<'a> {
    prompt: &'a str,
    context: Value,
}

pub fn run(args: Args, ep: &Endpoint) -> Result<i32> {
    let path = format!("/api/platform/agents/{}/invoke", args.agent_id);
    let result: Value = api::post_json(
        ep,
        &path,
        &InvokeRequest {
            prompt: &args.prompt,
            context: Value::Object(Default::default()),
        },
    )?;
    println!("{}", serde_json::to_string_pretty(&result)?);
    if let Some(warnings) = result.get("warnings").and_then(|v| v.as_array()) {
        for w in warnings {
            if let Some(s) = w.as_str() {
                ui::warn(s);
            }
        }
    }
    Ok(0)
}
