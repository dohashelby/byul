# Unix IPC error phases

`unix_ipc/0.1.3` keeps the published `unix-ipc-access-v1` profile and
`guard-unix-ipc-probe/1` packet. Only `EACCES`/`EPERM` raised by the actual
zero-payload `connect` call means `denied`. A permission failure in metadata,
socket creation or peer inspection is `unknown`.

Previously, a failed path recheck after a successful connection could leave
`outcome=connected` without `path_stable`. The strict consumer correctly rejected
the malformed packet, but this also removed a valid connection observation for
an earlier path in that packet. The probe now emits `unknown` for the failed
path, preserving the earlier complete path. A changed path with an available
recheck remains `connected` with `path_stable=false` and cannot establish a
connection violation.

The consumer's packet validation, owner/namespace/PID/start binding, policy,
normal control, independent FAIL and partial-scope UNKNOWN ceiling are unchanged.
The probe still sends no bytes, discovers no new path, parses no message and
uses no privilege escalation. There are no new packet fields or approval values.

Regression covers pre-lookup, connect, peer and post-lookup failures, endpoint
replacement, and the existing PLT02 product path with two explicit test paths.
Linux validation uses two test-owned temporary Unix sockets and actual peer
credentials/EOF with a metadata fault injected after the second connection.
That is an EXAMPLE protocol exercise, not an engine/worker/Kubernetes IPC test.

The current Kubernetes IPC public interface is still unprepared. A-1 must
publish the exact actor/probe distinction, PID/start/UID/groups/namespaces,
socket generations, fixed updated probe source, normal-response correlation,
limits and failure contract before A-2 connects that product consumer. The
proposal in the existing A-2 handoff is not current support. Enterprise policy,
actual worker/message consumption, same-UID unauthorized processes, SHM/TCP,
TP protection and whole IPC inventory remain incomplete.
