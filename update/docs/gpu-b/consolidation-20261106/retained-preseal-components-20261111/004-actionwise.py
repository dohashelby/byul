"""SUP16 action boundaries over the fixed outer/adapter preparation.

S2 supplies the real original phase supervisor and terminal verifier. No
resource, proof, phase start, or deadline is invented by this module.
"""
import hashlib
import math
from pathlib import Path

OUTER_SHA = '059fb04cc8e2eae69d9633b14c2e6e0b501382aff3241e69ce7b29c4dcd5b988'
ADAPTER_SHA = '7d839a0a0a8385822a9d8262739e0c89093241262c8e638deb3cc425f01876fc'
ACTIONS = (('preserve', 300), ('quiesce', 360), ('archive', 300))
SCHEMA = 'byul-sup16-original-action-receipt/1'


def require(condition, code):
    if not condition:
        raise ValueError(code)


def first_error(original, later, label):
    if original is None:
        return later
    if later is not original:
        original.add_note(label + ':' + type(later).__name__ + ':' + str(later))
    return original


def phase_deadline(phase, action, budget):
    require(phase['name'] == action and phase['budget_seconds'] == budget,
            'SUP16_ACTION_ORIGINAL_BUDGET_REQUIRED')
    values = [phase['phase_started_monotonic'], phase['total_deadline_monotonic'],
              *phase['additional_deadlines_monotonic']]
    require(all(type(v) in (int, float) and math.isfinite(v) for v in values),
            'SUP16_ACTION_ORIGINAL_FINITE_ANCHORS_REQUIRED')
    return min(phase['phase_started_monotonic'] + budget,
               phase['total_deadline_monotonic'], *phase['additional_deadlines_monotonic'])


def receipt_identity(outer, role, action, budget):
    return {'schema': SCHEMA, 'outer_identity': outer.identity,
            'role_phase': role['phase'], 'guard_run': role['run'],
            'configuration_sha256': role['configuration_sha256'],
            'action': action, 'budget_seconds': budget}


def restore_result(outer, adapter, role, action, receipt):
    """Read original completed results; this never calls a mutation callback."""
    if action == 'quiesce' and adapter.open_partial['created_namespace'] is not None:
        value = outer._cleanup_receipt(role)
        require(value == receipt['result'], 'SUP16_ACTION_CLEANUP_RECEIPT_CHANGED')
        adapter.open_partial['cleanup'] = value
    elif action == 'archive':
        value = outer._archive_receipt(Path(role['outer_root']) / 'outer-archive-receipt.json',
                                       Path(role['archive']))
        require(value == receipt['result'], 'SUP16_ACTION_ARCHIVE_RECEIPT_CHANGED')
        adapter.open_partial['archive'] = value
    else:
        require(receipt['result'] is None, 'SUP16_ACTION_NO_INVENTED_RESULT')


def finish_actions(outer, adapter, original):
    """Replace only the old grouped cleanup boundary; preserve the actions."""
    error = outer._leave(original)
    close_failed = outer.last_boundary_failed
    if outer.blocked:
        require(error is not None, 'SUP16_ACTION_NONTERMINAL_ERROR_REQUIRED')
        error.sup16_open_partial = adapter.open_partial
        raise error
    role = adapter.cfg
    context = outer.contexts[role['phase']]
    require(context is adapter.ops, 'SUP16_ACTION_SAME_ROLE_CONTEXT_REQUIRED')
    parent = Path(role['outer_root'])
    callbacks = {'preserve': adapter._preserve, 'quiesce': context.quiesce,
                 'archive': context.archive}
    for action, budget in ACTIONS:
        if outer.blocked:
            close_failed = True
            error = first_error(error, ValueError('SUP16_ACTION_NONTERMINAL_NO_NEXT_MUTATION'),
                                'SUP16_ACTION_BLOCKED')
            break
        expected = receipt_identity(outer, role, action, budget)
        intent_path = parent / ('outer-action-' + action + '-intent.json')
        receipt_path = parent / ('outer-action-' + action + '-receipt.json')
        current_error = None
        try:
            if receipt_path.exists():
                try:
                    receipt = outer._read_json(receipt_path)
                    require(all(receipt.get(k) == v for k, v in expected.items())
                            and receipt.get('status') == 'COMPLETED'
                            and receipt.get('terminal_verified') is True
                            and receipt.get('product_result_claimed') is False,
                            'SUP16_ACTION_EXACT_COMPLETED_RECEIPT_REQUIRED')
                    restore_result(outer, adapter, role, action, receipt)
                except BaseException:
                    outer.blocked = True
                    raise
                continue
            # A completed quiesce alone does not skip preserve or archive.
            # An uncertain prior attempt never reissues its resource actions.
            if intent_path.exists():
                outer.blocked = True
                raise ValueError('SUP16_ACTION_UNCERTAIN_NO_RESUBMISSION')
            outer._assert_mutable()
            outer._put(intent_path, {**expected, 'status': 'INTENT',
                                     'terminal_verified': False,
                                     'product_result_claimed': False})
            phase = outer._enter(role, action)
            deadline = phase_deadline(phase, action, budget)
            role['cleanup_phase'] = phase
            context.deadline = deadline
            # The original cleanup state binds a fresh action-specific mode,
            # output, original system Python proof and remaining CLI budget.
            context.cleanup_context()
            require(context.mode == action and context.deadline == deadline
                    and context.phase_context['name'] == action
                    and context.phase_context['active_command_deadline'] == deadline,
                    'SUP16_ACTION_ORIGINAL_STATE_DEADLINE_REQUIRED')
            value = callbacks[action]()
            result = None
            if action == 'quiesce' and adapter.open_partial['created_namespace'] is not None:
                result = outer._cleanup_receipt(role)
                adapter.open_partial['cleanup'] = result
            elif action == 'archive':
                adapter.open_partial['archive'] = value
                result = outer._archive_receipt(parent / 'outer-archive-receipt.json',
                                                Path(role['archive']))
                require(value == result, 'SUP16_ACTION_ORIGINAL_ARCHIVE_RETURN_REQUIRED')
            current_error = outer._leave(None)
            if current_error is not None:
                raise current_error
            # Only after original guard exit AND real terminal verification.
            outer._put(receipt_path, {**expected, 'status': 'COMPLETED',
                'phase_started_monotonic': phase['phase_started_monotonic'],
                'phase_serial': phase['serial'], 'effective_deadline': deadline,
                'terminal_verified': True, 'product_result_claimed': False,
                'result': result})
        except BaseException as later:
            current_error = later
            # _enter already leaves on an entry failure; do not reset its
            # failure flags by leaving the same phase a second time.
            if outer.active is not None:
                current_error = outer._leave(current_error)
            close_failed = True
            error = first_error(error, current_error, 'SUP16_ROLE_CLOSE_ERROR:' + action)
    if close_failed:
        require(error is not None, 'SUP16_ACTION_CLOSE_ERROR_REQUIRED')
        error.sup16_open_partial = adapter.open_partial
        raise error
    return adapter.open_partial['cleanup']


def actionwise_outer_class(base_module, *, outer_raw, adapter_raw):
    require(hashlib.sha256(outer_raw).hexdigest() == OUTER_SHA
            and hashlib.sha256(adapter_raw).hexdigest() == ADAPTER_SHA,
            'SUP16_ACTION_EXACT_PREPARATION_SOURCES_REQUIRED')
    class ActionwiseOuter(base_module.OuterLifecycle):
        def _finish_adapter(self, adapter, original_finish, original):
            # original_finish is the old grouped implementation; its three
            # action bodies are reused above with separate original phases.
            return finish_actions(self, adapter, original)
    return ActionwiseOuter
