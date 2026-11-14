"""One schema source for constrained generation and local trust-boundary validation."""
SCHEMA_VERSION = 'steward-schema-3'


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def string(maximum=200000):
    return {'type': 'string', 'maxLength': maximum}


def array(items, minimum=0, maximum=100):
    return {'type': 'array', 'items': items, 'minItems': minimum, 'maxItems': maximum}


TEXT = string()
PATH = {'type': 'string', 'minLength': 1, 'maxLength': 1000}
REFERENCE = obj({'path': PATH, 'start_line': {'type': 'integer', 'minimum': 1},
                 'end_line': {'type': 'integer', 'minimum': 1}})
OPTION = obj({key: {'type': 'string', 'minLength': 1, 'maxLength': 4000} for key in ('id', 'label', 'tradeoff')})
DECISION = obj({'decision': obj({'title': PATH, 'options': array(OPTION, 2, 5), 'recommendation': PATH})})
CONTEXT = obj({'request_context': {'type': 'string', 'minLength': 1, 'maxLength': 1000}})
EDIT = obj({'edits': array(obj({'path': PATH, 'content': TEXT}), 1, 20), 'explanation': TEXT})
OPERATION = obj({'op': {'type': 'string', 'enum': ['create', 'update', 'delete', 'rename']},
                 'path': PATH, 'expected_hash': {'type': ['string', 'null'], 'maxLength': 64},
                 'destination': {'type': ['string', 'null'], 'maxLength': 1000},
                 'content': {'type': ['string', 'null'], 'maxLength': 200000},
                 'replacements': array(obj({'old': TEXT, 'new': TEXT}), 0, 100),
                 'complete_file': {'type': 'boolean'}})
def operation_schema(op, *, whole_file=False):
    properties = dict(OPERATION['properties'])
    properties.update(op={'type': 'string', 'enum': [op]},
                      expected_hash={'type': 'null'} if op == 'create' else {'type': 'string', 'minLength': 64, 'maxLength': 64},
                      destination=PATH if op == 'rename' else {'type': 'null'},
                      content=TEXT if op == 'create' or whole_file else {'type': 'null'},
                      replacements=array(obj({'old': {'type': 'string', 'minLength': 1, 'maxLength': 200000}, 'new': TEXT}),
                                         1 if op == 'update' and not whole_file else 0,
                                         100 if op == 'update' and not whole_file else 0),
                      complete_file={'type': 'boolean', 'enum': [whole_file]})
    return obj(properties)


OPERATIONS = obj({'edits': array({'anyOf': [operation_schema('create'), operation_schema('update'), operation_schema('update', whole_file=True), operation_schema('delete'), operation_schema('rename')]}, 1, 20), 'explanation': TEXT})
ACCEPTANCE_TESTS = obj({**OPERATIONS['properties'], 'coverage': array(obj({'criterion_id':PATH,'path':PATH,'test':PATH}),1,20)})
FINDING = obj({'message': {'type': 'string', 'minLength': 1, 'maxLength': 8000},
               'reference': REFERENCE})
REVIEW = obj({'approved': {'type': 'boolean'}, 'findings': array(FINDING)})
LEGACY_REVIEW = obj({'approved': {'type': 'boolean'}, 'findings': array({'type': 'string', 'minLength': 1})})
QA = obj({'answer': TEXT, 'citations': array(REFERENCE)})
INVESTIGATION = obj({'summary': TEXT, 'evidence': array(REFERENCE), 'missing': array(string(1000))})
PLAN = obj({'steps': array(obj({'description': TEXT, 'paths': array(PATH)}), 1, 20),
            'acceptance': array(TEXT, 1, 20)})


def test_operations(payload):
    """Bind UPDATE preconditions together; CREATE still passes controller path guards."""
    branches = [operation_schema('create')]
    paths = set()
    for file in payload.get('files', []):
        path, digest = file.get('path'), file.get('hash')
        check(path, PATH)
        if path in paths or type(digest) is not str or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
            raise ValueError('Invalid immutable test-file evidence')
        paths.add(path)
        for whole_file in (False, True) if file.get('complete') is True else (False,):
            branch = operation_schema('update', whole_file=whole_file)
            branch['properties'].update(path={'type': 'string', 'enum': [path]},
                                        expected_hash={'type': 'string', 'enum': [digest]})
            branches.append(branch)
    return obj({'edits': array({'anyOf': branches}, 1, 20), 'explanation': TEXT})


def acceptance_ids(payload):
    ids = [item['id'] for item in payload.get('acceptance_criteria', [])]
    if not 1 <= len(ids) <= 20:
        raise ValueError('Acceptance criteria must be bounded and nonempty')
    for criterion in ids:
        check(criterion, PATH)
    if len(set(ids)) != len(ids):
        raise ValueError('Acceptance criterion IDs must be unique')
    return ids


def schema_for(task, legacy=False, *, payload=None):
    if not legacy and payload is not None and task in ('acceptance', 'regression'):
        operations = test_operations(payload)
        if task == 'regression':
            return {'anyOf': [operations, DECISION, CONTEXT]}
        ids = acceptance_ids(payload)
        return obj({**operations['properties'], 'coverage': array(obj({
            'criterion_id': {'type': 'string', 'enum': ids}, 'path': PATH, 'test': PATH}), len(ids), len(ids))})
    if task=='acceptance':
        return ACCEPTANCE_TESTS
    if task == 'review':
        return LEGACY_REVIEW if legacy else REVIEW
    if task == 'qa':
        return QA
    if task in ('investigate', 'investigation', 'summary', 'triage'):
        return INVESTIGATION
    if task in ('plan', 'planning'):
        return {'anyOf': [PLAN, DECISION, CONTEXT]}
    if task in ('implementation', 'edit', 'repair', 'regression'):
        return {'anyOf': [EDIT if legacy else OPERATIONS, DECISION, CONTEXT]}
    raise ValueError('Unsupported model task')


def check(value, schema):
    """Validate the small JSON Schema vocabulary emitted above, without a new dependency."""
    if 'anyOf' in schema:
        for candidate in schema['anyOf']:
            try:
                check(value, candidate)
                return
            except ValueError:
                pass
        raise ValueError('Invalid model schema alternative')
    kind = schema['type']
    if isinstance(kind, list):
        if value is None and 'null' in kind:
            return
        return check(value, {**schema, 'type': next(item for item in kind if item != 'null')})
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Invalid model enum')
    if kind == 'object':
        if type(value) is not dict or set(value) != set(schema['required']):
            raise ValueError('Invalid model object fields')
        for key, child in schema['properties'].items():
            check(value[key], child)
    elif kind == 'array':
        if type(value) is not list or not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 100):
            raise ValueError('Invalid model array')
        for child in value:
            check(child, schema['items'])
    elif kind == 'string':
        if type(value) is not str or not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 200000):
            raise ValueError('Invalid model string')
    elif kind == 'boolean':
        if type(value) is not bool:
            raise ValueError('Invalid model boolean')
    elif kind == 'integer':
        if type(value) is not int or value < schema.get('minimum', 0):
            raise ValueError('Invalid model integer')
    elif kind == 'null':
        if value is not None:
            raise ValueError('Invalid model null')
    else:
        raise ValueError('Unsupported schema type')


def validate(value, task='implementation', legacy=False, *, payload=None):
    check(value, schema_for(task, legacy, payload=payload))
    if task == 'acceptance' and not legacy and payload is not None:
        ids = [item['criterion_id'] for item in value['coverage']]
        if len(set(ids)) != len(ids) or set(ids) != set(acceptance_ids(payload)):
            raise ValueError('Every acceptance criterion needs exactly one test reference')
    if task == 'review' and bool(value['findings']) == value['approved']:
        raise ValueError('Review approval contradicts findings')
    if task == 'qa':
        if any(item['end_line'] < item['start_line'] for item in value['citations']):
            raise ValueError('Invalid citation range')
    if task == 'review' and not legacy:
        if any(item['reference']['end_line'] < item['reference']['start_line'] for item in value['findings']):
            raise ValueError('Invalid finding range')
    if 'decision' in value:
        decision = value['decision']
        ids = [item['id'] for item in decision['options']]
        if len(ids) != len(set(ids)) or decision['recommendation'] not in ids:
            raise ValueError('Decision IDs or recommendation invalid')
