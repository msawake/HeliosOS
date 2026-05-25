// SPDX-License-Identifier: BUSL-1.1
//! `forgeos config <subcommand>` — manage ~/.forgeos/.

use anyhow::Result;
use clap::Subcommand;

use crate::config;
use crate::ui;

#[derive(Subcommand)]
pub enum ConfigCmd {
    /// Print config + credentials (values redacted).
    View,
    /// Show the active profile.
    CurrentProfile,
    /// Switch the active profile.
    UseProfile {
        name: String,
    },
    /// Store a credential under the current (or specified) profile.
    SetCredential {
        name: String,
        value: String,
        #[arg(long)]
        profile: Option<String>,
    },
    /// Print a stored credential.
    GetCredential {
        name: String,
        #[arg(long)]
        profile: Option<String>,
    },
    /// Delete a stored credential.
    DeleteCredential {
        name: String,
        #[arg(long)]
        profile: Option<String>,
    },
    /// List credential names in a profile.
    ListCredentials {
        #[arg(long)]
        profile: Option<String>,
    },
}

pub fn run(cmd: ConfigCmd) -> Result<i32> {
    match cmd {
        ConfigCmd::View => view(),
        ConfigCmd::CurrentProfile => {
            println!("{}", config::current_profile()?);
            Ok(0)
        }
        ConfigCmd::UseProfile { name } => {
            config::set_current_profile(&name)?;
            ui::ok(&format!("Active profile is now {name:?}"));
            Ok(0)
        }
        ConfigCmd::SetCredential {
            name,
            value,
            profile,
        } => {
            config::set_credential(&name, &value, profile.as_deref())?;
            let p = match profile {
                Some(p) => p,
                None => config::current_profile()?,
            };
            ui::ok(&format!("Stored credential {name:?} in profile {p:?}"));
            Ok(0)
        }
        ConfigCmd::GetCredential { name, profile } => match config::get_credential(&name, profile.as_deref())? {
            Some(v) => {
                println!("{v}");
                Ok(0)
            }
            None => {
                ui::err(&format!("Credential {name:?} not found"));
                Ok(1)
            }
        },
        ConfigCmd::DeleteCredential { name, profile } => {
            let removed = config::delete_credential(&name, profile.as_deref())?;
            if removed {
                let p = match profile {
                    Some(p) => p,
                    None => config::current_profile()?,
                };
                ui::ok(&format!("Deleted credential {name:?} from profile {p:?}"));
                Ok(0)
            } else {
                ui::err(&format!("Credential {name:?} not found"));
                Ok(1)
            }
        }
        ConfigCmd::ListCredentials { profile } => {
            let names = config::list_credentials(profile.as_deref())?;
            let p = match &profile {
                Some(p) => p.clone(),
                None => config::current_profile()?,
            };
            if names.is_empty() {
                println!("# no credentials in profile {p:?}");
            } else {
                println!("# profile: {p}");
                for n in names {
                    println!("{n}");
                }
            }
            Ok(0)
        }
    }
}

fn view() -> Result<i32> {
    let cfg = config::load_config()?;
    let creds = config::load_credentials()?;
    let redacted: std::collections::BTreeMap<&String, std::collections::BTreeMap<&String, &str>> =
        creds
            .iter()
            .map(|(p, bucket)| {
                (
                    p,
                    bucket.iter().map(|(k, _)| (k, "***")).collect(),
                )
            })
            .collect();
    println!("# {}", config::config_path().display());
    println!("{}", serde_yaml::to_string(&cfg)?);
    println!("# {} (values redacted)", config::credentials_path().display());
    println!("{}", serde_yaml::to_string(&redacted)?);
    Ok(0)
}
