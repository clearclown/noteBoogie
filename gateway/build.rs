fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Generate the gRPC client for the Python sidecar from the shared proto.
    // The server side is generated too: integration tests spin up a mock
    // sidecar in-process to drive the background generation loop.
    tonic_prost_build::configure()
        .build_server(true)
        .build_client(true)
        .compile_protos(&["../protos/podcast.proto"], &["../protos"])?;
    Ok(())
}
