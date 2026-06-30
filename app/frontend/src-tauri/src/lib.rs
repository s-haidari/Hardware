use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;

use tauri::{Manager, RunEvent};

/// Holds the spawned Python backend so it can be killed when the app exits.
struct Backend(Mutex<Option<Child>>);

/// Resolve the backend dir relative to this crate (app/frontend/src-tauri).
fn backend_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("backend")
}

fn spawn_backend() -> Option<Child> {
    let backend = backend_dir();
    let python = backend.join(".venv").join("Scripts").join("python.exe");
    let script = backend.join("run_server.py");
    match Command::new(&python)
        .arg(&script)
        .current_dir(&backend)
        .spawn()
    {
        Ok(child) => Some(child),
        Err(e) => {
            eprintln!("failed to start backend ({python:?}): {e}");
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
