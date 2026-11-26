from pathlib import Path

CLIENT_DIRS = {
    'antigravity': Path('agents'),
    'empryo': Path('harnesses/empryo'),
}
ANTIGRAVITY_METADATA = {
    'subagent': True,
    'mainAgent': False,
    'commandExecutionPolicy': 'sandbox',
}
EMPRYO_TOOL_NAMES = {
    'read': 'read',
    'write': 'edit_file',
    'edit': 'edit_file',
    'grep': 'grep',
    'glob': 'find',
    'bash': 'shell',
}
EMPRYO_MODES = {
    'worker': 'code',
    'tool-runner': 'code',
    'reviewer': 'explore',
    'style-reviewer': 'explore',
    'plan-reviewer': 'explore',
    'spec-reviewer': 'explore',
}


def agent_name(role):
    return f'code-flow-{role}'


def form_filename(role, client):
    if client == 'antigravity':
        return f'{role}.md'
    if client == 'empryo':
        return f'{agent_name(role)}.md'
    raise ValueError(f'unknown native client: {client}')


def form_dir(package_root, client):
    if client not in CLIENT_DIRS:
        raise ValueError(f'unknown native client: {client}')
    return Path(package_root) / CLIENT_DIRS[client]


def form_path(package_root, client, role):
    return form_dir(package_root, client) / form_filename(role, client)


def expected_forms(package_root, client, roles):
    return [form_path(package_root, client, role) for role in sorted(roles)]


def role_capabilities(profiles):
    capabilities = {}
    for role in sorted(profiles.roles):
        role_sets = {}
        for harness, config in profiles.harnesses.items():
            role_config = config['roles'][role]
            if 'tools' not in role_config:
                raise ValueError(f'{harness}.{role}: missing tools')
            role_sets[harness] = list(role_config['tools'])
        canonical = next(iter(role_sets.values()))
        for harness, tools in role_sets.items():
            if tools != canonical:
                raise ValueError(f'{role}: capability mismatch in {harness}: {tools} != {canonical}')
        capabilities[role] = canonical
    return capabilities


def antigravity_fields(profile):
    model = 'inherit' if profile['model'] == 'runtime-default' else profile['model']
    fields = [
        ('name', profile['name']),
        ('description', profile['description']),
        ('model', model),
        *ANTIGRAVITY_METADATA.items(),
    ]
    if 'tools' in profile:
        fields.append(('tools', list(profile['tools'])))
    return fields


def empryo_tools(role_name, capabilities):
    translated = []
    for capability in capabilities:
        if capability not in EMPRYO_TOOL_NAMES:
            raise ValueError(f'{role_name}: unknown empryo capability: {capability}')
        translated.append(EMPRYO_TOOL_NAMES[capability])
    return list(dict.fromkeys(translated))


def empryo_mode(role_name):
    if role_name not in EMPRYO_MODES:
        raise ValueError(f'unknown empryo role: {role_name}')
    return EMPRYO_MODES[role_name]


def empryo_fields(role, capabilities):
    return [
        ('name', agent_name(role['name'])),
        ('description', role['description']),
        ('model', 'inherit'),
        ('mode', empryo_mode(role['name'])),
        ('tools', empryo_tools(role['name'], capabilities)),
    ]


def is_managed_form(path, client):
    if path.suffix != '.md':
        return False
    if client == 'antigravity':
        return True
    return path.name.startswith('code-flow-')
