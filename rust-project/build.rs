use std::process::Command;

fn main() {
    // Generate a build timestamp in ISO 8601 format.
    // Try the `date` command first; fall back to a placeholder if unavailable.
    let timestamp = Command::new("date")
        .arg("--iso-8601=seconds")
        .output()
        .ok()
        .and_then(|o| {
            if o.status.success() {
                String::from_utf8(o.stdout).ok().map(|s| s.trim().to_string())
            } else {
                None
            }
        })
        .unwrap_or_else(|| "unknown".to_string());

    println!("cargo:rustc-env=BUILD_TIMESTAMP={}", timestamp);
}
