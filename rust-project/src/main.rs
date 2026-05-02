/// Build timestamp injected at compile time.
const BUILD_TIMESTAMP: &str = env!("BUILD_TIMESTAMP");

/// Version string from Cargo.toml.
const VERSION: &str = env!("CARGO_PKG_VERSION");

fn main() {
    println!("attested-hello v{}", VERSION);
    println!("Built at: {}", BUILD_TIMESTAMP);
}
