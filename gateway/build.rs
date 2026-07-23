fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Generate the gRPC client for the Python sidecar from the shared proto.
    tonic_prost_build::configure()
        .build_server(false)
        .build_client(true)
        .compile_protos(&["../protos/podcast.proto"], &["../protos"])?;
    Ok(())
}
