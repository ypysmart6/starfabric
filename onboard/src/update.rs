use crate::atomic_write;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UpdateManifest {
    pub version: String,
    pub target_slot: String,
    pub artifact: String,
    pub sha256: String,
    pub signature: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct PendingUpdate {
    manifest: UpdateManifest,
    previous_slot: String,
}

impl UpdateManifest {
    pub fn signed_bytes(&self) -> Vec<u8> {
        format!(
            "{}\0{}\0{}\0{}",
            self.version, self.target_slot, self.artifact, self.sha256
        )
        .into_bytes()
    }
}

pub struct SlotManager {
    root: PathBuf,
    key: VerifyingKey,
}

impl SlotManager {
    pub fn new(root: impl Into<PathBuf>, public_key_hex: &str) -> io::Result<Self> {
        let bytes = hex::decode(public_key_hex).map_err(invalid)?;
        let key_bytes: [u8; 32] = bytes.try_into().map_err(|_| {
            io::Error::new(io::ErrorKind::InvalidInput, "Ed25519 key must be 32 bytes")
        })?;
        let key = VerifyingKey::from_bytes(&key_bytes).map_err(invalid)?;
        Ok(Self {
            root: root.into(),
            key,
        })
    }

    pub fn stage(&self, manifest: &UpdateManifest, active_slot: &str) -> io::Result<()> {
        if manifest.version.trim().is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "update version is required",
            ));
        }
        if !matches!(manifest.target_slot.as_str(), "a" | "b")
            || manifest.target_slot == active_slot
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "update must target inactive slot a or b",
            ));
        }
        let signature_bytes = hex::decode(&manifest.signature).map_err(invalid)?;
        let signature = Signature::from_slice(&signature_bytes).map_err(invalid)?;
        self.key
            .verify(&manifest.signed_bytes(), &signature)
            .map_err(invalid)?;
        let artifact = std::fs::read(&manifest.artifact)?;
        let digest = hex::encode(Sha256::digest(&artifact));
        if !digest.eq_ignore_ascii_case(&manifest.sha256) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "artifact SHA-256 mismatch",
            ));
        }
        let slot_path = self
            .root
            .join("slots")
            .join(&manifest.target_slot)
            .join("runtime");
        atomic_write(&slot_path, &artifact)?;
        let mut permissions = std::fs::metadata(&slot_path)?.permissions();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            permissions.set_mode(0o755);
            std::fs::set_permissions(&slot_path, permissions)?;
            std::fs::File::open(&slot_path)?.sync_all()?;
        }
        let pending = serde_json::to_vec_pretty(&PendingUpdate {
            manifest: manifest.clone(),
            previous_slot: active_slot.to_owned(),
        })
        .map_err(invalid)?;
        atomic_write(&self.root.join("pending-update.json"), &pending)
    }

    pub fn active_slot(&self) -> io::Result<String> {
        match std::fs::read_to_string(self.root.join("active-slot")) {
            Ok(slot) if matches!(slot.trim(), "a" | "b") => Ok(slot.trim().to_owned()),
            Ok(_) => Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "active-slot marker is invalid",
            )),
            Err(err) if err.kind() == io::ErrorKind::NotFound => Ok("a".to_owned()),
            Err(err) => Err(err),
        }
    }

    fn pending(&self) -> io::Result<PendingUpdate> {
        serde_json::from_slice(&std::fs::read(self.root.join("pending-update.json"))?)
            .map_err(invalid)
    }

    pub fn confirm(&self, target_slot: &str) -> io::Result<String> {
        if !matches!(target_slot, "a" | "b") {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid slot"));
        }
        let pending = self.pending()?;
        if pending.manifest.target_slot != target_slot {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "confirmed slot does not match pending update",
            ));
        }
        let active = self.active_slot()?;
        if active != pending.previous_slot {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "active slot changed after update staging",
            ));
        }
        let staged = self.root.join("slots").join(target_slot).join("runtime");
        let digest = hex::encode(Sha256::digest(std::fs::read(&staged)?));
        if !digest.eq_ignore_ascii_case(&pending.manifest.sha256) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "staged slot SHA-256 changed before confirmation",
            ));
        }
        atomic_write(&self.root.join("previous-active-slot"), active.as_bytes())?;
        atomic_write(&self.root.join("active-slot"), target_slot.as_bytes())?;
        std::fs::remove_file(self.root.join("pending-update.json"))?;
        std::fs::File::open(&self.root)?.sync_all()?;
        Ok(active)
    }

    pub fn rollback(&self) -> io::Result<String> {
        let pending_path = self.root.join("pending-update.json");
        if pending_path.exists() {
            let pending = self.pending()?;
            std::fs::remove_file(&pending_path)?;
            let staged = self
                .root
                .join("slots")
                .join(pending.manifest.target_slot)
                .join("runtime");
            match std::fs::remove_file(staged) {
                Ok(()) => {}
                Err(err) if err.kind() == io::ErrorKind::NotFound => {}
                Err(err) => return Err(err),
            }
            std::fs::File::open(&self.root)?.sync_all()?;
            return self.active_slot();
        }
        let previous_path = self.root.join("previous-active-slot");
        let previous = std::fs::read_to_string(&previous_path)?;
        let previous = previous.trim();
        if !matches!(previous, "a" | "b") {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "previous-active-slot marker is invalid",
            ));
        }
        atomic_write(&self.root.join("active-slot"), previous.as_bytes())?;
        std::fs::remove_file(previous_path)?;
        std::fs::File::open(&self.root)?.sync_all()?;
        Ok(previous.to_owned())
    }

    pub fn restore_active_slot(&self, slot: &str) -> io::Result<()> {
        if !matches!(slot, "a" | "b") {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid slot"));
        }
        atomic_write(&self.root.join("active-slot"), slot.as_bytes())
    }
}

fn invalid(err: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, err.to_string())
}

pub fn read_manifest(path: &Path) -> io::Result<UpdateManifest> {
    serde_json::from_slice(&std::fs::read(path)?).map_err(invalid)
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    #[test]
    fn signed_inactive_slot_is_staged_and_confirmed() {
        let root = std::env::temp_dir().join(format!("starfabric-slots-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let artifact_path = root.join("candidate");
        let artifact = b"signed satellite runtime";
        std::fs::write(&artifact_path, artifact).unwrap();
        let signing = SigningKey::from_bytes(&[7_u8; 32]);
        let mut manifest = UpdateManifest {
            version: "2.0.0".into(),
            target_slot: "b".into(),
            artifact: artifact_path.to_string_lossy().into_owned(),
            sha256: hex::encode(Sha256::digest(artifact)),
            signature: String::new(),
        };
        manifest.signature = hex::encode(signing.sign(&manifest.signed_bytes()).to_bytes());
        let manager =
            SlotManager::new(&root, &hex::encode(signing.verifying_key().as_bytes())).unwrap();
        manager.stage(&manifest, "a").unwrap();
        assert_eq!(
            std::fs::read(root.join("slots/b/runtime")).unwrap(),
            artifact
        );
        assert_eq!(manager.confirm("b").unwrap(), "a");
        assert_eq!(
            std::fs::read_to_string(root.join("active-slot")).unwrap(),
            "b"
        );
        assert!(!root.join("pending-update.json").exists());
        assert_eq!(manager.rollback().unwrap(), "a");
        assert_eq!(
            std::fs::read_to_string(root.join("active-slot")).unwrap(),
            "a"
        );
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn cannot_confirm_a_different_slot() {
        let root =
            std::env::temp_dir().join(format!("starfabric-wrong-slot-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let artifact_path = root.join("candidate");
        let artifact = b"candidate";
        std::fs::write(&artifact_path, artifact).unwrap();
        let signing = SigningKey::from_bytes(&[11_u8; 32]);
        let mut manifest = UpdateManifest {
            version: "2.0.0".into(),
            target_slot: "b".into(),
            artifact: artifact_path.to_string_lossy().into_owned(),
            sha256: hex::encode(Sha256::digest(artifact)),
            signature: String::new(),
        };
        manifest.signature = hex::encode(signing.sign(&manifest.signed_bytes()).to_bytes());
        let manager =
            SlotManager::new(&root, &hex::encode(signing.verifying_key().as_bytes())).unwrap();
        manager.stage(&manifest, "a").unwrap();
        assert!(manager.confirm("a").is_err());
        assert!(root.join("pending-update.json").exists());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn invalid_signature_is_rejected() {
        let root = std::env::temp_dir().join(format!("starfabric-badsig-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let artifact_path = root.join("candidate");
        std::fs::write(&artifact_path, b"candidate").unwrap();
        let signing = SigningKey::from_bytes(&[9_u8; 32]);
        let manifest = UpdateManifest {
            version: "bad".into(),
            target_slot: "b".into(),
            artifact: artifact_path.to_string_lossy().into_owned(),
            sha256: hex::encode(Sha256::digest(b"candidate")),
            signature: "00".repeat(64),
        };
        let manager =
            SlotManager::new(&root, &hex::encode(signing.verifying_key().as_bytes())).unwrap();
        assert!(manager.stage(&manifest, "a").is_err());
        assert!(!root.join("slots/b/runtime").exists());
        std::fs::remove_dir_all(root).unwrap();
    }
}
