use axum::{
    Json, Router,
    extract::State,
    http::StatusCode,
    routing::{get, post},
};
use clap::Parser;
use satellite_node_runtime::{
    routing,
    state::{NodeState, StateStore, now_ms},
    update::{SlotManager, read_manifest},
};
use serde::{Deserialize, Serialize};
use std::{path::PathBuf, sync::Arc, time::Duration};
use tokio::sync::RwLock;
use tracing::{error, info, warn};

#[derive(Parser, Debug)]
struct Args {
    #[arg(long, default_value = "127.0.0.1:9080")]
    listen: String,
    #[arg(long, default_value = "/var/lib/starfabric-node")]
    state_dir: PathBuf,
    #[arg(long)]
    config: PathBuf,
    #[arg(long, default_value_t = 30)]
    disconnect_hold_seconds: u64,
    /// Expire a connected state when ground heartbeats stop (disabled if omitted).
    #[arg(long, value_parser = clap::value_parser!(u64).range(1..))]
    heartbeat_timeout_seconds: Option<u64>,
    #[arg(long)]
    dry_run_routes: bool,
}

#[derive(Clone, Deserialize)]
struct Config {
    node_id: String,
    update_public_key_hex: String,
    #[serde(default)]
    fallback_routes: Vec<routing::FallbackRoute>,
    #[serde(default)]
    fallback_targets: Vec<routing::FallbackTarget>,
}

#[derive(Clone)]
struct Runtime {
    state: Arc<RwLock<NodeState>>,
    store: StateStore,
    config: Config,
    slots: Arc<SlotManager>,
    dry_run_routes: bool,
}

#[derive(Deserialize)]
struct Connectivity {
    connected: bool,
    generation: u64,
}
#[derive(Deserialize)]
struct Fault {
    mode: Option<String>,
}
#[derive(Deserialize)]
struct UpdateRequest {
    manifest_path: PathBuf,
}
#[derive(Deserialize)]
struct ConfirmRequest {
    slot: String,
}
#[derive(Serialize)]
struct Reply {
    status: &'static str,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let args = Args::parse();
    let config: Config = serde_json::from_slice(&std::fs::read(&args.config)?)?;
    for route in &config.fallback_routes {
        routing::validate(route)?;
    }
    for target in &config.fallback_targets {
        routing::validate_target(target)?;
    }
    std::fs::create_dir_all(&args.state_dir)?;
    let store = StateStore::new(args.state_dir.join("state.json"));
    let slots = Arc::new(SlotManager::new(
        &args.state_dir,
        &config.update_public_key_hex,
    )?);
    let mut loaded = store.load()?;
    let marker_slot = slots.active_slot()?;
    if loaded.active_slot != marker_slot {
        loaded.active_slot = marker_slot;
        store.save(&loaded)?;
    }
    if loaded.fallback_active {
        // A reboot clears volatile kernel routes while durable state survives.
        // Reconstruct the recorded safe mode before accepting management calls.
        loaded.fallback_routes = desired_fallback(&config)?;
        routing::apply(&loaded.fallback_routes, args.dry_run_routes)?;
        store.save(&loaded)?;
    }
    let state = Arc::new(RwLock::new(loaded));
    let runtime = Runtime {
        state: state.clone(),
        store: store.clone(),
        config: config.clone(),
        slots,
        dry_run_routes: args.dry_run_routes,
    };
    let _ = sd_notify::notify(true, &[sd_notify::NotifyState::Ready]);
    tokio::spawn(autonomy_loop(
        runtime.clone(),
        args.disconnect_hold_seconds,
        args.heartbeat_timeout_seconds,
        args.dry_run_routes,
    ));
    tokio::spawn(watchdog_loop());
    let app = Router::new()
        .route("/healthz", get(health))
        .route("/readyz", get(ready))
        .route("/v1/status", get(status))
        .route("/v1/connectivity", post(connectivity))
        .route("/v1/faults", post(fault))
        .route("/v1/update/stage", post(stage_update))
        .route("/v1/update/confirm", post(confirm_update))
        .route("/v1/update/rollback", post(rollback_update))
        .with_state(runtime);
    let listener = tokio::net::TcpListener::bind(&args.listen).await?;
    info!(
        node_id = config.node_id,
        address = args.listen,
        "satellite node runtime ready"
    );
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown())
        .await?;
    Ok(())
}

async fn health(State(rt): State<Runtime>) -> StatusCode {
    if rt.state.read().await.healthy {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    }
}
async fn ready(State(rt): State<Runtime>) -> StatusCode {
    let state = rt.state.read().await;
    if state.healthy && state.injected_fault.as_deref() != Some("process") {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    }
}
async fn status(State(rt): State<Runtime>) -> Json<NodeState> {
    Json(rt.state.read().await.clone())
}
async fn connectivity(
    State(rt): State<Runtime>,
    Json(req): Json<Connectivity>,
) -> Result<Json<Reply>, (StatusCode, String)> {
    let mut state = rt.state.write().await;
    if req.generation <= state.generation {
        return Err((StatusCode::CONFLICT, "stale or duplicate generation".into()));
    }
    let previous = state.clone();
    let removed_fallback = req.connected && state.fallback_active;
    if removed_fallback {
        routing::remove(&state.fallback_routes, rt.dry_run_routes).map_err(internal)?;
    }
    state.generation = req.generation;
    state.connected = req.connected;
    state.last_contact_unix_ms = now_ms();
    if req.connected {
        state.fallback_active = false;
        state.fallback_routes.clear();
    }
    if let Err(err) = rt.store.save(&state) {
        *state = previous;
        let restore = if removed_fallback {
            routing::apply(&state.fallback_routes, rt.dry_run_routes).err()
        } else {
            None
        };
        return Err(internal(match restore {
            Some(restore) => format!("{err}; restore fallback routes: {restore}"),
            None => err.to_string(),
        }));
    }
    Ok(Json(Reply { status: "accepted" }))
}
async fn fault(
    State(rt): State<Runtime>,
    Json(req): Json<Fault>,
) -> Result<Json<Reply>, (StatusCode, String)> {
    let mut state = rt.state.write().await;
    state.injected_fault = req.mode;
    state.healthy = state.injected_fault.as_deref() != Some("process");
    rt.store.save(&state).map_err(internal)?;
    Ok(Json(Reply { status: "accepted" }))
}
async fn stage_update(
    State(rt): State<Runtime>,
    Json(req): Json<UpdateRequest>,
) -> Result<Json<Reply>, (StatusCode, String)> {
    let manifest = read_manifest(&req.manifest_path).map_err(internal)?;
    let active = rt.state.read().await.active_slot.clone();
    rt.slots.stage(&manifest, &active).map_err(internal)?;
    Ok(Json(Reply { status: "staged" }))
}
async fn confirm_update(
    State(rt): State<Runtime>,
    Json(req): Json<ConfirmRequest>,
) -> Result<Json<Reply>, (StatusCode, String)> {
    let previous_slot = rt.slots.confirm(&req.slot).map_err(internal)?;
    let mut state = rt.state.write().await;
    let previous_state = state.clone();
    state.active_slot = req.slot;
    if let Err(err) = rt.store.save(&state) {
        *state = previous_state;
        let restore = rt.slots.restore_active_slot(&previous_slot).err();
        return Err(internal(match restore {
            Some(restore) => format!("{err}; restore active slot: {restore}"),
            None => err.to_string(),
        }));
    }
    Ok(Json(Reply {
        status: "confirmed",
    }))
}
async fn rollback_update(State(rt): State<Runtime>) -> Result<Json<Reply>, (StatusCode, String)> {
    let mut state = rt.state.write().await;
    let previous_slot = state.active_slot.clone();
    let active_slot = rt.slots.rollback().map_err(internal)?;
    state.active_slot = active_slot;
    if let Err(err) = rt.store.save(&state) {
        state.active_slot = previous_slot.clone();
        let restore = rt.slots.restore_active_slot(&previous_slot).err();
        return Err(internal(match restore {
            Some(restore) => format!("{err}; restore active slot: {restore}"),
            None => err.to_string(),
        }));
    }
    Ok(Json(Reply {
        status: "rolled_back",
    }))
}

fn desired_fallback(config: &Config) -> std::io::Result<Vec<routing::FallbackRoute>> {
    if config.fallback_targets.is_empty() { Ok(config.fallback_routes.clone()) }
    else { routing::resolve(&config.fallback_targets) }
}

async fn autonomy_loop(rt: Runtime, hold_seconds: u64, heartbeat_seconds: Option<u64>, dry_run: bool) {
    let hold_ms = hold_seconds.saturating_mul(1000);
    let heartbeat_ms = heartbeat_seconds.map(|seconds| seconds.saturating_mul(1000));
    let mut ticker = tokio::time::interval(Duration::from_secs(1));
    loop {
        ticker.tick().await;
        // Serialize the decision, kernel route mutation and durable state with
        // incoming heartbeats; otherwise reconnect can race with route apply.
        let mut state = rt.state.write().await;
        let elapsed = now_ms().saturating_sub(state.last_contact_unix_ms);
        let expired = heartbeat_ms.is_some_and(|timeout| elapsed >= timeout);
        if (!state.connected || expired) && elapsed >= hold_ms {
            let desired = match desired_fallback(&rt.config) {
                Ok(routes) => routes,
                Err(err) => { error!(%err, "fallback gateway lookup failed"); continue; }
            };
            if state.fallback_active && state.fallback_routes == desired { continue; }
            if let Err(err) = routing::transition(&state.fallback_routes, &desired, dry_run) {
                error!(%err, "fallback route apply failed");
                continue;
            }
            let previous = state.clone();
            state.connected = false;
            state.fallback_active = true;
            state.fallback_routes = desired.clone();
            if let Err(err) = rt.store.save(&state) {
                *state = previous;
                let remove_error = routing::transition(&desired, &state.fallback_routes, dry_run).err();
                error!(%err, ?remove_error, "persist fallback state failed; routes restored to pre-transition state");
                continue;
            }
            warn!("controller hold expired; local fallback routes activated");
        }
    }
}
async fn watchdog_loop() {
    let mut ticker = tokio::time::interval(Duration::from_secs(5));
    loop {
        ticker.tick().await;
        let _ = sd_notify::notify(false, &[sd_notify::NotifyState::Watchdog]);
    }
}
async fn shutdown() {
    let _ = tokio::signal::ctrl_c().await;
    let _ = sd_notify::notify(false, &[sd_notify::NotifyState::Stopping]);
}
fn internal(err: impl std::fmt::Display) -> (StatusCode, String) {
    (StatusCode::INTERNAL_SERVER_ERROR, err.to_string())
}
