#[cfg_attr(
    mobile,
    tauri::mobile_entry_point
)]

pub fn run() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_opener::init()
        )
        // Native folder picker for the per-conversation workspace.
        .plugin(
            tauri_plugin_dialog::init()
        )
        .run(
            tauri::generate_context!()
        )
        .expect(
            "error while running Trajecta",
        );
}
