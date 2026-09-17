use crate::atomic_write;
use serde::{Deserialize, Serialize};
use std::io;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct NodeState {
    pub generation: u64,
    pub connected: bool,
    pub fallback_active: bool,
    pub healthy: bool,
    pub active_slot: String,
    pub last_contact_unix_ms: u64,
    pub injected_fault: Option<String>,
    #[serde(default)]
    pub fallback_routes: Vec<crate::routing::FallbackRoute>,
}

impl Default for NodeState {
    fn default() -> Self {
        Self {
            generation: 1,
            connected: false,
            fallback_active: false,
            healthy: true,
            active_slot: "a".into(),
            last_contact_unix_ms: now_ms(),
            injected_fault: None,
            fallback_routes: Vec::new(),
        }
    }
}

#[derive(Clone)]
pub struct StateStore {
    path: PathBuf,
}

impl StateStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    pub fn load(&self) -> io::Result<NodeState> {
        match std::fs::read(&self.path) {
            Ok(data) => serde_json::from_slice(&data)
                .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err)),
            Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(NodeState::default()),
            Err(err) => Err(err),
        }
    }

    pub fn save(&self, state: &NodeState) -> io::Result<()> {
        let encoded = serde_json::to_vec_pretty(state)
            .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err))?;
        atomic_write(Path::new(&self.path), &encoded)
    }
}

pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn state_round_trip() {
        let path =
            std::env::temp_dir().join(format!("starfabric-state-{}.json", std::process::id()));
        let store = StateStore::new(&path);
        let expected = NodeState {
            generation: 42,
            ..NodeState::default()
        };
        store.save(&expected).unwrap();
        assert_eq!(store.load().unwrap(), expected);
        std::fs::remove_file(path).unwrap();
    }
}
