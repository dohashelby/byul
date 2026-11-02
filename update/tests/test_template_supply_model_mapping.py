"""EXAMPLE alias contracts for SUP03's existing real template collector."""
import json

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.executors.template_validation import TemplateValidationExecutor
from guard.models import canonical_json
from tests.builders import environment
from tests.test_template_supply_approval import SupplyWorld, definition
from tests.test_template_validation import IMAGE, target


@pytest.mark.parametrize('command_alias', ['served-alias', 'different-alias'])
@pytest.mark.parametrize('reported', ['served-alias', 'repository/model', 'unrelated', None])
def test_supply_real_uses_validated_alias_and_preserves_exact_command_and_response_checks(
        tmp_path, command_alias, reported):
    env = environment(engine={'name': 'vllm', 'version': '0.31.0', 'image_digest': IMAGE},
                      model={'id': 'repository/model', 'revision': 'a' * 40, 'inventory_sha256': 'b' * 64})
    before = canonical_json(env.to_dict())
    class AliasWorld(SupplyWorld):
        def inspect_json(self, container, *, timeout):
            row = super().inspect_json(container, timeout=timeout)
            row['Config']['Cmd'] += ['--served-model-name', command_alias]
            return row

        def probe(self, network, requests, timeout):
            rows = super().probe(network, requests, timeout)
            for row in rows:
                body = json.loads(row['body'])
                if 'model' in body:
                    if reported is None:
                        body.pop('model')
                    else:
                        body['model'] = reported
                    row['body'] = json.dumps(body)
                    if 'body_bytes' in row:
                        row['body_bytes'] = len(row['body'].encode())
            return rows
    world = AliasWorld()
    data = target(world.context)
    launch = data['comparison_config']['launch']
    launch.update(model=env.model.id, argv=[*launch['argv'], '--served-model-name', command_alias])
    data['model_mapping'] = {'schema': 'guard.model-request-mapping/1', 'environment_ref': env.ref().model_dump(mode='json'),
        'repository_id': env.model.id, 'revision': env.model.revision, 'served_model_name': 'served-alias',
        'host_snapshot': '/srv/models/' + env.model.revision, 'container_mount': '/models',
        'inventory_sha256': env.model.inventory_sha256}
    path = tmp_path / 'target.json'; path.write_text(json.dumps(data), encoding='utf-8')
    draft = TemplateValidationExecutor({'target': str(path), 'request_timeout': 1},
        docker=world, container_probe=world.probe).run(definition(), env,
        {'allowed_template_sha256': []}, VllmAdapter(), tmp_path / 'work')
    valid_command = command_alias == 'served-alias'
    assert len(world.calls) == (2 if valid_command else 0)
    assert draft.normal_control.succeeded is (valid_command and reported == 'served-alias')
    assert all(row['body']['model'] == 'served-alias' for row in world.calls)
    if valid_command:
        assert draft.observations['selected_template_approved'].value is False
    assert draft.observations['template_consumer_verified'].value is False
    assert draft.observations['supply_scope_complete'].value is False
    assert draft.data_origin.value == 'EXAMPLE'
    assert canonical_json(env.to_dict()) == before
