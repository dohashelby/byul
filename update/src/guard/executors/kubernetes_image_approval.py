"""SUP-04's opt-in, live local-node image relation and normal control.

The receipt comes from the existing containerd/kernel collector, never from an
operator file or Pod imageID equality. Full package/mutation coverage stays open.
"""
from copy import deepcopy

from guard.models import DataOrigin, ORIGIN_STRENGTH, canonical_json, sha256_hex
from . import node_image_proof
from .container_config import kubernetes_process_control
from .running_image_approval import collect_kubernetes_running_image_approval


def collect(ctx,*,containerd_address=None):
    transport = ctx.cprobe
    snapshots, proof, diagnostics = [], None, []
    node_after = None
    try:
        snapshots.append(transport.snapshot())
        engine = transport.binding['engine']
        node = transport._call(['get', 'node', engine['node'], '-o', 'json'])
        proof = node_image_proof.collect_local_node_image_proof(
            {**transport.binding, 'lab': ctx.target['lab']}, node,
            runner=transport.runner if transport.injected else None,
            timeout=min(30, transport.deadline - transport.clock()),
            **({'containerd_address':containerd_address} if containerd_address is not None else {}))
        node_after = transport._call(['get', 'node', engine['node'], '-o', 'json'])
    except Exception as error:
        diagnostics.append(node_image_proof.failure_code(error))
    try:
        snapshots.append(transport.snapshot())
    except Exception:
        snapshots.append({})
    if len(snapshots) < 2:
        snapshots.insert(0, {})
    observations, normal, challenges, summary = collect_kubernetes_running_image_approval(
        ctx, snapshots[0], snapshots[1])
    analysis = summary['kubernetes_image']
    verified, receipt = False, None
    try:
        if (not analysis['api_identity_stable']
                or type(proof) is not node_image_proof.NodeImageProof
                or not proof.matches(transport.binding['engine'])):
            raise ValueError('NODE_IMAGE_PROOF_UNVERIFIED')
        receipt = proof.public_receipt()
        engine = transport.binding['engine']
        selected_digest = node_image_proof.image_digest(engine['image'])
        if (node_image_proof.validate_node(node_after, engine['node'], node_image_proof.kernel_identity()) != receipt['node']
                or receipt['schema'] != 'guard-node-image-proof/1'
                or receipt['profile'] != node_image_proof.PROFILE
                or receipt.get('runtime_endpoint','/run/containerd/containerd.sock') !=
                    (containerd_address or '/run/containerd/containerd.sock')
                or not all(receipt[key] is True for key in
                    ('image_chain_verified', 'snapshot_chain_verified', 'live_root_mount_verified'))
                # A runtime index may enclose the exact approved platform manifest.
                or selected_digest not in (receipt['root_digest'], receipt['manifest_digest'])):
            raise ValueError('NODE_IMAGE_PROOF_UNVERIFIED')
        verified = True
    except Exception:
        diagnostics.append('NODE_IMAGE_PROOF_UNVERIFIED')
        receipt = None

    if verified:
        actual = transport.binding['engine']['image']
        approved = actual in ctx.policy_values['approved_image_digests']
        observations.update({
            'running_image_identity_verified': {'value': True, 'collected': True},
            'running_image_repo_digest': {'value': actual, 'collected': True},
            'running_image_approved': {'value': approved, 'collected': True},
            'running_image_unapproved': {'value': not approved, 'collected': True},
        })
        functional, normal = kubernetes_process_control(ctx)
        observations.update(functional)
        # This covers image identity only. The package/entrypoint and deployment
        # mutation challenge remains unattempted, including on approved images.
        challenges[0]['attempted'] = True
        challenges[0]['observations']['scope'] = 'owned live containerd image ancestry only'
        analysis.update(runtime_image_relation_verified=True,
            interface_status='LIVE_NODE_IMAGE_CONSUMER',consumer_version='0.2.0',
            diagnostic_codes=[], node_image_proof_sha256=sha256_hex(canonical_json(receipt)))
        summary['not_exercised'] = [x for x in summary['not_exercised']
            if x not in ('runtime_repo_digest_relation', 'functional_inference')]
        summary['node_image_proof'] = deepcopy(receipt)
        ctx._kubernetes_image_data_origin = min(
            DataOrigin(transport.process_observation_origin), DataOrigin(receipt['data_origin']),
            key=lambda origin: ORIGIN_STRENGTH[origin])
    else:
        ctx._kubernetes_image_data_origin = DataOrigin(transport.process_observation_origin)
    summary['collection_errors'] = sorted(set(diagnostics + analysis['diagnostic_codes']))
    summary['dispatch'] = {'connected': True, 'scope': 'local-containerd-live-image-only',
        'runtime_image_approval_connected': verified, 'whole_run_coverage_verified': False}
    summary['limitations'] = [
        'The opt-in collector binds the live local containerd image, snapshot and kernel root mount.',
        'A verified approved image is not full package, entrypoint, mounted-code or deployment-gate challenge coverage.',
        'Collector provenance is preserved; injected, lab or declared receipts are not promoted to MEASURED.',
    ]
    summary['kubernetes'] = transport.report()
    return observations, normal, challenges, summary
