use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;

use tauri::{Manager, RunEvent};

/// Holds the spawned Python backend so it can be killed when the app exits.
struct Backend(Mutex<Option<Child>>);

fn backend_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("backend")
}

fn repo_libs() -> PathBuf {
    // src-tauri -> frontend -> app -> Hardware -> libs
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("..")
        .join("libs")
}

/// Point the backend at the repo's KiCad library, unless already overridden.
/// The frozen sidecar can't resolve a repo-relative path on its own.
fn export_env() {
    if std::env::var("HWKIT_LIBS").is_err() {
        std::env::set_var("HWKIT_LIBS", repo_libs());
    }
}

/// Candidate locations for the bundled backend sidecar, most-specific first.
fn sidecar_candidates() -> Vec<PathBuf> {
    let mut v = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            v.push(dir.join("hwkit-backend.exe")); // installed: next to the app exe
        }
    }
    // dev: the externalBin source checked into the repo
    v.push(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("binaries")
            .join("hwkit-backend-x86_64-pc-windows-msvc.exe"),
    );
    v
}

fn spawn_backend() -> Option<Child> {
    export_env();
    // 1. bundled standalone sidecar (portable, no venv needed)
    for sc in sidecar_candidates() {
        if sc.exists() {
            let cwd = sc.parent().map(PathBuf::from).unwrap_or_default();
            if let Ok(child) = Command::new(&sc).current_dir(&cwd).spawn() {
                return Some(child);
            }
        }
    }
    // 2. dev fallback: the backend venv
    let backend = backend_dir();
    let python = backend.join(".venv").join("Scripts").join("python.exe");
    let script = backend.join("run_server.py");
    match Command::new(&python).arg(&script).current_dir(&backend).spawn() {
        Ok(child) => Some(child),
        Err(e) => {
            eprintln!("failed to start backend: {e}");
            None
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            app.manage(Backend(Mutex::new(spawn_backend())));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<Backend>() {
                    if let Some(child) = state.0.lock().unwrap().as_mut() {
                        let _ = child.kill();
                    }
                }
            }
        });
}
