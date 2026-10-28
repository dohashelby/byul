from .events import emit


class Endpoint:
    def __init__(self, name, required_tasks=None):
        self.name = name
        self.required_tasks = required_tasks
        emit(name, 'endpoint_factory')

    def attach_router(self, app):
        emit(self.name, 'attach_router')
        name = self.name
        async def probe():
            emit(name, 'endpoint_handler')
            return {'ok': True}
        app.add_api_route('/byul-cpu-' + name, probe, methods=['GET'])

    async def init_state(self, engine_client, state, args):
        emit(self.name, 'init_state')


class Processor:
    plugin_name = 'unset'
    def __init__(self, config, renderer):
        emit(self.plugin_name, 'io_constructor')
    def pre_process(self, prompt, **kwargs):
        emit(self.plugin_name, 'io_preprocess')
        return prompt
    def post_process(self, output, **kwargs):
        emit(self.plugin_name, 'io_postprocess')
        return output
