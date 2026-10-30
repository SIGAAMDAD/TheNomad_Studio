// Generated from host_protocol.schema.json; serialization uses exact JSON property names.
using System;
using System.Text.Json;
using System.Text.Json.Serialization;
namespace Nomad.Studio.ToolHost.Contracts;

public sealed record Handshake(
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("protocol_version")] string ProtocolVersion,
    [property: JsonPropertyName("host_nonce")] string HostNonce,
    [property: JsonPropertyName("supported_isolation")] string[] SupportedIsolation
);

public sealed record ToolRun(
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("protocol_version")] string ProtocolVersion,
    [property: JsonPropertyName("operation_id")] string OperationId,
    [property: JsonPropertyName("request_id")] string RequestId,
    [property: JsonPropertyName("project_id")] string ProjectId,
    [property: JsonPropertyName("task_id")] string TaskId,
    [property: JsonPropertyName("worker_id")] string WorkerId,
    [property: JsonPropertyName("shift_id")] string ShiftId,
    [property: JsonPropertyName("lease_generation")] int LeaseGeneration,
    [property: JsonPropertyName("source_revision_id")] string SourceRevisionId,
    [property: JsonPropertyName("canon_snapshot_id")] string CanonSnapshotId,
    [property: JsonPropertyName("package_digest")] string PackageDigest,
    [property: JsonPropertyName("entrypoint_id")] string EntrypointId,
    [property: JsonPropertyName("grant_digest")] string GrantDigest,
    [property: JsonPropertyName("workspace_id")] string WorkspaceId,
    [property: JsonPropertyName("args")] JsonElement Args,
    [property: JsonPropertyName("deadline_ms")] int DeadlineMs,
    [property: JsonPropertyName("input_artifact_digests")] string[] InputArtifactDigests
);

public sealed record ToolObserve(
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("protocol_version")] string ProtocolVersion,
    [property: JsonPropertyName("operation_id")] string OperationId,
    [property: JsonPropertyName("project_id")] string ProjectId
);

public sealed record ToolCancel(
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("protocol_version")] string ProtocolVersion,
    [property: JsonPropertyName("operation_id")] string OperationId,
    [property: JsonPropertyName("project_id")] string ProjectId,
    [property: JsonPropertyName("lease_generation")] int LeaseGeneration,
    [property: JsonPropertyName("request_id")] string RequestId
);

public sealed record ToolReceipt(
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("protocol_version")] string ProtocolVersion,
    [property: JsonPropertyName("operation_id")] string OperationId,
    [property: JsonPropertyName("project_id")] string ProjectId,
    [property: JsonPropertyName("state")] string State,
    [property: JsonPropertyName("lease_generation")] int LeaseGeneration,
    [property: JsonPropertyName("host_identity")] string HostIdentity,
    [property: JsonPropertyName("exit_code")] int? ExitCode,
    [property: JsonPropertyName("started_at")] string? StartedAt,
    [property: JsonPropertyName("ended_at")] string? EndedAt,
    [property: JsonPropertyName("log_digests")] string[] LogDigests,
    [property: JsonPropertyName("output_artifact_digests")] string[] OutputArtifactDigests,
    [property: JsonPropertyName("reason")] string? Reason
);

