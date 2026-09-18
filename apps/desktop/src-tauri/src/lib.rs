#[cfg_attr(
    mobile,
    tauri::mobile_entry_point
)]

use tauri::Manager;

pub fn run() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_opener::init()
        )
        .setup(|app| {
            let window = app
                .get_webview_window("main")
                .expect("failed to get main window");

            window
                .set_icon(
                    tauri::image::Image::from_path(
                        app.path()
                            .resource_directory()
                            .expect("failed to get resource directory")
                            .join("icons/icon.png"),
                    ),
                )
                .expect("failed to set window icon");

            Ok(())
        })
        .run(
            tauri::generate_context!()
        )
        .expect(
            "error while running Trajecta",
        );
}
