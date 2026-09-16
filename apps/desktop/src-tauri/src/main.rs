#![
    cfg_attr(
        not(debug_assertions),
        windows_subsystem = "windows"
    )
]

fn main() {
    trajecta_desktop_lib::run();
}